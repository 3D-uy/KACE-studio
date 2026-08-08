import json

import pytest

from backend.bootstrap_events import BootstrapEventParser
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
    assert "curl" not in api._ssh.commands[0]

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
