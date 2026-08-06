"""Mock-only tests for target disk identity and capacity safeguards."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from backend import imager, kace_writer


@pytest.fixture(autouse=True)
def windows_disk_api(monkeypatch):
    """Exercise the mocked Windows disk path on every CI platform."""
    monkeypatch.setattr(imager.sys, "platform", "win32")


def powershell_disk(**overrides):
    disk = {
        "Number": 3,
        "FriendlyName": "SanDisk USB Flash Drive",
        "Size": 32 * 1024**3,
        "BusType": "USB",
        "IsSystem": False,
        "IsBoot": False,
        "SerialNumber": "SERIAL-123",
        "UniqueId": "UNIQUE-123",
        "Path": r"\\?\usbstor#disk&ven_sandisk",
        "MediaType": "Unspecified",
    }
    disk.update(overrides)
    return disk


def snapshot(**overrides):
    identity = imager._normalize_disk_identity(powershell_disk())
    identity.update(overrides)
    return identity


def subprocess_result(payload):
    return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


def test_list_drives_keeps_full_identity_and_classifies_external_ssd(monkeypatch):
    disks = [
        powershell_disk(),
        powershell_disk(
            Number=4,
            FriendlyName="Samsung Portable SSD",
            Size=2 * 1024**4,
            SerialNumber="SSD-SERIAL",
            UniqueId="SSD-UNIQUE",
            Path=r"\\?\usbstor#disk&ven_samsung",
            MediaType="SSD",
        ),
        powershell_disk(Number=5, BusType="SATA", SerialNumber="SATA", UniqueId="SATA-ID", Path="SATA-PATH"),
        powershell_disk(Number=6, IsSystem=True, IsBoot=True, SerialNumber="SYS", UniqueId="SYS-ID", Path="SYS-PATH"),
    ]
    captured = []

    def fake_run(args, **_kwargs):
        captured.append(args[-1])
        return subprocess_result(disks)

    monkeypatch.setattr(imager.subprocess, "run", fake_run)
    drives = imager.list_drives()

    assert [drive["number"] for drive in drives] == [3, 4]
    assert drives[0]["high_risk"] is False
    assert drives[1]["high_risk"] is True
    for field in imager.DISK_IDENTITY_FIELDS:
        assert field in drives[0]
    for property_name in (
        "Number", "FriendlyName", "Size", "BusType", "IsSystem", "IsBoot",
        "SerialNumber", "UniqueId", "Path", "MediaType",
    ):
        assert property_name in captured[0]


@pytest.mark.parametrize("missing", ["SerialNumber", "UniqueId", "Path", "Size"])
def test_list_drives_rejects_incomplete_identity(monkeypatch, missing):
    disk = powershell_disk()
    disk.pop(missing)
    monkeypatch.setattr(imager.subprocess, "run", lambda *_a, **_k: subprocess_result(disk))
    assert imager.list_drives() == []


def test_list_drives_excludes_offline_disks(monkeypatch):
    disk = powershell_disk(IsOffline=True)
    monkeypatch.setattr(imager.subprocess, "run", lambda *_a, **_k: subprocess_result(disk))
    assert imager.list_drives() == []


@pytest.mark.parametrize("bus", ["SATA", "NVME", "ATA", "RAID"])
def test_internal_bus_types_are_rejected(monkeypatch, bus):
    monkeypatch.setattr(
        imager.subprocess, "run",
        lambda *_a, **_k: subprocess_result(powershell_disk(BusType=bus)),
    )
    assert imager.list_drives() == []


@pytest.mark.parametrize("flag", ["IsSystem", "IsBoot"])
def test_system_and_boot_disks_are_rejected(monkeypatch, flag):
    monkeypatch.setattr(
        imager.subprocess, "run",
        lambda *_a, **_k: subprocess_result(powershell_disk(**{flag: True})),
    )
    assert imager.list_drives() == []


def test_elevated_helper_accepts_matching_identity(monkeypatch):
    expected = snapshot()
    monkeypatch.setattr(kace_writer, "_query_disk_identity", lambda _number: dict(expected))
    assert kace_writer._validate_disk_identity(3, expected) == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("number", 4),
        ("friendly_name", "Different disk"),
        ("size_bytes", 64 * 1024**3),
        ("serial_number", "OTHER-SERIAL"),
        ("unique_id", "OTHER-UNIQUE"),
        ("path", r"\\?\different-path"),
    ],
)
def test_elevated_helper_rejects_reassigned_identity(monkeypatch, field, value):
    expected = snapshot()
    current = dict(expected)
    current[field] = value
    monkeypatch.setattr(kace_writer, "_query_disk_identity", lambda _number: current)
    assert kace_writer._validate_disk_identity(3, expected) is None


def test_elevated_helper_rejects_incomplete_expected_snapshot(monkeypatch):
    expected = snapshot()
    expected.pop("serial_number")
    current = snapshot()
    monkeypatch.setattr(kace_writer, "_query_disk_identity", lambda _number: current)
    assert kace_writer._validate_disk_identity(3, expected) is None


def test_image_capacity_is_checked_before_elevation(tmp_path):
    image = tmp_path / "large.img"
    image.write_bytes(b"X" * 1024)
    small_disk = snapshot(size_bytes=512)

    success, message = imager.flash_drive(3, str(image), drive_identity=small_disk)
    assert success is False
    assert "too large" in message.lower()


def test_helper_capacity_failure_never_opens_physical_drive(tmp_path, monkeypatch):
    image = tmp_path / "large.img"
    image.write_bytes(b"X" * 1024)
    expected = snapshot(size_bytes=512)
    status = tmp_path / ".kace" / "temp" / "kace_flash_3.json"
    status.parent.mkdir(parents=True)
    opened = False

    class ForbiddenWriter:
        def __init__(self, *_args, **_kwargs):
            nonlocal opened
            opened = True
            raise AssertionError("PhysicalDrive must not be opened")

    monkeypatch.setattr(kace_writer, "_query_disk_identity", lambda _number: dict(expected))
    monkeypatch.setattr(kace_writer, "Win32DiskWriter", ForbiddenWriter)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    write_contract = {
        "disk_identity": expected,
        "image_size": image.stat().st_size,
        "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(
        kace_writer.sys,
        "argv",
        ["kace_writer.py", "3", str(image), str(status), json.dumps(write_contract)],
    )

    with pytest.raises(SystemExit) as exit_info:
        kace_writer.main()
    assert exit_info.value.code == 1
    assert opened is False


def test_helper_rejects_changed_image_before_opening_physical_drive(tmp_path, monkeypatch):
    image = tmp_path / "image.img"
    image.write_bytes(b"original")
    expected = snapshot()
    status = tmp_path / ".kace" / "temp" / "kace_flash_3.json"
    status.parent.mkdir(parents=True)
    contract = {
        "disk_identity": expected,
        "image_size": image.stat().st_size,
        "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
    }
    image.write_bytes(b"modified")
    opened = False

    class ForbiddenWriter:
        def __init__(self, *_args, **_kwargs):
            nonlocal opened
            opened = True

    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(kace_writer, "Win32DiskWriter", ForbiddenWriter)
    monkeypatch.setattr(
        kace_writer.sys,
        "argv",
        ["kace_writer.py", "3", str(image), str(status), json.dumps(contract)],
    )
    with pytest.raises(SystemExit) as exit_info:
        kace_writer.main()
    assert exit_info.value.code == 1
    assert opened is False


def test_helper_rejects_arbitrary_status_path(tmp_path, monkeypatch):
    image = tmp_path / "image.img"
    image.write_bytes(b"image")
    contract = {
        "disk_identity": snapshot(),
        "image_size": image.stat().st_size,
        "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
    }
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        kace_writer.sys,
        "argv",
        ["kace_writer.py", "3", str(image), str(tmp_path / "arbitrary.json"), json.dumps(contract)],
    )
    with pytest.raises(SystemExit) as exit_info:
        kace_writer.main()
    assert exit_info.value.code == 1
    assert not (tmp_path / "arbitrary.json").exists()


def test_api_rejects_missing_identity_before_starting_thread(monkeypatch):
    api = main.Api()
    started = False

    def forbidden_start(*_args, **_kwargs):
        nonlocal started
        started = True

    monkeypatch.setattr(main.threading.Thread, "start", forbidden_start)
    result = api.start_flash(3, "image.img", "host", "", "", "pw", "mainsail")
    assert result is False
    assert started is False


def test_api_rejects_client_identity_that_differs_from_stored_snapshot():
    api = main.Api()
    stored = snapshot()
    api._drive_snapshots[3] = dict(stored)
    changed = dict(stored, serial_number="OTHER-SERIAL")
    result = api.start_flash(
        3, "image.img", "host", "", "", "pw", "mainsail",
        drive_identity=changed,
    )
    assert result is False


def test_api_requires_reinforced_confirmation_for_high_risk_disk():
    api = main.Api()
    high_risk = snapshot()
    high_risk["high_risk"] = True
    api._drive_snapshots[3] = dict(high_risk)
    result = api.start_flash(
        3, "image.img", "host", "", "", "pw", "mainsail",
        drive_identity=high_risk,
        high_risk_confirmed=False,
    )
    assert result is False


def test_api_rejects_concurrent_flash_operations(monkeypatch):
    api = main.Api()
    selected = snapshot()
    api._drive_snapshots[3] = dict(selected)

    class InertThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Thread", InertThread)
    first = api.start_flash(
        3, "image.img", "host", "", "", "pw", "mainsail",
        drive_identity=selected,
    )
    second = api.start_flash(
        3, "image.img", "host", "", "", "pw", "mainsail",
        drive_identity=selected,
    )
    assert first is True
    assert second is False


def test_frontend_passes_snapshot_and_requires_typed_high_risk_confirmation():
    source = (Path(main.__file__).resolve().parent / "web" / "app.js").read_text(encoding="utf-8")
    assert "driveIdentitySnapshots" in source
    assert "ERASE DRIVE" in source
    assert "driveIdentity" in source
    assert "passwordAuth,\n            driveIdentity" in source
    assert "highRiskConfirmed" in source
