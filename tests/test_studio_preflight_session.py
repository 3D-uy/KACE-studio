"""SSH bridge setup, secret delivery and revocation without a printer host."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend import ssh_client, studio_preflight_bridge


ENV = {
    "KACE_STUDIO_PREFLIGHT_PORT": "49001",
    "KACE_STUDIO_PREFLIGHT_TOKEN": "a" * 64,
    "KACE_STUDIO_PREFLIGHT_SESSION": "b" * 32,
}
REMOTE_PATH = "/tmp/kace-studio-preflight-test123.env"


@pytest.fixture
def context(monkeypatch):
    session = ssh_client.SSHSession()
    transport, client, bridge = Mock(), Mock(), Mock()
    transport.is_active.return_value = True
    client.get_transport.return_value = transport
    session.client = client
    bridge.start.return_value = dict(ENV)
    factory = Mock(return_value=bridge)
    monkeypatch.setattr(studio_preflight_bridge, "StudioPreflightBridge", factory)
    command = Mock(return_value=(REMOTE_PATH + "\n").encode())
    monkeypatch.setattr(ssh_client, "_preflight_channel_command", command)
    return SimpleNamespace(session=session, client=client, transport=transport,
                           bridge=bridge, factory=factory, command=command)


def test_setup_passes_secrets_only_through_non_pty_stdin(context):
    validator = object()
    assert context.session.start_preflight(validator) == REMOTE_PATH
    context.factory.assert_called_once_with(context.transport, validator)
    transport, command, payload = context.command.call_args.args
    assert transport is context.transport
    assert json.loads(payload) == ENV
    assert ENV["KACE_STUDIO_PREFLIGHT_TOKEN"] not in command
    assert ENV["KACE_STUDIO_PREFLIGHT_SESSION"] not in command
    assert context.session.channel is None
    assert context.session._preflight_file == REMOTE_PATH
    with pytest.raises(RuntimeError, match="already active"):
        context.session.start_preflight(validator)
    assert context.command.call_count == 1


def test_setup_failure_revokes_and_releases_slot(context):
    context.bridge.start.side_effect = OSError("forward refused")
    with pytest.raises(OSError, match="forward refused"):
        context.session.start_preflight(object())
    context.bridge.close.assert_called_once()
    context.command.assert_not_called()
    assert context.session._preflight_bridge is None


def test_session_replaced_during_setup_cannot_deliver_context(context):
    def command(*args):
        context.session.client = Mock()
        return (REMOTE_PATH + "\n").encode()
    context.command.side_effect = command
    with pytest.raises(RuntimeError, match="session changed"):
        context.session.start_preflight(object())
    context.bridge.close.assert_called_once()
    assert context.session._preflight_bridge is None
    assert context.command.call_count == 2  # cleanup is on the captured old transport
    assert context.command.call_args.args == (context.transport, "rm -f -- " + REMOTE_PATH)


@pytest.mark.parametrize("reply", [b"/etc/passwd\n", b"/tmp/kace-studio-preflight-a.env;touch /tmp/bad\n", b"", b"\xff"])
def test_invalid_remote_path_never_reaches_launch_or_cleanup(context, reply):
    context.command.return_value = reply
    with pytest.raises((RuntimeError, UnicodeError)):
        context.session.start_preflight(object())
    context.bridge.close.assert_called_once()
    assert context.command.call_count == 1


def test_explicit_stop_revokes_before_cleanup_and_is_idempotent(context):
    context.session.start_preflight(object())
    calls = []
    cleaned = threading.Event()
    context.bridge.close.side_effect = lambda: calls.append("revoke")
    def cleanup(*_):
        calls.append("cleanup")
        cleaned.set()
    context.command.side_effect = cleanup
    context.session.stop_preflight()
    assert cleaned.wait(1)
    assert calls == ["revoke", "cleanup"]
    context.session.stop_preflight()
    assert calls == ["revoke", "cleanup"]


def test_stale_workflow_cannot_stop_new_preflight_context(context):
    context.session.start_preflight(object())
    context.session.stop_preflight("/tmp/kace-studio-preflight-old.env")
    context.bridge.close.assert_not_called()
    assert context.session._preflight_file == REMOTE_PATH
    context.session.stop_preflight(REMOTE_PATH)
    context.bridge.close.assert_called_once()
    assert context.session._preflight_file is None


def test_preparation_epoch_rejects_old_start_before_opening_forward(context):
    old_epoch = context.session.stop_preflight()
    current_epoch = context.session.stop_preflight()
    assert current_epoch > old_epoch
    with pytest.raises(RuntimeError, match="superseded"):
        context.session.start_preflight(object(), generation=old_epoch)
    context.factory.assert_not_called()
    assert context.session.start_preflight(object(), generation=current_epoch) == REMOTE_PATH


def test_disconnect_invalidates_unstarted_preparation_epoch(context):
    epoch = context.session.stop_preflight()
    context.session._detach_transport()
    context.session.client = context.client  # synthetic reconnect on same object
    with pytest.raises(RuntimeError, match="superseded"):
        context.session.start_preflight(object(), generation=epoch)
    context.factory.assert_not_called()


def test_cleanup_does_not_block_revocation_or_next_generation(context):
    context.session.start_preflight(object())
    entered, release = threading.Event(), threading.Event()
    def cleanup(*_):
        entered.set()
        assert release.wait(2)
    context.command.side_effect = cleanup
    try:
        epoch = context.session.stop_preflight()
        assert entered.wait(1)
        assert context.session._preflight_bridge is None
        assert context.session.stop_preflight() > epoch
    finally:
        release.set()


@pytest.mark.parametrize("mode", ["close", "discard"])
def test_disconnect_revokes_even_when_remote_file_cannot_be_cleaned(context, mode):
    context.session.start_preflight(object())
    if mode == "close":
        context.session.close()
    else:
        context.session._discard_client(context.client)
    context.bridge.close.assert_called_once()
    context.client.close.assert_called_once()
    assert context.session.client is None
    assert context.session._preflight_bridge is None
    assert context.command.call_count == 1


def test_discard_of_old_client_cannot_revoke_new_bridge(context):
    context.session.start_preflight(object())
    context.session._discard_client(Mock())
    context.bridge.close.assert_not_called()
    assert context.session.client is context.client


def test_terminal_eof_revokes_bridge_without_waiting_for_transport_loss(context, monkeypatch):
    context.session.start_preflight(object())
    channel = Mock()
    channel.exit_status_ready.return_value = True
    channel.recv_ready.return_value = False
    context.transport.open_session.return_value = channel
    monkeypatch.setattr(ssh_client.select, "select", lambda *_: ([], [], []))
    done = threading.Event()
    context.session.run_command_stream("bash", lambda _: None, done.set)
    assert done.wait(2)
    context.bridge.close.assert_called_once()
    assert context.session._preflight_bridge is None


def test_private_env_writer_validates_and_uses_mode_0600(tmp_path, monkeypatch):
    # Execute the exact remote Python source with an isolated tempfile directory;
    # no SSH, shell or actual printer is involved.
    original = tempfile.mkstemp
    calls = []
    def mkstemp(**kwargs):
        assert kwargs.pop("dir") == "/tmp"
        return original(dir=tmp_path, **kwargs)
    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(os, "fchmod", lambda fd, mode: calls.append(mode), raising=False)
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(ENV).encode())))
    monkeypatch.setattr(sys, "stdout", output)
    exec(compile(ssh_client._PREFLIGHT_ENV_SCRIPT, "remote-preflight-context", "exec"), {})
    path = Path(output.getvalue().strip())
    assert path.parent == tmp_path
    assert calls == [0o600]
    assert path.read_text().splitlines() == [f"export {k}={v}" for k, v in sorted(ENV.items())]


@pytest.mark.parametrize("environment", [
    {}, {**ENV, "KACE_STUDIO_PREFLIGHT_TOKEN": "x;bad"},
    {**ENV, "KACE_STUDIO_PREFLIGHT_PORT": "65536"},
    {**ENV, "KACE_STUDIO_PREFLIGHT_SESSION": 12}, {**ENV, "OTHER": "x"},
])
def test_private_env_writer_rejects_invalid_values_before_creating_file(monkeypatch, environment):
    create = Mock(side_effect=AssertionError("must not create a file"))
    monkeypatch.setattr(tempfile, "mkstemp", create)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(environment).encode())))
    with pytest.raises(ValueError):
        exec(compile(ssh_client._PREFLIGHT_ENV_SCRIPT, "remote-preflight-context", "exec"), {})
    create.assert_not_called()


def test_non_pty_channel_bounds_output_and_closes():
    transport, channel = Mock(), Mock()
    transport.open_session.return_value = channel
    channel.recv.return_value = b"a" * 1024
    with pytest.raises(RuntimeError, match="excessive"):
        ssh_client._preflight_channel_command(transport, "fixed command", b"secret")
    channel.get_pty.assert_not_called()
    channel.sendall.assert_called_once_with(b"secret")
    channel.close.assert_called_once()
    channel.settimeout.assert_called_once_with(ssh_client.PREFLIGHT_SETUP_TIMEOUT_SECONDS)


def test_non_pty_exec_acknowledgement_is_bounded(monkeypatch):
    transport, channel = Mock(), Mock()
    transport.open_session.return_value = channel
    closed = threading.Event()
    channel.close.side_effect = closed.set
    def stuck_ack(_command):
        assert closed.wait(1)
        raise OSError("channel closed by deadline")
    channel.exec_command.side_effect = stuck_ack
    monkeypatch.setattr(ssh_client, "PREFLIGHT_SETUP_TIMEOUT_SECONDS", 0.02)
    with pytest.raises(OSError, match="deadline"):
        ssh_client._preflight_channel_command(transport, "fixed command")
    assert closed.is_set()
