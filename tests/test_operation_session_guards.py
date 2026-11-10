"""Session and media operation races with synthetic transports and disks only."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from tests.test_drive_identity import snapshot


class Session:
    def __init__(self):
        self.connected = False
        self.commands = []

    def connect(self, host, *_args):
        self.host = host
        self.connected = True
        return True

    def close(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    def read_text_file_result(self, *_args, **_kwargs):
        return "absent", None

    def run_command_stream(self, command, on_data, on_close, **kwargs):
        self.shell_command, self.shell_dimensions = command, kwargs
        self.on_data, self.on_close = on_data, on_close

    def send_input(self, command):
        self.commands.append(command)
        return self.connected

    def stop_preflight(self, path=None):
        return 1

    def start_preflight(self, validator, *, generation=None):
        return "/tmp/kace-studio-preflight-fixture.env"


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(main, "DockerPreflight", lambda: SimpleNamespace(prepare=lambda: None))
    monkeypatch.setattr(main, "SSHSession", Session)
    monkeypatch.setattr(main, "require_release_ready", lambda: None)
    monkeypatch.setattr(main, "load_release_contract", lambda: {"kace": {"bootstrap_sha256": "a" * 64}})
    result = main.Api()
    result.set_device_state = Mock()
    result._window = Mock()
    assert result.connect_ssh("A.local", "fixture", "synthetic")["status"] == "success"
    return result


def test_interactive_shell_opts_into_filtered_kace_events(api):
    session = api._ssh
    assert session.shell_command == "env KACE_MACHINE_OUTPUT=1 bash"
    assert session.shell_dimensions == {"cols": 80, "rows": 24}
    assert session.commands == []  # No extra terminal input or hardware command.
    event = {"schema": 2, "workflow_kind": "firmware_deployment",
             "workflow_id": "archived", "sequence": 8, "state": "DISCARDED"}
    marker = "=== KACE_WORKFLOW_EVENT: " + json.dumps(event) + " ===\n"
    api._window.evaluate_js.reset_mock()
    session.on_data(marker[:31])
    session.on_data(marker[31:])
    forwarded = [call.args[0] for call in api._window.evaluate_js.call_args_list]
    assert forwarded == [f"window.updateKaceWorkflowEvent({json.dumps(event)}, {api._ssh_gen});"]
    assert api._last_kace_workflow_state == "DISCARDED"


def test_bootstrap_cannot_cross_session_replaced_during_preflight(api, monkeypatch):
    origin = api._ssh
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    def preflight():
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(main, "require_release_ready", preflight)
    worker = threading.Thread(target=lambda: outcomes.append(api.start_bootstrap("mainsail")))
    worker.start()
    try:
        assert entered.wait(3)
        assert api.connect_ssh("B.local", "fixture", "synthetic")["status"] == "success"
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert outcomes[0]["status"] == "failed"
    assert origin.commands == []
    assert api._ssh.commands == []
    assert api._bootstrap_active is False


def test_bootstrap_rejects_request_from_previous_frontend_session(api):
    context = dict(api._power_context)
    assert api.connect_ssh("B.local", "fixture", "synthetic")["status"] == "success"
    assert api.start_bootstrap("mainsail", context)["status"] == "failed"
    assert api._ssh.commands == []
    assert api.start_bootstrap("mainsail", dict(api._power_context))["status"] == "started"


def test_bootstrap_exception_releases_guard_for_retry(api, monkeypatch):
    monkeypatch.setattr(api._ssh, "send_input", Mock(side_effect=OSError("synthetic send failure")))
    with pytest.raises(OSError, match="synthetic send failure"):
        api.start_bootstrap("mainsail", dict(api._power_context))
    assert api._bootstrap_active is False
    assert api._bootstrap_workflow_id is None
    monkeypatch.setattr(api._ssh, "send_input", Mock(return_value=True))
    assert api.start_bootstrap("mainsail", dict(api._power_context))["status"] == "started"


@pytest.mark.parametrize("close", [False, True])
def test_old_terminal_timer_and_close_discard_buffer_after_reconnect(api, monkeypatch, close):
    timers = []

    class Timer:
        def __init__(self, _delay, callback):
            self.callback = callback
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(main.threading, "Timer", Timer)
    origin = api._ssh
    origin.on_data("old terminal output")
    origin.on_data("=== KACE_BOOTSTRAP_E")  # A withheld protocol-like tail.
    assert len(timers) == 1
    assert api.connect_ssh("B.local", "fixture", "synthetic")["status"] == "success"
    api._window.evaluate_js.reset_mock()
    if close:
        origin.on_close()
    timers[0].callback()
    api._window.evaluate_js.assert_not_called()


@pytest.fixture
def media_api(api, monkeypatch, tmp_path):
    api._drive_snapshots[3] = snapshot()
    monkeypatch.setattr(main, "bootstrap_preflight_facts", lambda: ("fake", True, "a" * 64))
    monkeypatch.setattr(main, "application_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(main, "validate_provisioning", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(api, "_flash_worker", Mock())
    return api


def start_flash(api):
    return api.start_flash(3, "fake.img", "printer", "", "", "synthetic", "mainsail",
                           drive_identity=snapshot())


def test_eject_cannot_start_during_flash(media_api, monkeypatch):
    media_api._flash_active = True
    helper = Mock(return_value={"success": True})
    monkeypatch.setattr(main, "request_safe_eject", helper)
    assert media_api.eject_drive(3)["success"] is False
    helper.assert_not_called()


def test_flash_cannot_start_until_eject_finishes(media_api, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    def eject(*_args):
        entered.set()
        assert release.wait(3)
        return {"success": True}

    monkeypatch.setattr(main, "request_safe_eject", eject)
    worker = threading.Thread(target=lambda: outcomes.append(media_api.eject_drive(3)))
    worker.start()
    try:
        assert entered.wait(3)
        assert start_flash(media_api) is False
        assert media_api.eject_drive(3)["success"] is False
        media_api._flash_worker.assert_not_called()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert outcomes == [{"success": True}]
    assert start_flash(media_api) is True


def test_eject_helper_exception_releases_media_guard(media_api, monkeypatch):
    monkeypatch.setattr(main, "request_safe_eject", Mock(side_effect=OSError("synthetic eject failure")))
    with pytest.raises(OSError, match="synthetic eject failure"):
        media_api.eject_drive(3)
    assert start_flash(media_api) is True
