"""The delivery manifest can restore delivery progress, never installation success."""

import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from main import Api


ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((ROOT / "tests/fixtures/deployment_manifests.json").read_text(encoding="utf-8"))


def manifest():
    return {
        "schema": 1, "workflow_kind": "firmware_deployment",
        "workflow_id": "delivery", "sequence": 3, "state": "ARTIFACT_READY",
        "deployment": {
            "deployment_id": "delivery", "method": "MANUAL",
            "final_filename": "firmware.bin",
            "staged_path": "/home/kace/kace/deploy/delivery/firmware.bin",
            "instructions": [{"id": "copy", "text": "Copy the prepared firmware"}],
        },
    }


def read_manifest(value):
    api = Api()
    api._ssh_gen = 7
    api._ssh = Mock(read_text_file=Mock(return_value=json.dumps(value)))
    result = api.get_firmware_deployment_manifest()
    api._ssh.read_text_file.assert_called_once_with("kace/deployment-manifest.json")
    return result


@pytest.mark.parametrize("state", ["COMPLETE", "DONE", "READY_TO_DEPLOY", "DEPLOYING", "UNKNOWN", "", None, []])
def test_manifest_cannot_claim_installation_state(state):
    value = manifest()
    value["state"] = state
    assert read_manifest(value) is None


@pytest.mark.parametrize(("path", "invalid"), [
    (("schema",), True), (("schema",), 1.0),
    (("workflow_kind",), "installation"), (("workflow_id",), " "),
    (("sequence",), True), (("sequence",), 0), (("sequence",), -1),
    (("sequence",), 2.5), (("sequence",), "3"),
    (("deployment",), []), (("deployment", "deployment_id"), "other"),
    (("deployment", "final_filename"), "../firmware.bin"),
    (("deployment", "final_filename"), "other.bin"),
    (("deployment", "staged_path"), "../firmware.bin"),
    (("deployment", "staged_path"), "/home/kace/../firmware.bin"),
    (("deployment", "staged_path"), "C:\\firmware.bin"),
    (("deployment", "staged_path"), "/home/kace/firmware.bin\0"),
    (("deployment", "instructions"), ["copy"]),
    (("deployment", "instructions"), [{"text": []}]),
    (("deployment", "method"), []),
    (("deployment", "automation"), {"eligible": "true"}),
    (("result",), {"status": "FLASHED", "detail": "ready"}),
])
def test_inconsistent_manifest_is_rejected_without_projecting_a_default(path, invalid):
    value = manifest()
    target = value
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] = invalid
    assert read_manifest(value) is None


@pytest.mark.parametrize("field", ["schema", "workflow_kind", "workflow_id", "sequence", "state"])
def test_missing_manifest_envelope_is_not_inferred_from_flags(field):
    value = manifest()
    del value[field]
    value["deployment"]["automation"] = {"supported": True, "eligible": True, "blockers": []}
    assert read_manifest(value) is None


@pytest.mark.parametrize("field", ["deployment_id", "final_filename", "staged_path"])
def test_missing_download_identity_is_rejected(field):
    value = manifest()
    del value["deployment"][field]
    assert read_manifest(value) is None


@pytest.mark.parametrize("case", [
    "manifest_never_completes_installation",
    "manifest_positive_states_and_authority",
    "manifest_discard_and_reconnect",
    "manifest_checkpoint_api_evidence",
])
def test_production_manifest_frontend(case, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the production frontend regression")
    args = [
        node, str(ROOT / "tests/bootstrap_session_harness.cjs"),
        str(ROOT / "web/app.js"), case,
    ]
    if case == "manifest_checkpoint_api_evidence":
        from test_firmware_checkpoint import checkpoint, resigned

        complete = checkpoint("COMPLETE")
        invalid = checkpoint("COMPLETE")
        invalid["artifact"] = None
        resigned(invalid)  # Correct checksum must not excuse missing evidence.
        api = Api()
        api._ssh_gen = 7
        api._ssh = Mock(read_text_file=Mock(return_value=json.dumps(complete)))
        valid_result = api.get_firmware_workflow_checkpoint()
        api._ssh.read_text_file.return_value = json.dumps(invalid)
        invalid_result = api.get_firmware_workflow_checkpoint()
        invalid_manifest = {**manifest(), "state": "COMPLETE"}
        payload = {"complete": valid_result, "invalidCheckpoint": invalid_result,
                   "invalidManifest": read_manifest(invalid_manifest)}
        source = tmp_path / "api-projections.json"
        source.write_text(json.dumps(payload), encoding="utf-8")
        args.append(str(source))
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("completed")


def test_manifest_from_superseded_session_is_discarded():
    api = Api()
    api._ssh_gen = 7

    def read(_path):
        api._ssh_gen = 8
        return json.dumps(manifest())

    api._ssh = Mock(read_text_file=Mock(side_effect=read))
    assert api.get_firmware_deployment_manifest() is None


def test_invalid_manifest_does_not_replace_valid_durable_checkpoint():
    from test_firmware_checkpoint import checkpoint

    value = manifest()
    value["state"] = "COMPLETE"
    valid = checkpoint("COMPLETE")
    api = Api()
    api._ssh = Mock(read_text_file=Mock(side_effect=lambda path: json.dumps(
        value if path.endswith("deployment-manifest.json") else valid
    )))
    assert api.get_firmware_deployment_manifest() is None
    assert api.get_firmware_workflow_checkpoint()["checkpoint"] == valid


def test_valid_manifest_is_observational_and_preserves_optional_legacy_fields():
    value = manifest()
    value["state"] = "ACTION_REQUIRED"
    # v1 presentation never required newer firmware identity, profile or
    # transformation records, nor physical access to the recorded Pi paths.
    assert read_manifest(value) == {**value, "generation": 7}


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["case"])
def test_accepts_current_and_original_producer_serializations(case):
    value = case["manifest"]
    assert read_manifest(value) == {**value, "generation": 7}
