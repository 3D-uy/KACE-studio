"""Trust handoff and session regressions. Disk/elevation calls are simulated."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from backend import imager, kace_writer, resources
from tests.test_drive_identity import snapshot


def image_file(tmp_path, content=b"A"):
    path = tmp_path / "image.img"
    path.write_bytes(content * 510 + b"\x55\xaa")
    path.with_suffix(".img.sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_changed_custom_image_cannot_be_reapproved_by_flash_drive(tmp_path, monkeypatch):
    path = image_file(tmp_path)
    approved = main.Api()._resolve_custom_image(str(path))
    path.write_bytes(b"B" * 510 + b"\x55\xaa")
    elevate = Mock(side_effect=AssertionError("No elevation allowed"))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=SimpleNamespace(ShellExecuteExW=elevate)), raising=False)
    ok, detail = imager.flash_drive(3, approved.path, drive_identity=snapshot(),
        expected_image_sha256=approved.sha256, expected_image_size=approved.size_bytes)
    assert not ok and "changed after approval" in detail
    elevate.assert_not_called()


@pytest.mark.parametrize("exit_code,foreign,success", [(0, False, True), (2, False, False), (0, True, False)])
def test_completion_requires_own_operation_and_successful_exit(tmp_path, monkeypatch, exit_code, foreign, success):
    monkeypatch.setattr(imager, "sys", SimpleNamespace(**{**vars(imager.sys), "platform": "win32"}))
    path = image_file(tmp_path)
    approved = main.Api()._resolve_custom_image(str(path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    contracts, statuses = [], []
    encode = json.dumps
    def capture(value, **kwargs):
        if isinstance(value, dict) and "disk_identity" in value:
            contracts.append(value)
        return encode(value, **kwargs)
    monkeypatch.setattr(imager.json, "dumps", capture)
    def launch(info):
        contract = contracts[-1]
        info._obj.hProcess = 123
        status = Path(kace_writer._expected_status_path(3, contract["operation_id"]))
        statuses.append(status)
        status.write_text(encode({"status": "success", "operation_id": "f" * 32 if foreign else contract["operation_id"]}))
        return True
    def exited(_handle, value):
        value._obj.value = exit_code
        return True
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
        shell32=SimpleNamespace(ShellExecuteExW=launch),
        kernel32=SimpleNamespace(GetExitCodeProcess=exited, CloseHandle=Mock())), raising=False)
    for _ in range(2):
        ok, _ = imager.flash_drive(3, approved.path, drive_identity=snapshot(),
            expected_image_sha256=approved.sha256, expected_image_size=approved.size_bytes)
        assert ok is success
    assert statuses[0] != statuses[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing contract")
def test_verified_source_handle_denies_later_external_writes(tmp_path):
    path = image_file(tmp_path)
    expected = path.read_bytes()
    with kace_writer._open_verified_image(str(path), len(expected), hashlib.sha256(expected).hexdigest()) as source:
        with pytest.raises(PermissionError):
            path.write_bytes(b"B" * 512)
        assert source.read() == expected


def test_injected_bootstrap_preserves_exact_release_bytes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / "release-contract.json").read_text())
    source = resources.resolve_bootstrap_source()
    destination = tmp_path / "bootstrap.sh"
    imager._copy_bootstrap_atomically(str(source), str(destination))
    assert destination.read_bytes() == source.read_bytes()
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == contract["kace"]["bootstrap_sha256"]


@pytest.mark.parametrize("method", ["get_firmware_deployment_manifest", "get_firmware_workflow_checkpoint"])
def test_reconnect_rejects_old_remote_recovery_response(method):
    api = main.Api()
    def delayed(*_):
        api._ssh_gen += 1
        return json.dumps({"schema": 1, "deployment": {}})
    api._ssh = Mock(read_text_file=delayed)
    assert getattr(api, method)() is None


def test_firmware_download_passes_originating_generation_and_rejects_stale_view():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    app = (Path(__file__).resolve().parents[1] / "web/app.js").read_text(encoding="utf-8")
    function = app[app.index("window.downloadKaceFirmwareArtifact ="):app.index("// Stage definitions")]
    harness = '''const assert = require('node:assert/strict');
const window = globalThis;
let firmwareGeneration = 9, sshConnected = true;
const button = {dataset: {remotePath:'kace/firmware.bin',generation:'9'}, disabled:false};
const document = {getElementById() {return button;}};
const calls=[];
window.pywebview={api:{download_file(...args) {calls.push(args);return Promise.resolve(true);}}};
'''
    scenario = '''
assert.equal(downloadKaceFirmwareArtifact(),true);
assert.deepEqual(calls,[['kace/firmware.bin',9]]);
firmwareGeneration=10;
assert.equal(downloadKaceFirmwareArtifact(),false);
firmwareGeneration=9;sshConnected=false;
assert.equal(downloadKaceFirmwareArtifact(),false);
assert.equal(calls.length,1);
'''
    result = subprocess.run([node, "-e", harness + function + scenario], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
