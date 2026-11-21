"""The real SFTP staging/publish code, with synthetic bytes and session state."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.ssh_client import SSHSession
from main import Api
from test_deployment_manifest import manifest
from test_firmware_checkpoint import CORPUS, checkpoint, resigned


@pytest.mark.parametrize("robin", [False, True])
@pytest.mark.parametrize("fault", ["none", "same-size", "truncated", "changed-workflow", "reconnected", "wrong-name"])
def test_transfer_is_bound_to_current_final_artifact(tmp_path, robin, fault):
    session = SSHSession()
    session.is_connected = Mock(return_value=True)
    data = b"reviewed synthetic firmware"
    digest = hashlib.sha256(data).hexdigest()
    value = deepcopy(CORPUS["cases"][-1]["checkpoint"]) if robin else checkpoint()
    value["state"] = "AWAITING_FLASH"
    artifact = value["artifact"]
    artifact.update(sha256=digest, size_bytes=len(data))
    build = artifact["build"]
    build["size_bytes"] = len(data)
    native = digest
    if robin:
        native = hashlib.sha256(b"native synthetic firmware").hexdigest()
        artifact["transformation"].update(final_sha256=digest, native_sha256=native, size_bytes=len(data))
    build["sha256"] = native
    if build.get("firmware_identity"):
        build["firmware_identity"].update(artifact_sha256=native, artifact_size=len(data))
    resigned(value)
    session.read_text_file_result = Mock(side_effect=lambda _: ("ok", json.dumps(value)))
    api = Api()
    api._ssh = session
    api._ssh_gen = 12
    api._window = Mock()
    destination = tmp_path / artifact["final_filename"]
    destination.write_bytes(b"previous")

    def choose(*_, save_filename):
        assert save_filename == artifact["final_filename"]
        if fault == "reconnected":
            api._ssh_gen += 1
        return str(tmp_path / "different.bin") if fault == "wrong-name" else str(destination)

    def transfer(remote, temporary):
        assert remote == artifact["path"]
        payload = {"same-size": b"X" * len(data), "truncated": data[:-1]}.get(fault, data)
        Path(temporary).write_bytes(payload)
        if fault == "changed-workflow":
            value["sequence"] += 1
            resigned(value)

    api._window.create_file_dialog.side_effect = choose
    sftp = Mock()
    sftp.get.side_effect = transfer
    session.get_sftp = Mock(return_value=sftp)
    assert api.download_kace_firmware(value["workflow_id"], value["sequence"], 12, artifact["path"]) is (fault == "none")
    api._window.create_file_dialog.assert_called_once()
    assert destination.read_bytes() == (data if fault == "none" else b"previous")
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("checkpoint_status,expected", [("absent", True), ("error", False), ("ok", False)])
def test_legacy_manifest_requires_absent_checkpoint_and_consistent_digest(checkpoint_status, expected):
    saved = manifest()
    deployment = saved["deployment"]
    deployment.update(staged_sha256="a"*64, artifact={"sha256": "a"*64, "size_bytes": 16})
    session = Mock()
    session.read_text_file_result.return_value = (checkpoint_status, "invalid")
    session.read_text_file.side_effect = lambda _: json.dumps(saved)
    api = Api()
    proof = api._firmware_download_artifact(session, saved["workflow_id"], saved["sequence"])
    assert bool(proof) is expected
    deployment["staged_sha256"] = "b"*64
    assert api._firmware_download_artifact(session, saved["workflow_id"], saved["sequence"]) is None
