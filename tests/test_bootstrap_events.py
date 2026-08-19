import json

import pytest

from backend.bootstrap_events import BootstrapEventParser, MachineProtocolDisplayFilter
from main import Api


PREFIX = "=== KACE_BOOTSTRAP_EVENT: "


def marker(event="stage_started", sequence=1, workflow_id="bootstrap-a", **extra):
    payload = {
        "protocol": "kace-bootstrap/v1",
        "event": event,
        "workflow_id": workflow_id,
        "sequence": sequence,
        "stage": extra.pop("stage", "KLIPPER"),
        "code": extra.pop("code", ""),
        "exit_code": extra.pop("exit_code", 0),
        **extra,
    }
    return f"{PREFIX}{json.dumps(payload)} ===\n", payload


def test_fragmented_bootstrap_event_is_reassembled_and_forwarded():
    seen = []
    parser = BootstrapEventParser(seen.append)
    line, event = marker()
    split = len(line) // 2
    assert parser.feed(line[:split]) == []
    assert parser.feed(line[split:]) == [event]
    assert seen == [event]


def test_bootstrap_event_embedded_in_an_echoed_shell_command_is_not_authoritative():
    parser = BootstrapEventParser()
    line, _ = marker(event="workflow_failed", code="BOOTSTRAP_NOT_FOUND")
    echoed_command = f"kace@kace:~ $ if true; then :; else printf '%s' '{line.strip()}'; fi\r\n"
    assert parser.feed(echoed_command) == []


def test_duplicate_and_out_of_order_events_are_ignored_per_workflow():
    parser = BootstrapEventParser()
    newer, event = marker(sequence=3, stage="MOONRAKER")
    older, _ = marker(sequence=2, stage="KLIPPER")
    assert parser.feed(newer) == [event]
    assert parser.feed(newer + older) == []
    assert parser.workflows["bootstrap-a"].sequence == 3


@pytest.mark.parametrize("payload", [
    {},
    {"protocol": "wrong", "event": "workflow_started", "workflow_id": "x", "sequence": 1},
    {"protocol": "kace-bootstrap/v1", "event": "unknown", "workflow_id": "x", "sequence": 1},
    {"protocol": "kace-bootstrap/v1", "event": "workflow_started", "workflow_id": "", "sequence": 1},
    {"protocol": "kace-bootstrap/v1", "event": "workflow_started", "workflow_id": "x", "sequence": True},
    {"protocol": "kace-bootstrap/v1", "event": "stage_started", "workflow_id": "x", "sequence": 1, "stage": ""},
])
def test_malformed_or_unknown_events_are_ignored(payload):
    parser = BootstrapEventParser()
    assert parser.feed(f"{PREFIX}{json.dumps(payload)} ===\n") == []


def test_machine_protocol_filter_hides_fragmented_markers_but_preserves_prompts():
    display_filter = MachineProtocolDisplayFilter()
    line, _ = marker()
    result_marker = (
        '=== KACE_RESULT: {"protocol":"kace-outcome/v1",'
        '"outcome":"CANCELLED","exit_code":2} ===\r\n'
    )
    chunks = [
        "Apply configuration? [y/N]: ",
        "n\r\n" + line[:17],
        line[17:] + result_marker[:11],
        result_marker[11:] + "\u26a0 Installation cancelled\r\n",
    ]
    visible = "".join(display_filter.feed(chunk) for chunk in chunks)
    visible += display_filter.flush()
    assert "Apply configuration? [y/N]: n" in visible
    assert "\u26a0 Installation cancelled" in visible
    assert "KACE_BOOTSTRAP_EVENT" not in visible
    assert "KACE_RESULT" not in visible
    assert "workflow_id" not in visible


def test_machine_protocol_filter_hides_fragmented_studio_launch_echo():
    display_filter = MachineProtocolDisplayFilter()
    command = (
        "KACE_STUDIO_LAUNCH=1; if [ -f /boot/firmware/bootstrap.sh ]; then "
        "KACE_BOOTSTRAP_WORKFLOW_ID='bootstrap-secret' bash /boot/firmware/bootstrap.sh; "
        "else printf '%s' 'BOOTSTRAP_NOT_FOUND'; fi\r\n"
    )
    chunks = ["kace@kace:~ $ " + command[:9], command[9:41], command[41:] + "Starting KACE\r\n"]
    visible = "".join(display_filter.feed(chunk) for chunk in chunks)
    visible += display_filter.flush()
    assert visible == "kace@kace:~ $ \r\nStarting KACE\r\n"
    assert "bootstrap-secret" not in visible
    assert "BOOTSTRAP_NOT_FOUND" not in visible


def test_machine_protocol_filter_does_not_delay_non_marker_partial_lines():
    display_filter = MachineProtocolDisplayFilter()
    assert display_filter.feed("ordinary prompt without newline") == "ordinary prompt without newline"
    assert display_filter.feed("\n=== user-facing heading ===\n") == "\n=== user-facing heading ===\n"


def test_authoritative_events_hide_stage_and_error_fallback_markers():
    display_filter = MachineProtocolDisplayFilter()
    legacy = "=== STAGE: KLIPPER ===\n"
    assert display_filter.feed(legacy) == legacy

    display_filter.enable_authoritative_bootstrap()
    hidden = display_filter.feed(
        "=== STAGE: MOONRAKER ===\r\n"
        "=== KACE_BOOTSTRAP_ERROR: KACE_INSTALL ===\r\n"
        "Readable failure guidance.\r\n"
    )
    assert hidden == "Readable failure guidance.\r\n"


class FakeWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)


class FakeSsh:
    def __init__(self, send_ok=True):
        self.send_ok = send_ok
        self.commands = []

    def send_input(self, command):
        self.commands.append(command)
        return self.send_ok


def test_backend_guard_allows_only_one_active_bootstrap_and_reopens_on_terminal():
    api = Api()
    api._ssh = FakeSsh()
    api._window = FakeWindow()

    first = api.start_bootstrap("mainsail")
    second = api.start_bootstrap("fluidd")
    assert first["status"] == "started"
    assert second["status"] == "busy"
    assert len(api._ssh.commands) == 1
    assert first["workflow_id"] in api._ssh.commands[0]
    assert api._ssh.commands[0].startswith("KACE_STUDIO_LAUNCH=1;")
    assert api._ssh.commands[0].count("\n") == 1
    assert "KACE_BOOTSTRAP_EVENT_STREAM=1" in api._ssh.commands[0]
    assert "curl" not in api._ssh.commands[0]
    assert "kace.py" not in api._ssh.commands[0]
    assert "install.sh" not in api._ssh.commands[0]

    api._forward_bootstrap_event({
        "protocol": "kace-bootstrap/v1",
        "event": "workflow_failed",
        "workflow_id": first["workflow_id"],
        "sequence": 2,
        "stage": "KACE",
        "code": "DEPLOYMENT_FAILED",
        "exit_code": 40,
    }, api._ssh_gen)

    third = api.start_bootstrap("fluidd")
    assert third["status"] == "started"
    assert len(api._ssh.commands) == 2


def test_backend_ignores_events_from_an_unrelated_workflow():
    api = Api()
    api._ssh = FakeSsh()
    api._window = FakeWindow()
    started = api.start_bootstrap("mainsail")

    api._forward_bootstrap_event({
        "protocol": "kace-bootstrap/v1",
        "event": "workflow_succeeded",
        "workflow_id": "bootstrap-unrelated",
        "sequence": 1,
        "stage": "KACE",
        "code": "SUCCESS",
        "exit_code": 0,
    }, api._ssh_gen)

    assert api._bootstrap_active is True
    assert api._bootstrap_workflow_id == started["workflow_id"]
    assert not any("bootstrap-unrelated" in script for script in api._window.scripts)


def test_kace_cancellation_is_forwarded_as_terminal_and_releases_guard():
    api = Api()
    api._ssh = FakeSsh()
    api._window = FakeWindow()
    started = api.start_bootstrap("mainsail")

    api._forward_bootstrap_event({
        "protocol": "kace-bootstrap/v1",
        "event": "workflow_cancelled",
        "workflow_id": started["workflow_id"],
        "sequence": 2,
        "stage": "KACE",
        "code": "CANCELLED",
        "exit_code": 2,
    }, api._ssh_gen)

    assert api._bootstrap_active is False
    assert api._bootstrap_workflow_id is None
    assert any("workflow_cancelled" in script for script in api._window.scripts)
    assert api.start_bootstrap("mainsail")["status"] == "started"


def test_backend_rejects_invalid_dashboard_without_sending_input():
    api = Api()
    api._ssh = FakeSsh()
    result = api.start_bootstrap("mainsail; reboot")
    assert result["status"] == "failed"
    assert api._ssh.commands == []


def test_interruption_is_terminal_for_the_local_guard():
    api = Api()
    api._ssh = FakeSsh()
    api._window = FakeWindow()
    started = api.start_bootstrap("both")
    assert started["status"] == "started"
    assert api._interrupt_bootstrap("connection lost") is True
    assert api._interrupt_bootstrap("duplicate") is False
    assert "updateBootstrapInterrupted" in api._window.scripts[-1]
    assert api.start_bootstrap("both")["status"] == "started"
