"""Writer-to-UI completion evidence. All disks, processes and injection are fake."""
import ctypes
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from backend import imager, kace_writer
from backend.image_manifest import ResolvedImage
from backend.provisioning import validate_provisioning
from tests.test_drive_identity import snapshot


def evidence(operation_id, identity, digest, size, status="verified"):
    return {"schema": "kace-studio-readback/v1", "operation_id": operation_id,
            "disk_identity": dict(identity), "image_sha256": digest,
            "image_size": size, "status": status}


@pytest.mark.parametrize("verify,corrupt", [(True, False), (False, False), (True, True)])
def test_writer_reports_observed_readback_only_after_success(tmp_path, monkeypatch, verify, corrupt):
    image = tmp_path / "test.img"
    image.write_bytes(b"x" * 512)
    identity = snapshot(size_bytes=4096)
    operation = "a" * 32
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    status = tmp_path / f"kace_flash_3_{operation}.json"
    reads = []

    class Disk(io.BytesIO):
        lock_failed = False
        def __init__(self, *_):
            super().__init__()
        def verify_identity(self, actual):
            assert actual == identity
        def read(self, size):
            reads.append(size)
            result = super().read(size)
            return b"z" * len(result) if corrupt else result

    monkeypatch.setattr(kace_writer, "Win32DiskWriter", Disk)
    monkeypatch.setattr(kace_writer, "_expected_status_path", lambda *_: str(status))
    monkeypatch.setattr(kace_writer, "_validate_disk_identity", lambda *_: identity)
    monkeypatch.setattr(kace_writer.subprocess, "run", Mock())
    contract = {"operation_id": operation, "disk_identity": identity,
                "image_size": 512, "image_sha256": digest, "verify_write": verify}
    monkeypatch.setattr(kace_writer.sys, "argv", ["writer", "3", str(image), str(status), json.dumps(contract)])
    with pytest.raises(SystemExit) as result:
        kace_writer.main()
    observed = json.loads(status.read_text())
    assert result.value.code == (2 if corrupt else 0)
    assert bool(reads) is verify
    if corrupt:
        assert observed["status"] == "error"
        assert "readback" not in observed
    else:
        assert observed["readback"] == evidence(operation, identity, digest, 512,
                                                "verified" if verify else "skipped")


@pytest.mark.parametrize("mutation", [None, "skipped", "missing", "operation", "disk", "hash", "size", "status", "unexpected_skip", "unexpected_verified", "boolean_size", "schema", "disk_missing", "non_object"])
def test_imager_requires_matching_completion_evidence(tmp_path, monkeypatch, mutation):
    monkeypatch.setattr(imager, "sys", SimpleNamespace(**{**vars(imager.sys), "platform": "win32"}))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    image = tmp_path / "test.img"
    image.write_bytes(b"x" * 512)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    identity = snapshot()
    contracts = []
    encode = json.dumps
    def capture(value, **kwargs):
        if isinstance(value, dict) and "disk_identity" in value:
            contracts.append(value)
        return encode(value, **kwargs)
    monkeypatch.setattr(imager.json, "dumps", capture)
    def launch(info):
        contract = contracts[-1]
        info._obj.hProcess = 123
        result = evidence(contract["operation_id"], identity, digest, 512)
        if mutation == "operation": result["operation_id"] = "f" * 32
        if mutation == "disk": result["disk_identity"]["serial_number"] = "OTHER"
        if mutation == "hash": result["image_sha256"] = "f" * 64
        if mutation == "size": result["image_size"] = 513
        if mutation == "status": result["status"] = "unknown"
        if mutation == "unexpected_skip": result["status"] = "skipped"
        if mutation == "skipped": result["status"] = "skipped"
        if mutation == "boolean_size": result["image_size"] = True
        if mutation == "schema": result["schema"] = "unknown"
        if mutation == "disk_missing": result.pop("disk_identity")
        status = Path(kace_writer._expected_status_path(3, contract["operation_id"]))
        payload = {"status": "success", "operation_id": contract["operation_id"]}
        if mutation != "missing": payload["readback"] = result
        if mutation == "non_object": payload = []
        status.write_text(encode(payload))
        return True
    def exited(_handle, code):
        code._obj.value = 0
        return True
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
        shell32=SimpleNamespace(ShellExecuteExW=launch),
        kernel32=SimpleNamespace(GetExitCodeProcess=exited, CloseHandle=Mock())), raising=False)
    ok, detail = imager.flash_drive(3, str(image), drive_identity=identity,
                                  expected_image_sha256=digest, expected_image_size=512,
                                  verify_write=mutation not in {"skipped", "unexpected_verified"})
    assert ok is (mutation in {None, "skipped"})
    if ok:
        assert detail["status"] == ("skipped" if mutation == "skipped" else "verified")
    else:
        assert isinstance(detail, str) and detail


@pytest.mark.parametrize("scenario", ["verified", "skipped", "missing", "stale", "disk", "injection_failure", "cancelled", "superseded"])
def test_worker_only_completes_current_provisioned_operation(monkeypatch, scenario):
    api = main.Api()
    identity = snapshot()
    provisioning = validate_provisioning(
        image_type="raspios_vanilla", hostname="printer", image_path="default_lite",
        wifi_ssid="", wifi_password="", ssh_password="validpass123", dashboard_ui="mainsail",
        verify_write=scenario != "skipped")
    monkeypatch.setattr(api, "_resolve_default_image", lambda _: ResolvedImage("fake.img", "a" * 64, 512))
    monkeypatch.setattr(api, "_validate_raw_image", lambda _: 512)
    states = []
    monkeypatch.setattr(api, "set_device_state", lambda *args: states.append(args))
    def flash(*_args, **kwargs):
        result = evidence(kwargs.get("operation_id", "b" * 32), identity, "a" * 64, 512,
                          "skipped" if scenario == "skipped" else "verified")
        if scenario == "missing": return True, ""
        if scenario == "stale": result["operation_id"] = "f" * 32
        if scenario == "disk": result["disk_identity"]["serial_number"] = "OTHER"
        return True, result
    def inject(*_args, **_kwargs):
        if scenario == "cancelled": api._flash_cancel_event.set()
        if scenario == "superseded": api._flash_operation_id = "f" * 32
        return scenario != "injection_failure"
    injection = Mock(side_effect=inject)
    monkeypatch.setattr(main, "flash_drive", flash)
    monkeypatch.setattr(main, "inject_config", injection)
    api._flash_worker(3, provisioning, identity)
    completions = [event for event in states if event[0] == "FLASHED"]
    if scenario in {"verified", "skipped"}:
        assert len(completions) == 1
        assert completions[0][3]["status"] == scenario
        assert completions[0][3]["disk_identity"] == identity
        assert injection.call_count == 1
    else:
        assert not completions
        for event in states:
            if event[0] == "ERROR":
                assert event[3]["operation_id"] == api._flash_operation_id
                assert event[3]["disk_identity"] == identity
        if scenario in {"missing", "stale", "disk"}:
            injection.assert_not_called()
