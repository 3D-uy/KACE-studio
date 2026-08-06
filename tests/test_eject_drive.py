import json
from types import SimpleNamespace

import main
from backend import ejector


def disk_identity(**overrides):
    identity = {
        "number": 3,
        "friendly_name": "SanDisk USB Device",
        "size_bytes": 32 * 1024**3,
        "bus_type": "USB",
        "is_system": False,
        "is_boot": False,
        "serial_number": "SERIAL-123",
        "unique_id": "UNIQUE-123",
        "path": r"\\?\usbstor#disk&ven_sandisk",
        "media_type": "Unspecified",
    }
    identity.update(overrides)
    return identity


def test_api_rejects_eject_without_stored_identity(monkeypatch):
    api = main.Api()
    called = False

    def forbidden_request(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(main, "request_safe_eject", forbidden_request)
    result = api.eject_drive(3)
    assert result["success"] is False
    assert "identity is unavailable" in result["error"]
    assert called is False


def test_api_passes_stored_identity_to_elevated_helper(monkeypatch):
    api = main.Api()
    api._drive_snapshots[3] = disk_identity()
    captured = {}

    def fake_request(disk_number, identity):
        captured["disk_number"] = disk_number
        captured["identity"] = identity
        return {"success": True, "method": "offline", "message": "Safe to remove."}

    monkeypatch.setattr(main, "request_safe_eject", fake_request)
    result = api.eject_drive(3)
    assert result["success"] is True
    assert captured == {"disk_number": 3, "identity": disk_identity()}


def test_api_propagates_verified_eject_failure(monkeypatch):
    api = main.Api()
    api._drive_snapshots[3] = disk_identity()
    monkeypatch.setattr(
        main,
        "request_safe_eject",
        lambda *_args: {"success": False, "error": "The volume is in use."},
    )
    result = api.eject_drive(3)
    assert result == {"success": False, "error": "The volume is in use."}


def test_privileged_eject_rejects_changed_disk_identity(monkeypatch, tmp_path):
    status_directory = tmp_path / ".kace" / "temp"
    status_directory.mkdir(parents=True)
    status_file = status_directory / "kace_eject_result.json"
    monkeypatch.setattr(ejector, "_status_directory", lambda: str(status_directory))
    monkeypatch.setattr(ejector, "_validate_eject_identity", lambda *_args: None)
    monkeypatch.setattr(
        ejector,
        "_perform_windows_eject",
        lambda *_args: (_ for _ in ()).throw(AssertionError("eject must not run")),
    )

    assert ejector.privileged_eject(3, str(status_file), disk_identity()) is False
    result = json.loads(status_file.read_text(encoding="utf-8"))
    assert result["success"] is False
    assert "identity changed" in result["error"]


def test_windows_eject_accepts_only_verified_success(monkeypatch):
    payload = {
        "success": True,
        "method": "offline",
        "message": "The disk is safe to remove.",
    }
    monkeypatch.setattr(
        ejector.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )
    assert ejector._perform_windows_eject(3) == payload


def test_windows_eject_rejects_permission_failure(monkeypatch):
    monkeypatch.setattr(
        ejector.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"success": False, "error": "Access denied"}),
            stderr="",
        ),
    )
    assert ejector._perform_windows_eject(3) == {
        "success": False,
        "error": "Access denied",
    }


def test_windows_eject_rejects_invalid_result(monkeypatch):
    monkeypatch.setattr(
        ejector.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="SUCCESS", stderr=""),
    )
    result = ejector._perform_windows_eject(3)
    assert result["success"] is False
    assert "invalid result" in result["error"]


def test_powershell_fallback_leaves_disk_offline_and_verifies_it():
    command = ejector._powershell_eject_command(3)
    assert "Set-Disk -Number $diskNumber -IsOffline $true" in command
    assert "-IsOffline $false" not in command
    assert "-not $current.IsOffline" in command
    assert "One or more volumes remain mounted" in command


def test_eject_identity_ignores_content_derived_unique_id(monkeypatch):
    current = disk_identity(unique_id="POST-FLASH-UNIQUE-ID")
    monkeypatch.setattr(ejector, "_query_disk_identity", lambda *_args: current)
    assert ejector._validate_eject_identity(3, disk_identity()) == current


def test_eject_identity_rejects_stable_hardware_change(monkeypatch):
    monkeypatch.setattr(
        ejector,
        "_query_disk_identity",
        lambda *_args: disk_identity(serial_number="OTHER-SERIAL"),
    )
    assert ejector._validate_eject_identity(3, disk_identity()) is None


def test_request_safe_eject_reads_failure_status_from_nonzero_helper(monkeypatch, tmp_path):
    status_directory = tmp_path / ".kace" / "temp"
    status_directory.mkdir(parents=True)
    monkeypatch.setattr(ejector.sys, "platform", "win32")
    monkeypatch.setattr(ejector, "_status_directory", lambda: str(status_directory))

    def fake_launch(arguments):
        status_file = arguments[2]
        with open(status_file, "w", encoding="utf-8") as output:
            json.dump({"success": False, "error": "The volume is in use."}, output)
        return True, ""

    monkeypatch.setattr(ejector, "_launch_elevated_helper", fake_launch)
    assert ejector.request_safe_eject(3, disk_identity()) == {
        "success": False,
        "error": "The volume is in use.",
    }
