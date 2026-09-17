"""Volume protection tests using mocked Win32 calls and storage providers."""

import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from backend import kace_writer
from tests.test_audit_stabilization import handle_reply
from tests.test_drive_identity import snapshot

IDENTITY = snapshot(number=99)


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
    monkeypatch.setattr(kace_writer, "_validate_disk_identity", lambda *_: IDENTITY)
    monkeypatch.setattr(kace_writer.Win32DiskWriter, "_query_handle",
                        lambda self, *args: handle_reply(IDENTITY)(*args))
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
    query.return_value.stdout = json.dumps(["E:\\"])
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
    query.return_value.stdout = json.dumps(["E:\\"]) + "\n" + json.dumps(["F:\\"]) if after_secured_volume else "F:\\\n"
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
        with kace_writer.Win32DiskWriter(PHYSICAL, 99) as writer:
            writer.verify_identity(IDENTITY)
    kernel.WriteFile.assert_not_called()
    acquired = ([101] if after_secured_volume else [])
    if failure not in {"open", "open_exception"}:
        acquired.append(102)
    if failure not in {"open", "open_exception"}:
        acquired.append(201)
    assert sorted(entry.args[0] for entry in kernel.CloseHandle.call_args_list) == acquired
    if failure in {"open", "open_exception"}:
        kernel.DeviceIoControl.assert_not_called()
    if failure == "lock":
        assert not any(entry.args[:2] == (102, DISMOUNT) for entry in kernel.DeviceIoControl.call_args_list)


def test_verified_handle_precedes_volume_dismount_and_mock_write(storage):
    kernel, query = storage
    query.return_value.stdout = json.dumps(["E:\\"]) + "\n" + json.dumps(["F:\\"])

    def write(_handle, _buffer, size, written, _overlapped):
        written._obj.value = size
        return True

    kernel.WriteFile.side_effect = write
    with kace_writer.Win32DiskWriter(PHYSICAL, 99) as writer:
        assert writer.lock_failed is False
        kernel.DeviceIoControl.assert_not_called()
        with pytest.raises(OSError, match="not been verified"):
            writer.write(b"x")
        writer.verify_identity(IDENTITY)
        assert writer.write(b"mock sector".ljust(512, b"\0")) == 512
        writer.flush()
    calls = kernel.mock_calls
    physical_index = next(i for i, entry in enumerate(calls) if entry[0] == "CreateFileW" and entry.args[0] == PHYSICAL)
    secured = [entry.args[:2] for entry in calls[physical_index:] if entry[0] == "DeviceIoControl"]
    for handle in (101, 102):
        assert [entry for entry in secured if entry[0] == handle] == [(handle, LOCK), (handle, DISMOUNT)]
    assert sorted(entry.args[0] for entry in kernel.CloseHandle.call_args_list) == [101, 102, 201]
    writer.close()
    assert kernel.CloseHandle.call_count == 3


def test_successful_read_only_fallback_still_requires_lock(storage):
    kernel, query = storage
    query.return_value.stdout = json.dumps(["E:\\"])
    kernel.CreateFileW.side_effect = [-1, 101, 201]
    with kace_writer.Win32DiskWriter(PHYSICAL, 99) as writer:
        writer.verify_identity(IDENTITY)
    assert [entry.args[1] for entry in kernel.CreateFileW.call_args_list] == [
        0xC0000000, 0x80000000, 0xC0000000,
    ]
    assert [entry.args[:2] for entry in kernel.DeviceIoControl.call_args_list] == [(101, LOCK), (101, DISMOUNT)]
    assert kernel.CloseHandle.call_args_list == [call(201), call(101)]


@pytest.mark.parametrize("raises", [False, True])
def test_physical_open_failure_releases_secured_volumes(storage, raises):
    kernel, query = storage
    query.return_value.stdout = json.dumps(["E:\\"])
    kernel.CreateFileW.side_effect = [101, OSError("physical open failed") if raises else -1]
    with pytest.raises(OSError):
        kace_writer.Win32DiskWriter(PHYSICAL, 99)
    kernel.CloseHandle.assert_called_once_with(101)
    kernel.DeviceIoControl.assert_not_called()
    kernel.WriteFile.assert_not_called()


def test_enumeration_preserves_normalized_paths_and_deduplicates(storage):
    _kernel, query = storage
    query.return_value.stdout = json.dumps(["E:\\", "E:\\", "\\\\?\\Volume{test}\\"])
    writer = kace_writer.Win32DiskWriter.__new__(kace_writer.Win32DiskWriter)
    assert writer._get_disk_volumes(99) == [r"\\?\Volume{test}"]
    command = query.call_args.args[0][-1]
    assert "-ErrorAction Stop" in command


def test_letter_and_guid_alias_lock_only_one_handle(storage):
    kernel, query = storage
    guid = r"\\?\Volume{test}"
    query.return_value.stdout = json.dumps(["E:\\", guid + "\\", "C:\\mount\\"])
    kernel.CreateFileW.side_effect = lambda path, *_: {guid: 101, PHYSICAL: 201}[path]
    locked = set()
    def ioctl(handle, code, *_):
        if code == LOCK:
            if locked:
                return False
            locked.add(handle)
        return True
    kernel.DeviceIoControl.side_effect = ioctl
    with kace_writer.Win32DiskWriter(PHYSICAL, 99) as writer:
        writer.verify_identity(IDENTITY)
    assert locked == {101}
    assert [c.args[0] for c in kernel.CreateFileW.call_args_list] == [guid, PHYSICAL]
    kernel.WriteFile.assert_not_called()


@pytest.mark.skipif(not shutil.which("powershell"), reason="PowerShell is unavailable")
@pytest.mark.parametrize("provider_body,expected", [
    ("return", []),
    ("[pscustomobject]@{ AccessPaths = @() }", []),
    ("[pscustomobject]@{ AccessPaths = $null }", []),
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
