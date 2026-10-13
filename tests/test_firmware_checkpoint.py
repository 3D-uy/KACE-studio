import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from backend.firmware_workflow import checkpoint_event, parse_checkpoint
from main import Api


CORPUS = json.loads((Path(__file__).parent / "fixtures/firmware_checkpoints.json").read_text())


def resigned(value):
    value.pop("integrity_sha256", None)
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    value["integrity_sha256"] = hashlib.sha256(canonical).hexdigest()
    return value


def checkpoint(state="AWAITING_FLASH"):
    return deepcopy(next(case["checkpoint"] for case in CORPUS["cases"]
                         if case["case"] == "current-" + state))


def test_valid_checkpoint_is_projected_as_recoverable_workflow():
    value = checkpoint()
    parsed = parse_checkpoint(json.dumps(value))
    event = checkpoint_event(parsed)

    assert parsed == value
    assert event["state"] == "AWAITING_FLASH"
    assert event["data"]["final_filename"] == "firmware.bin"
    assert event["data"]["strategy"] == value["artifact"]["strategy"]


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["case"])
def test_accepts_producer_wire_checkpoints_without_accessing_pi_artifacts(case, monkeypatch):
    # A completed historical installation remains observable after its staged
    # file has gone away. No desktop filesystem or receipt revalidation occurs.
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Studio must not inspect Pi filesystem paths")
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("os.stat", forbidden)
    parsed = parse_checkpoint(json.dumps(case["checkpoint"]))
    assert parsed == case["checkpoint"]
    assert checkpoint_event(parsed)["state"] == case["checkpoint"]["state"]


# These wire corruptions lie in the shared structural domain. The optional
# producer comparison test below checks that current KACE rejects them too.
SHARED_CORRUPTIONS = [
    ("artifact", None),
    ("artifact", {}),
    ("artifact.path", ""),
    ("artifact.sha256", "not-a-hash"),
    ("artifact.build", {}),
    ("artifact.build.mcu", ""),
    ("artifact.build.sha256", "b" * 64),
    ("artifact.build.size_bytes", 999),
    ("artifact.build.firmware_identity.artifact_sha256", "b" * 64),
    ("artifact.build.firmware_identity.artifact_size", 999),
    ("hardware.board", "other-board.cfg"),
    ("wizard_data.board", "other-board.cfg"),
    ("hardware.mcu", ""),
    ("hardware", []),
    ("wizard_data", []),
    ("sequence", True),
    ("sequence", 0),
    ("state", "UNKNOWN"),
    ("schema", "kace-firmware-workflow/v2"),
]


def changed_checkpoint(path, value):
    changed = checkpoint("COMPLETE")
    target = changed
    *parents, key = path.split(".")
    for part in parents:
        target = target[part]
    target[key] = value
    return resigned(changed)


@pytest.mark.parametrize("state", ["MCU_VERIFIED", "CONFIG_GENERATED", "READY_TO_DEPLOY", "DEPLOYING", "COMPLETE"])
@pytest.mark.parametrize("field,invalid", [
    ("verified_serial_path", None), ("verified_serial_path", ""),
    ("verified_serial_path", "  "), ("verified_serial_path", False),
    ("verified_serial_path", ["/dev/serial/by-id/test"]),
    ("flash_evidence_recorded_at", None), ("flash_evidence_recorded_at", 0),
    ("flash_evidence_recorded_at", -1), ("flash_evidence_recorded_at", True),
    ("flash_evidence_recorded_at", "1"),
])
def test_post_verification_states_require_saved_evidence(state, field, invalid):
    value = checkpoint(state)
    if invalid is None:
        value["hardware"].pop(field)
    else:
        value["hardware"][field] = invalid
    assert parse_checkpoint(resigned(value)) is None


@pytest.mark.parametrize("state", ["HARDWARE_SELECTED", "COMPILE_REQUIRED", "ARTIFACT_READY", "AWAITING_FLASH", "VERIFYING_MCU"])
def test_early_states_allow_verification_still_pending(state):
    value = checkpoint(state)
    value["hardware"]["verified_serial_path"] = ""
    value["hardware"]["flash_evidence_recorded_at"] = 0
    assert parse_checkpoint(resigned(value)) is not None


@pytest.mark.parametrize("path,value", SHARED_CORRUPTIONS)
def test_structurally_inconsistent_complete_is_rejected_even_with_valid_checksum(path, value):
    assert parse_checkpoint(changed_checkpoint(path, value)) is None


@pytest.mark.parametrize("path,value", [
    ("state", []), ("workflow_id", []), ("hardware.board", ["board"]),
    ("hardware.mcu", 1769), ("created_at", True), ("updated_at", "yesterday"),
    ("last_error", {}), ("wizard_data.language", []),
    ("artifact.path", ["firmware.bin"]), ("artifact.final_filename", 123),
    ("artifact.size_bytes", True), ("artifact.size_bytes", 0),
    ("artifact.build", []), ("artifact.build.mcu", 1769),
    ("artifact.build.firmware_identity", []),
    ("artifact.instructions", "copy"), ("artifact.instructions", ["copy"]),
    ("artifact.instructions", [{"text": []}]),
])
def test_invalid_wire_types_fail_closed_without_exception(path, value):
    # Producer constructors emit typed JSON; validate_checkpoint historically
    # coerces some malformed values. This parser must also protect its UI shape.
    assert parse_checkpoint(changed_checkpoint(path, value)) is None


@pytest.mark.parametrize("field", ["path", "sha256", "size_bytes", "final_filename", "build"])
def test_missing_required_artifact_fields_cannot_complete(field):
    value = checkpoint("COMPLETE")
    del value["artifact"][field]
    assert parse_checkpoint(resigned(value)) is None


TRANSFORMATION_CORRUPTIONS = [
    ("final_path", "/another/image.bin"), ("final_sha256", "b" * 64),
    ("final_filename", "wrong.bin"), ("size_bytes", 999),
    ("native_path", "/another/native.bin"), ("native_sha256", "b" * 64),
]


@pytest.mark.parametrize("field,value", TRANSFORMATION_CORRUPTIONS)
def test_saved_transformation_references_must_match_artifact_and_build(field, value):
    checkpoint = deepcopy(next(case["checkpoint"] for case in CORPUS["cases"]
                               if case["case"] == "producer-robin-transformation-COMPLETE"))
    checkpoint["artifact"]["transformation"][field] = value
    assert parse_checkpoint(resigned(checkpoint)) is None


def test_api_rejects_inconsistent_complete_and_preserves_remote_state():
    invalid = changed_checkpoint("artifact", None)
    calls = []
    class ReadOnlySsh:
        def read_text_file(self, path):
            calls.append(path)
            return json.dumps(invalid)
    api = Api()
    api._ssh = ReadOnlySsh()
    assert api.get_firmware_workflow_checkpoint() is None
    assert calls == ["kace/firmware-workflow.json"]
    assert invalid["state"] == "COMPLETE"


def test_api_discards_valid_complete_from_superseded_session():
    api = Api()
    class ReplacedSsh:
        def read_text_file(self, _path):
            api._ssh_gen += 1
            return json.dumps(checkpoint("COMPLETE"))
    api._ssh = ReplacedSsh()
    assert api.get_firmware_workflow_checkpoint() is None


def test_corrupt_checkpoint_and_secret_payload_are_rejected():
    damaged = checkpoint()
    damaged["state"] = "COMPLETE"
    assert parse_checkpoint(damaged) is None

    leaked = checkpoint()
    leaked["wizard_data"]["password"] = "secret"
    leaked.pop("integrity_sha256")
    canonical = json.dumps(
        leaked, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    leaked["integrity_sha256"] = hashlib.sha256(canonical).hexdigest()
    assert parse_checkpoint(leaked) is None


def test_api_reads_checkpoint_without_advancing_remote_state():
    value = checkpoint()

    class ReadOnlySsh:
        calls = []

        def read_text_file(self, path):
            self.calls.append(path)
            return json.dumps(value)

    api = Api()
    api._ssh = ReadOnlySsh()
    result = api.get_firmware_workflow_checkpoint()

    assert result["checkpoint"] == value
    assert result["event"]["state"] == "AWAITING_FLASH"
    assert api._ssh.calls == ["kace/firmware-workflow.json"]


def test_expected_restart_disconnect_is_recoverable_not_failed():
    class Window:
        scripts = []

        def evaluate_js(self, script):
            self.scripts.append(script)

    api = Api()
    api._window = Window()
    api._bootstrap_active = True
    api._bootstrap_workflow_id = "bootstrap-real"
    api._last_kace_workflow_state = "FIRMWARE_RESTART"

    assert api._suspend_bootstrap_for_ssh_loss(
        "expected restart", expected=True
    ) is True
    assert api._bootstrap_active is False
    assert api._bootstrap_recovery["expected"] is True
    assert api._bootstrap_recovery["kace_state"] == "FIRMWARE_RESTART"
    assert "updateBootstrapDisconnected" in api._window.scripts[-1]
    assert "updateBootstrapInterrupted" not in api._window.scripts[-1]


def test_unexpected_ssh_loss_is_distinct_but_still_recoverable():
    api = Api()
    api._bootstrap_active = True
    api._bootstrap_workflow_id = "bootstrap-real"
    api._last_kace_workflow_state = "ARTIFACT_READY"

    assert api._suspend_bootstrap_for_ssh_loss(
        "unexpected loss", expected=False
    ) is True
    assert api._bootstrap_recovery["expected"] is False
    assert api._bootstrap_recovery["reason"] == "unexpected loss"

