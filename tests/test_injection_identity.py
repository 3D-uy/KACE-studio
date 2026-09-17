from backend.image_manifest import ResolvedImage
"""Injection identity regressions; storage is simulated, files stay in tmp_path."""

import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.usefixtures("finalized_release_contract")

import main
from backend import imager
from backend.provisioning import validate_provisioning


IDENTITY = {
    "number": 99, "friendly_name": "Test SD", "size_bytes": 32000000000,
    "bus_type": "USB", "is_system": False, "is_boot": False,
    "serial_number": "SERIAL-99", "unique_id": "UNIQUE-99", "path": "PHYSICAL-99",
}
VOLUME = "\\\\?\\Volume{12345678-1234-1234-1234-123456789abc}\\"


def binding(**overrides):
    result = {
        "Disk": dict(IDENTITY), "DiskNumber": 99, "PartitionNumber": 1,
        "VolumePath": VOLUME, "FileSystem": "FAT32",
    }
    result.update(overrides)
    return result


@pytest.fixture
def storage(monkeypatch, tmp_path):
    monkeypatch.setattr(imager.sys, "platform", "win32")
    # No storage command reaches PowerShell or Win32 in these tests.
    query = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(binding()), stderr=""))
    monkeypatch.setattr(imager.subprocess, "run", query)
    mount = Mock(return_value="E:\\")
    monkeypatch.setattr(imager, "get_boot_drive_letter", mount)
    exists = imager.os.path.exists
    monkeypatch.setattr(imager.os.path, "exists", lambda p: True if p == "E:\\" else exists(p))
    monkeypatch.setattr(imager.time, "sleep", lambda _delay: None)
    return query, mount


def inject(identity=IDENTITY):
    return imager.inject_config(
        99, "printer", "private-wifi", "private-password", "validpass123", "mainsail",
        drive_identity=identity,
    )


@pytest.mark.parametrize("field,value", [
    ("serial_number", "OTHER-SERIAL"), ("unique_id", "OTHER-UNIQUE"),
    ("path", "OTHER-PATH"), ("number", 100), ("size_bytes", 64000000000),
    ("friendly_name", "Other SD"), ("bus_type", "SATA"),
    ("is_system", True), ("is_boot", True),
])
def test_changed_physical_identity_never_writes(storage, monkeypatch, tmp_path, field, value):
    query, _mount = storage
    query.return_value.stdout = json.dumps(binding(Disk=dict(IDENTITY, **{field: value})))
    write = Mock(side_effect=AssertionError("No configuration or credentials may be written"))
    copy = Mock(side_effect=AssertionError("No bootstrap may be copied"))
    remove = Mock(side_effect=AssertionError("No existing file may be removed"))
    monkeypatch.setattr(imager, "_write_text_atomically", write)
    monkeypatch.setattr(imager, "_copy_bootstrap_atomically", copy)
    monkeypatch.setattr(imager.os, "remove", remove)
    assert inject() is False
    write.assert_not_called()
    copy.assert_not_called()
    remove.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("payload", [
    None, [], [binding(), binding()], {},
    binding(Disk={"number": 99}), binding(Disk=dict(IDENTITY, serial_number="")),
    binding(DiskNumber=100), binding(PartitionNumber=2),
    binding(VolumePath="E:\\"), binding(FileSystem="NTFS"),
])
def test_absent_incomplete_ambiguous_or_wrong_volume_aborts(storage, monkeypatch, payload):
    query, _mount = storage
    query.return_value.stdout = json.dumps(payload)
    write = Mock()
    monkeypatch.setattr(imager, "_write_text_atomically", write)
    assert inject() is False
    write.assert_not_called()


@pytest.mark.parametrize("identity", [None, {}, {"number": 99}, dict(IDENTITY, number=100)])
def test_missing_authorized_identity_cannot_fall_back_to_number(storage, monkeypatch, identity):
    _query, mount = storage
    write = Mock()
    monkeypatch.setattr(imager, "_write_text_atomically", write)
    assert inject(identity) is False
    mount.assert_not_called()
    write.assert_not_called()


@pytest.mark.parametrize("second", [
    None, binding(Disk=dict(IDENTITY, serial_number="REUSED-NUMBER")),
    binding(VolumePath="\\\\?\\Volume{87654321-1234-1234-1234-123456789abc}\\"),
])
def test_device_removed_or_replaced_after_mount_aborts_before_first_write(storage, monkeypatch, second):
    query, _mount = storage
    query.side_effect = [
        SimpleNamespace(returncode=0, stdout=json.dumps(binding()), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps(second), stderr=""),
    ]
    write = Mock()
    monkeypatch.setattr(imager, "_write_text_atomically", write)
    assert inject() is False
    write.assert_not_called()
    assert query.call_count == 2


@pytest.mark.parametrize("result", [
    SimpleNamespace(returncode=1, stdout="", stderr="disconnected"),
    SimpleNamespace(returncode=0, stdout=json.dumps(binding()), stderr="partial failure"),
    SimpleNamespace(returncode=0, stdout="invalid JSON", stderr=""),
    OSError("provider unavailable"),
])
def test_unverifiable_binding_never_writes(storage, monkeypatch, result):
    query, _mount = storage
    query.side_effect = [result]
    write = Mock()
    monkeypatch.setattr(imager, "_write_text_atomically", write)
    assert inject() is False
    write.assert_not_called()


def test_same_device_injects_via_verified_guid_into_temporary_directory(storage, monkeypatch, tmp_path):
    query, mount = storage
    join = imager.os.path.join
    # Translate only the fake GUID to a sandbox directory. No physical path can
    # be opened even if the implementation tries to write after verification.
    def sandbox_join(first, *rest):
        assert first != "E:\\", "Writes must not use the reusable drive letter"
        return join(str(tmp_path) if first == VOLUME else first, *rest)

    monkeypatch.setattr(imager.os.path, "join", sandbox_join)
    monkeypatch.setattr(imager, "hash_password", lambda _password: "$6$test-only")
    assert inject() is True
    assert (tmp_path / "userconf.txt").read_text().startswith("kace:$6$")
    assert (tmp_path / "kace-bootstrap.txt").exists()
    mount.assert_called_once_with(99, drive_identity=imager._normalize_disk_identity(IDENTITY))
    commands = [entry.args[0][-1] for entry in query.call_args_list]
    assert "Get-Volume -DriveLetter 'E'" in commands[0]
    assert "Get-Partition -Volume $volume" in commands[0]
    assert "Get-Disk -Partition $part" in commands[0]
    assert f"Get-Volume -Path '{VOLUME}'" in commands[1]


def test_changed_drive_letter_with_same_identity_is_allowed(storage):
    assert imager._verified_boot_volume("F:\\", IDENTITY) == VOLUME


def test_mount_revalidation_rejects_reused_disk_number(storage, monkeypatch):
    query, _mount = storage
    # Exercise the real mount resolver separately from the injected mock.
    query.return_value.stdout = json.dumps(dict(IDENTITY, serial_number="REPLACEMENT"))
    assert REAL_MOUNT(99, drive_identity=IDENTITY) is None
    assert query.call_count == 1
    assert "Set-Partition" not in query.call_args.args[0][-1]


REAL_MOUNT = imager.get_boot_drive_letter


def test_mount_retries_recheck_identity_before_another_mount_attempt(storage, monkeypatch):
    query, _mount = storage
    query.side_effect = [
        SimpleNamespace(returncode=0, stdout=json.dumps(IDENTITY), stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="not mounted yet"),
        SimpleNamespace(returncode=0, stdout=json.dumps(dict(IDENTITY, unique_id="REUSED")), stderr=""),
    ]
    assert REAL_MOUNT(99, drive_identity=IDENTITY) is None
    assert query.call_count == 3
    assert "Set-Partition" not in query.call_args.args[0][-1]


def test_same_identity_can_mount_with_new_letter(storage):
    query, _mount = storage
    query.side_effect = [
        SimpleNamespace(returncode=0, stdout=json.dumps(IDENTITY), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"DriveLetter": "E", "FileSystem": "FAT32"}), stderr=""),
    ]
    assert REAL_MOUNT(99, drive_identity=IDENTITY) == "E:\\"


def test_worker_transports_authorized_identity_to_both_stages(monkeypatch):
    api = main.Api()
    provisioning = validate_provisioning(
        image_type="raspios_vanilla", hostname="printer", image_path="default_lite",
        wifi_ssid="", wifi_password="", ssh_password="validpass123", dashboard_ui="mainsail",
    )
    monkeypatch.setattr(api, "_resolve_default_image", lambda _arch: ResolvedImage("fake.img", "a" * 64, 512))
    monkeypatch.setattr(api, "_validate_raw_image", lambda _path: 1024)
    flash = Mock(return_value=(True, ""))
    injection = Mock(return_value=True)
    monkeypatch.setattr(main, "flash_drive", flash)
    monkeypatch.setattr(main, "inject_config", injection)
    api._flash_worker(99, provisioning, dict(IDENTITY))
    assert flash.call_args.args[3] == IDENTITY
    assert injection.call_args.kwargs["drive_identity"] == IDENTITY


def test_authorized_snapshot_is_copied_before_starting_worker(monkeypatch):
    api = main.Api()
    snapshot = dict(IDENTITY)
    api._drive_snapshots[99] = snapshot
    thread = Mock()
    monkeypatch.setattr(main.threading, "Thread", thread)
    assert api.start_flash(
        99, "default_lite", "printer", "", "", "validpass123", "mainsail",
        drive_identity=dict(IDENTITY),
    ) is True
    identity = thread.call_args.kwargs["args"][2]
    snapshot["serial_number"] = "REPLACEMENT"
    assert identity["serial_number"] == IDENTITY["serial_number"]


@pytest.mark.skipif(not shutil.which("powershell"), reason="PowerShell is unavailable")
@pytest.mark.parametrize("failure", [None, "volume", "partition", "disk", "query"])
def test_volume_owner_query_with_simulated_powershell_providers(monkeypatch, failure):
    run = subprocess.run
    monkeypatch.setattr(imager.sys, "platform", "win32")

    def simulated_run(args, **kwargs):
        command = args[-1]
        disk = (
            "[pscustomobject]@{ Number=99; FriendlyName='Test SD'; Size=32000000000; "
            "BusType='USB'; IsSystem=$false; IsBoot=$false; SerialNumber='SERIAL-99'; "
            "UniqueId='UNIQUE-99'; Path='PHYSICAL-99' }"
        )
        bodies = {
            "Get-Volume": f"[pscustomobject]@{{ Path='{VOLUME}'; FileSystem='FAT32' }}",
            "Get-Partition": "[pscustomobject]@{ PartitionNumber=1; DiskNumber=99 }",
            "Get-Disk": disk,
        }
        if failure == "query":
            bodies["Get-Volume"] = "Write-Error 'storage unavailable'"
        elif failure:
            name = "Get-" + failure.title()
            bodies[name] += "; " + bodies[name]
        definitions = []
        for original, body in bodies.items():
            fake = original.replace("Get-", "Get-Fake")
            command = command.replace(original, fake)
            definitions.append(
                f"function {fake} {{ [CmdletBinding()] param($DriveLetter,$Path,$Volume,$Partition) {body} }}; "
            )
        assert all(name not in command for name in bodies)
        return run(args[:-1] + ["".join(definitions) + command], **kwargs)

    monkeypatch.setattr(imager.subprocess, "run", simulated_run)
    if failure:
        with pytest.raises(OSError):
            imager._verified_boot_volume("E:\\", IDENTITY)
    else:
        assert imager._verified_boot_volume("E:\\", IDENTITY) == VOLUME
