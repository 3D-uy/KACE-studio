"""PC validation preparation cannot launch on a changed or interrupted session."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main


PATH = "/tmp/kace-studio-preflight-fixture.env"


class Session:
    def __init__(self):
        self.commands = []
        self.stops = []
        self.generation = 0
        self.path = None
        self.started = []

    def stop_preflight(self, path=None):
        self.stops.append(path)
        if path is None or path == self.path:
            self.generation += 1
            self.path = None
        return self.generation

    def start_preflight(self, validator, *, generation=None):
        assert generation == self.generation
        self.started.append(validator)
        self.path = PATH
        return self.path

    def send_input(self, command):
        self.commands.append(command)
        return True


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(main, "require_release_ready", lambda: None)
    monkeypatch.setattr(main, "load_release_contract", lambda: {"kace": {"bootstrap_sha256": "a" * 64}})
    engine = Mock()
    factory = Mock(return_value=engine)
    monkeypatch.setattr(main, "DockerPreflight", factory)
    api = main.Api()
    session = Session()
    api._ssh = session
    api.set_device_state = Mock()
    return SimpleNamespace(api=api, session=session, engine=engine, factory=factory)


def test_docker_unavailable_blocks_before_terminal_input(context):
    context.engine.prepare.side_effect = RuntimeError("Docker unavailable")
    result = context.api.start_bootstrap("mainsail")
    assert result["status"] == "failed" and "Docker unavailable" in result["message"]
    assert not context.session.started and not context.session.commands
    assert not context.api._bootstrap_active
    assert context.api._bootstrap_workflow_id is None


def test_changed_ssh_during_pc_prepare_never_starts_remote_context(context):
    replacement = Session()
    def prepare():
        context.api._ssh = replacement
        context.api._ssh_gen += 1
    context.engine.prepare.side_effect = prepare
    result = context.api.start_bootstrap("mainsail")
    assert result["status"] == "failed"
    assert not context.session.started and not replacement.started
    assert not context.session.commands and not replacement.commands
    assert not context.api._bootstrap_active


def test_interrupted_pc_prepare_never_starts_remote_context(context):
    def prepare():
        context.api._bootstrap_workflow_id = None
        context.api._bootstrap_active = False
    context.engine.prepare.side_effect = prepare
    assert context.api.start_bootstrap("mainsail")["status"] == "failed"
    assert not context.session.started and not context.session.commands


@pytest.mark.parametrize("interruption", ["session", "workflow"])
def test_change_during_context_setup_revokes_only_its_private_path(context, interruption, monkeypatch):
    original = context.session.start_preflight
    def start(*args, **kwargs):
        path = original(*args, **kwargs)
        if interruption == "session":
            context.api._ssh = Session()
            context.api._ssh_gen += 1
        else:
            context.api._bootstrap_workflow_id = "new-workflow"
        return path
    monkeypatch.setattr(context.session, "start_preflight", start)
    assert context.api.start_bootstrap("mainsail")["status"] == "failed"
    assert not context.session.commands
    assert context.session.stops[-1] == PATH
    if interruption == "workflow":
        assert context.api._bootstrap_workflow_id == "new-workflow"


def test_bridge_failure_releases_launch_guard(context, monkeypatch):
    monkeypatch.setattr(context.session, "start_preflight", Mock(side_effect=RuntimeError("forwarding refused")))
    assert context.api.start_bootstrap("mainsail")["status"] == "failed"
    assert not context.session.commands
    assert not context.api._bootstrap_active


def test_unready_terminal_revokes_context_and_releases_guard(context, monkeypatch):
    monkeypatch.setattr(context.session, "send_input", Mock(return_value=False))
    assert context.api.start_bootstrap("mainsail")["status"] == "failed"
    assert context.session.stops[-1] == PATH
    assert not context.api._bootstrap_active


def test_success_command_contains_private_path_but_no_secret(context):
    assert context.api.start_bootstrap("mainsail")["status"] == "started"
    context.engine.prepare.assert_called_once()
    assert context.session.started == [context.engine]
    assert len(context.session.commands) == 1
    command = context.session.commands[0]
    assert f"( set +x; . {PATH} && rm -f -- {PATH} && " in command
    assert "KACE_STUDIO_PREFLIGHT_TOKEN" not in command
    assert "KACE_STUDIO_PREFLIGHT_SESSION" not in command
    assert "sha256sum --status --check" in command
    assert command.endswith(")\n")


def test_busy_workflow_does_not_prepare_or_revoke(context):
    context.api._bootstrap_active = True
    context.api._bootstrap_workflow_id = "existing"
    result = context.api.start_bootstrap("mainsail")
    assert result == {"status": "busy", "message": "A bootstrap workflow is already active.", "workflow_id": "existing"}
    context.factory.assert_not_called()
    assert not context.session.stops
    assert not context.session.commands
