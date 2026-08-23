import hashlib
import json

from backend.firmware_workflow import checkpoint_event, parse_checkpoint
from main import Api


def checkpoint(state="AWAITING_FLASH"):
    value = {
        "schema": "kace-firmware-workflow/v1",
        "workflow_id": "firmware-resume",
        "sequence": 4,
        "state": state,
        "created_at": 1,
        "updated_at": 2,
        "hardware": {
            "board": "generic-bigtreetech-skr-v1.4.cfg",
            "mcu": "lpc1769",
            "baseline_serial_path": "/dev/serial/by-id/old",
            "verified_serial_path": "",
        },
        "wizard_data": {"board": "generic-bigtreetech-skr-v1.4.cfg"},
        "artifact": {
            "path": "/home/kace/kace/firmware.bin",
            "final_filename": "firmware.bin",
            "sha256": "a" * 64,
            "size_bytes": 123,
            "method": "MANUAL",
            "strategy": "SD_CARD",
            "instructions": [{"id": "copy", "text": "Copy firmware.bin"}],
        },
        "last_error": "",
    }
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    value["integrity_sha256"] = hashlib.sha256(canonical).hexdigest()
    return value


def test_valid_checkpoint_is_projected_as_recoverable_workflow():
    value = checkpoint()
    parsed = parse_checkpoint(json.dumps(value))
    event = checkpoint_event(parsed)

    assert parsed == value
    assert event["state"] == "AWAITING_FLASH"
    assert event["data"]["final_filename"] == "firmware.bin"
    assert event["data"]["strategy"] == "SD_CARD"


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

