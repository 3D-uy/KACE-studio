"""Volume protection tests using mocked Win32 calls and storage providers."""

import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from backend import kace_writer


PHYSICAL = r"\\.\PhysicalDrive99"
VOLUME_A = r"\\.\E:"
VOLUME_B = r"\\.\F:"
LOCK = 0x00090018
DISMOUNT = 0x00090020


@pytest.fixture
def storage(monkeypatch):
    kernel = Mock()
    handles = {VOLUME_A: 101, VOLUME_B: 102, PHYSICAL: 201}
    kernel.CreateFileW.side_effect = lambda path, *_args: handles[path]
    kernel.DeviceIoControl.return_value = True
    kernel.CloseHandle.return_value = True
    kernel.GetLastError.return_value = 5
    # Replace the entire DLL accessor; no test can reach a real Win32 function.
    monkeypatch.setattr(kace_writer.ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    query = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(kace_writer.subprocess, "run", query)
    return kernel, query


def assert_no_physical_access(kernel):
    assert PHYSICAL not in [entry.args[0] for entry in kernel.CreateFileW.call_args_list]
    kernel.WriteFile.assert_not_called()


def test_successful_empty_enumeration_allows_disk_without_volumes(storage):
    kernel, query = storage
    with kace_writer.Win32DiskWriter(PHYSICAL, 99) as writer:
        assert writer.lock_failed is False
        assert writer.volume_handles == []
    query.assert_called_once()
    assert [entry.args[0] for entry in kernel.CreateFileW.call_args_list] == [PHYSICAL]
    kernel.DeviceIoControl.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(201)
    kernel.WriteFile.assert_not_called()


@pytest.mark.parametrize("returncode,stdout,stderr", [
    (1, "", "partition enumeration failed"),
    (1, "E:\\\n", "partial enumeration failed"),
    (0, "", "non-terminating inspection error"),
    (0, "E:\\\n", "partial inspection failed"),
])
def test_enumeration_errors_never_allow_physical_open(storage, returncode, stdout, stderr):
    kernel, query = storage
    query.return_value = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    with pytest.raises(OSError):
        kace_writer.Win32DiskWriter(PHYSICAL, 99)
    kernel.CreateFileW.assert_not_called()
    kernel.CloseHandle.assert_not_called()
    assert_no_physical_access(kernel)


@pytest.mark.parametrize("error", [OSError("query failed"), ValueError("cannot decode query")])
def test_enumeration_exceptions_are_not_empty_results(storage, error):
    kernel, query = storage
    query.side_effect = error
    with pytest.raises(type(error), match=str(error)):
        kace_writer.Win32DiskWriter(PHYSICAL, 99)
    assert_no_physical_access(kernel)


def test_missing_disk_number_cannot_skip_volume_protection(storage):
    kernel, query = storage
    with pytest.raises(TypeError, match="disk_number"):
        kace_writer.Win32DiskWriter(PHYSICAL)
    query.assert_not_called()
    assert_no_physical_access(kernel)


@pytest.mark.parametrize("invalid", [None, 0, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF])
def test_volume_open_failure_aborts_even_after_read_only_fallback(storage, invalid):
    kernel, query = storage
    query.return_value.stdout = "E:\\\n"
    kernel.CreateFileW.side_effect = lambda path, *_args: invalid if path == VOLUME_A else 201
    with pytest.raises(OSError, match="open volume"):
        kace_writer.Win32DiskWriter(PHYSICAL, 99)
    assert kernel.CreateFileW.call_count == 2
    kernel.CloseHandle.assert_not_called()
    assert_no_physical_access(kernel)


@pytest.mark.parametrize("failure", ["open", "lock", "dismount", "open_exception", "ioctl_exception", "dismount_exception"])
@pytest.mark.parametrize("after_secured_volume", [False, True])
def test_volume_failure_closes_all_acquired_handles(storage, monkeypatch, failure, after_secured_volume):
    kernel, query = storage
    query.return_value.stdout = "E:\\\nF:\\\n" if after_secured_volume else "F:\\\n"
    monkeypatch.setattr(
        kace_writer.Win32DiskWriter, "_get_disk_volumes",
        lambda _self, _number: [VOLUME_A, VOLUME_B] if after_secured_volume else [VOLUME_B],
    )

    def open_handle(path, *_args):
        if path == VOLUME_B:
            if failure == "open":
                return -1
            if failure == "open_exception":
                raise OSError("open volume exception")
        return {VOLUME_A: 101, VOLUME_B: 102, PHYSICAL: 201}[path]

    def ioctl(handle, code, *_args):
        if handle == 102:
            if failure == "ioctl_exception":
                raise OSError("inspection failed")
            if failure == "dismount_exception" and code == DISMOUNT:
                raise OSError("dismount failed after lock")
            if (failure, code) in {("lock", LOCK), ("dismount", DISMOUNT)}:
                return False
        return True

    kernel.CreateFileW.side_effect = open_handle
    kernel.DeviceIoControl.side_effect = ioctl
    with pytest.raises(OSError):
        kace_writer.Win32DiskWriter(PHYSICAL, 99)
    assert_no_physical_access(kernel)
    acquired = ([101] if after_secured_volume else [])
    if failure not in {"open", "open_exception"}:
        acquired.append(102)
    assert sorted(entry.args[0] for entry in kernel.CloseHandle.call_args_list) == acquired
    if after_secured_volume:
        calls = kernel.mock_calls
        first_locked = next(i for i, entry in enumerate(calls) if entry[0] == "DeviceIoControl" and entry.args[:2] == (101, LOCK))
        second_opened = next(i for i, entry in enumerate(calls) if entry[0] == "CreateFileW" and entry.args[0] == VOLUME_B)
        assert first_locked < second_opened
    if failure == "lock":
        assert not any(entry.args[:2] == (102, DISMOUNT) for entry in kernel.DeviceIoControl.call_args_list)


def test_all_volumes_are_secured_before_physical_open_and_mock_write(storage):
    kernel, query = storage
    query.return_value.stdout = "E:\\\nF:\\\n"

    def write(_handle, _buffer, size, written, _overlapped):
        written._obj.value = size
        return True

    kernel.WriteFile.side_effect = write
    with kace_writer.Win32DiskWriter(PHYSICAL, 99) as writer:
        assert writer.lock_failed is False
        assert writer.write(b"mock sector".ljust(512, b"\0")) == 512
        writer.flush()
    calls = kernel.mock_calls
    physical_index = next(i for i, entry in enumerate(calls) if entry[0] == "CreateFileW" and entry.args[0] == PHYSICAL)
    secured = [entry.args[:2] for entry in calls[:physical_index] if entry[0] == "DeviceIoControl"]
    for handle in (101, 102):
        assert [entry for entry in secured if entry[0] == handle] == [(handle, LOCK), (handle, DISMOUNT)]
    assert sorted(entry.args[0] for entry in kernel.CloseHandle.call_args_list) == [101, 102, 201]
    writer.close()
    assert kernel.CloseHandle.call_count == 3


def test_successful_read_only_fallback_still_requires_lock(storage):
    kernel, query = storage
    query.return_value.stdout = "E:\\\n"
    kernel.CreateFileW.side_effect = [-1, 101, 201]
    with kace_writer.Win32DiskWriter(PHYSICAL, 99):
        pass
    assert [entry.args[1] for entry in kernel.CreateFileW.call_args_list] == [
        0xC0000000, 0x80000000, 0xC0000000,
    ]
    assert [entry.args[:2] for entry in kernel.DeviceIoControl.call_args_list] == [(101, LOCK), (101, DISMOUNT)]
    assert kernel.CloseHandle.call_args_list == [call(201), call(101)]


@pytest.mark.parametrize("raises", [False, True])
def test_physical_open_failure_releases_secured_volumes(storage, raises):
    kernel, query = storage
    query.return_value.stdout = "E:\\\n"
    kernel.CreateFileW.side_effect = [101, OSError("physical open failed") if raises else -1]
    with pytest.raises(OSError):
        kace_writer.Win32DiskWriter(PHYSICAL, 99)
    kernel.CloseHandle.assert_called_once_with(101)
    kernel.WriteFile.assert_not_called()


def test_enumeration_preserves_normalized_paths_and_deduplicates(storage):
    _kernel, query = storage
    query.return_value.stdout = "E:\\\nE:\\\n\\\\?\\Volume{test}\\\n"
    writer = kace_writer.Win32DiskWriter.__new__(kace_writer.Win32DiskWriter)
    assert set(writer._get_disk_volumes(99)) == {VOLUME_A, r"\\?\Volume{test}"}
    command = query.call_args.args[0][-1]
    assert "-ErrorAction Stop" in command


@pytest.mark.skipif(not shutil.which("powershell"), reason="PowerShell is unavailable")
@pytest.mark.parametrize("provider_body,expected", [
    ("return", []),
    ("[pscustomobject]@{ AccessPaths = @() }", []),
    ("[pscustomobject]@{ AccessPaths = @('E:\\') }", [VOLUME_A]),
    ("Write-Error 'enumeration failed'", None),
    ("[pscustomobject]@{ MissingAccessPaths = 1 }", None),
    ("[pscustomobject]@{ AccessPaths = @('E:\\') }; Write-Error 'partial failure'", None),
])
def test_powershell_query_with_fake_partition_provider(monkeypatch, provider_body, expected):
    run = subprocess.run

    def fake_storage_query(args, **kwargs):
        # Run the query syntax, but replace the storage cmdlet with an in-memory
        # provider. Even a broken test cannot enumerate or change real disks.
        command = args[-1].replace("Get-CimInstance", "Get-FakePartition")
        assert "Get-CimInstance" not in command
        provider = (
            "function Get-FakePartition { [CmdletBinding()] "
            "param($Namespace, $ClassName, $Filter) " + provider_body + " }; "
        )
        return run(args[:-1] + [provider + command], **kwargs)

    monkeypatch.setattr(kace_writer.subprocess, "run", fake_storage_query)
    writer = kace_writer.Win32DiskWriter.__new__(kace_writer.Win32DiskWriter)
    if expected is None:
        with pytest.raises(OSError, match="enumerate volumes"):
            writer._get_disk_volumes(99)
    else:
        assert writer._get_disk_volumes(99) == expected
