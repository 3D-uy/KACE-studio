import json
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.workflow_events import KaceWorkflowEventParser


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "=== KACE_WORKFLOW_EVENT: "


def marker(workflow_id="flow-a", sequence=1, state="BACKUP", detail=""):
    event = {
        "schema": 1,
        "workflow_id": workflow_id,
        "sequence": sequence,
        "state": state,
        "detail": detail,
    }
    return f"{PREFIX}{json.dumps(event)} ===\n", event


def test_fragmented_event_is_reassembled_from_ssh_chunks():
    seen = []
    parser = KaceWorkflowEventParser(seen.append)
    line, event = marker(state="MCU_ABSENT")

    midpoint = len(line) // 2
    assert parser.feed(line[:midpoint]) == []
    assert parser.feed(line[midpoint:]) == [event]
    assert seen == [event]


def test_repeated_and_out_of_order_sequences_are_ignored_per_workflow():
    parser = KaceWorkflowEventParser()
    line_2, event_2 = marker(sequence=2, state="MCU_PRESENT")
    line_1, _ = marker(sequence=1, state="MCU_ABSENT")

    assert parser.feed(line_2) == [event_2]
    assert parser.feed(line_2) == []
    assert parser.feed(line_1) == []
    assert parser.workflows["flow-a"].sequence == 2


@pytest.mark.parametrize("bad_line", [
    "=== KACE_WORKFLOW_EVENT: not-json ===\n",
    "=== KACE_WORKFLOW_EVENT: {} ===\n",
    '=== KACE_WORKFLOW_EVENT: {"schema":3,"workflow_id":"x","sequence":1,"state":"DONE"} ===\n',
    '=== KACE_WORKFLOW_EVENT: {"schema":2,"workflow_kind":"other","workflow_id":"x","sequence":1,"state":"DONE"} ===\n',
    '=== KACE_WORKFLOW_EVENT: {"workflow_id":"x","sequence":true,"state":"DONE"} ===\n',
    '=== KACE_WORKFLOW_EVENT: {"workflow_id":"","sequence":1,"state":"DONE"} ===\n',
    '=== KACE_WORKFLOW_EVENT: {"workflow_id":"x","sequence":0,"state":"DONE"} ===\n',
])
def test_malformed_events_are_ignored(bad_line):
    parser = KaceWorkflowEventParser()
    assert parser.feed(bad_line) == []
    assert parser.workflows == {}


def test_multiple_workflows_have_independent_sequence_cursors():
    parser = KaceWorkflowEventParser()
    a, event_a = marker("flow-a", 5, "FIRMWARE_VERIFIED")
    b, event_b = marker("flow-b", 1, "BACKUP")

    assert parser.feed(a + b) == [event_a, event_b]
    assert parser.workflows["flow-a"].sequence == 5
    assert parser.workflows["flow-b"].sequence == 1


def test_normal_terminal_output_does_not_clear_last_state():
    parser = KaceWorkflowEventParser()
    line, event = marker(sequence=7, state="KLIPPER_READY")
    parser.feed(line)

    assert parser.feed("Klipper log output\r\n$ prompt\n") == []
    assert parser.workflows["flow-a"].event == event


def test_done_is_delivered_as_the_only_success_state():
    parser = KaceWorkflowEventParser()
    line, event = marker(sequence=20, state="DONE", detail="deployment validated")

    assert parser.feed(line) == [event]
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "const isDone = view.state === 'DONE'" in app_js
    assert "view.state === 'FLASHED'" not in app_js


@pytest.mark.parametrize("state", [
    "ABORTED",
    "FAILED_FLASH",
    "FAILED_MONITOR",
    "FAILED_UPLOAD",
    "CONFIG_ERROR",
    "FAILED_PRECONDITION",
])
def test_terminal_error_states_are_preserved_and_rendered(state):
    parser = KaceWorkflowEventParser()
    line, event = marker(sequence=9, state=state, detail="terminal failure")

    assert parser.feed(line) == [event]
    assert parser.workflows["flow-a"].event["state"] == state
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert f"'{state}'" in app_js


def test_open_moonraker_port_is_not_used_as_installation_success():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    connect_ssh_source = main_source.split("def connect_ssh", 1)[1].split("def send_ssh_input", 1)[0]

    assert "probe_ip_ports" not in connect_ssh_source
    assert 'set_device_state("BOOTSTRAPPED"' not in connect_ssh_source
    assert 'set_device_state("SSH_READY"' in connect_ssh_source
    close_source = connect_ssh_source.split("def on_close", 1)[1]
    assert 'set_device_state("BOOTSTRAPPED"' not in close_source
    assert '"DONE"' not in close_source


def test_legacy_sessions_without_kace_events_remain_supported():
    parser = KaceWorkflowEventParser()
    legacy = "=== STAGE: KLIPPER ===\nBootstrap complete! KACE Node is fully ready.\n"

    assert parser.feed(legacy) == []
    assert parser.workflows == {}
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "parseBootstrapProgress(data)" in app_js


def test_firmware_deployment_events_preserve_method_artifact_and_instructions():
    parser = KaceWorkflowEventParser()
    event = {
        "schema": 2,
        "workflow_kind": "firmware_deployment",
        "workflow_id": "firmware-flow",
        "sequence": 3,
        "state": "ARTIFACT_READY",
        "detail": "firmware.bin ready",
        "data": {
            "method": "MANUAL",
            "final_filename": "firmware.bin",
            "staged_path": "/home/kace/kace/deploy/id/firmware.bin",
            "instructions": [{"id": "copy", "text": "Copy firmware.bin"}],
        },
    }
    line = f"{PREFIX}{json.dumps(event)} ===\n"

    assert parser.feed(line) == [event]
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "firmware_deployment" in app_js
    assert "Final filename:" in app_js
    assert "KACE_FIRMWARE_DEPLOYMENT_STEPS" in app_js
    assert "downloadKaceFirmwareArtifact" in app_js


def test_mcu_identity_confirmation_states_are_observationally_rendered():
    parser = KaceWorkflowEventParser()
    event = {
        "schema": 1,
        "workflow_id": "install-flow",
        "sequence": 12,
        "state": "AWAITING_MCU_CONFIRMATION",
        "detail": "physical MCU identity requires explicit confirmation",
        "data": {
            "identity_assessment": {
                "verdict": "AMBIGUOUS",
                "score": 80,
                "automatic_threshold": 90,
                "reasons": ["reported serial changed"],
            },
            "manually_confirmed": False,
        },
    }
    line = f"{PREFIX}{json.dumps(event)} ===\n"

    assert parser.feed(line) == [event]
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "AWAITING_MCU_CONFIRMATION: [3, 4]" in app_js
    assert "MCU_IDENTITY_CONFIRMED: [4, 5]" in app_js
    assert "MCU identity evidence:" in app_js
    assert "physically confirmed by the operator" in app_js


def test_simulated_kace_transcripts_keep_terminal_semantics_observational():
    parser = KaceWorkflowEventParser()

    def encoded(workflow_id, sequence, state, *, schema=1, kind=None):
        event = {
            "schema": schema,
            "workflow_id": workflow_id,
            "sequence": sequence,
            "state": state,
            "detail": f"simulated {state.lower()}",
        }
        if kind is not None:
            event["workflow_kind"] = kind
        return f"{PREFIX}{json.dumps(event)} ===\n", event

    success_states = [
        "MONITOR_ARMED", "MCU_ABSENT", "MCU_PRESENT", "MOONRAKER_ONLINE",
        "KLIPPER_READY", "MCU_REGISTERED", "VERIFYING_FIRMWARE",
        "FIRMWARE_VERIFIED", "APPLYING_CONFIG", "DONE",
    ]
    transcript = "ordinary SSH output\n"
    expected_success = None
    for sequence, state in enumerate(success_states, 1):
        line, expected_success = encoded("install-success", sequence, state)
        transcript += line
    timeout_line, timeout_event = encoded("install-timeout", 4, "TIMEOUT")
    action_line, action_event = encoded(
        "prepare-only",
        3,
        "ACTION_REQUIRED",
        schema=2,
        kind="firmware_deployment",
    )
    transcript += timeout_line + action_line

    # Replay arbitrary SSH chunk boundaries, including a stale event after the
    # authoritative terminal transcript.
    accepted = []
    widths = (1, 7, 19, 3, 41)
    offset = 0
    index = 0
    while offset < len(transcript):
        width = widths[index % len(widths)]
        accepted.extend(parser.feed(transcript[offset:offset + width]))
        offset += width
        index += 1
    stale, _ = encoded("install-success", 2, "MCU_ABSENT")
    assert parser.feed(stale) == []

    assert parser.workflows["install-success"].event == expected_success
    assert parser.workflows["install-timeout"].event == timeout_event
    assert parser.workflows["prepare-only"].event == action_event
    assert accepted[-1] == action_event

    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "'TIMEOUT'" in app_js
    assert "tracker.classList.toggle('success', isDone);" in app_js
    assert "tracker.classList.toggle('action-required', isActionRequired);" in app_js
    assert "isDone || isActionRequired" not in app_js

    # Reconnect recovery remains a read-only SSH/SFTP projection of KACE's
    # persisted manifest; it cannot advance or rewrite the workflow.
    from main import Api

    persisted = {
        "schema": 1,
        "workflow_kind": "firmware_deployment",
        "workflow_id": "prepare-only",
        "sequence": 3,
        "state": "ACTION_REQUIRED",
        "deployment": {
            "deployment_id": "prepare-only",
            "final_filename": "klipper.uf2",
            "staged_path": "/home/kace/kace/deploy/prepare-only/klipper.uf2",
        },
    }

    class ReadOnlySsh:
        calls = []

        def read_text_file(self, path):
            self.calls.append(path)
            return json.dumps(persisted)

    api = Api()
    api._ssh = ReadOnlySsh()
    assert api.get_firmware_deployment_manifest() == persisted
    assert api._ssh.calls == ["kace/deployment-manifest.json"]


def test_manifest_reader_is_home_relative_and_size_limited():
    from backend.ssh_client import SSHSession

    class FakeSftp:
        closed = False

        def normalize(self, path):
            assert path == "."
            return "/home/kace"

        def stat(self, path):
            assert path == "/home/kace/kace/deployment-manifest.json"
            return SimpleNamespace(st_size=16)

        def open(self, path, mode):
            assert mode == "rb"
            return io.BytesIO(b'{"schema": 1}')

        def close(self):
            self.closed = True

    session = SSHSession()
    fake = FakeSftp()
    session.get_sftp = lambda: fake

    assert session.read_text_file("kace/deployment-manifest.json") == '{"schema": 1}'
    assert fake.closed is True
    assert session.read_text_file("../etc/passwd") is None


def test_api_exposes_only_valid_versioned_deployment_manifest():
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    method = main_source.split("def get_firmware_deployment_manifest", 1)[1]
    method = method.split("def get_preferences", 1)[0]

    assert 'read_text_file("kace/deployment-manifest.json")' in method
    assert 'manifest.get("schema") != 1' in method
    assert 'manifest.get("deployment")' in method
