"""Detect a silent SSH black hole with simulated replies, never real hardware."""

import threading
from unittest.mock import Mock

import paramiko
import pytest

from backend import ssh_client


@pytest.mark.parametrize("reply", ["timeout", "acknowledged", "refused"])
def test_idle_terminal_requires_bounded_server_reply(monkeypatch, reply):
    session = ssh_client.SSHSession()
    channel, probe, transport, client = Mock(), Mock(), Mock(), Mock()
    client.get_transport.return_value = transport
    transport.is_active.return_value = True
    session.client = client
    clock = [0.0]
    monkeypatch.setattr(ssh_client.time, "monotonic", lambda: clock[0])

    def select(*_args):
        clock[0] += 6
        return [], [], []

    monkeypatch.setattr(ssh_client.select, "select", select)
    channel.exit_status_ready.side_effect = lambda: clock[0] >= 18
    channel.recv_ready.return_value = False
    if reply == "timeout":
        response = TimeoutError("Timeout opening channel")
    elif reply == "refused":
        response = paramiko.ChannelException(1, "Channel limit reached")
    else:
        response = probe
    transport.open_session.side_effect = [channel, response]
    closed = threading.Event()
    output = []
    session.run_command_stream("bash", output.append, closed.set)
    assert closed.wait(1), "A silent transport must not leave the stream hanging"
    assert transport.open_session.call_count == 2
    for call in transport.open_session.call_args_list:
        assert call.kwargs == {"timeout": ssh_client.SSH_CHANNEL_TIMEOUT_SECONDS}
    channel.get_pty.assert_called_once()
    channel.exec_command.assert_called_once_with("bash")
    channel.close.assert_called_once()
    probe.exec_command.assert_not_called()
    if reply == "timeout":
        client.close.assert_called_once()
        assert session.client is None
        assert "Timeout opening channel" in "".join(output)
    else:
        client.close.assert_not_called()
        assert session.client is client
        assert output == []
    if reply == "acknowledged":
        probe.close.assert_called_once()


def test_terminal_setup_failure_closes_transport_and_notifies(monkeypatch):
    session = ssh_client.SSHSession()
    channel, transport, client = Mock(), Mock(), Mock()
    client.get_transport.return_value = transport
    transport.open_session.return_value = channel
    session.client = client
    channel.get_pty.side_effect = TimeoutError("PTY response timed out")
    # A failing cleanup must still deliver the disconnect callback.
    channel.close.side_effect = OSError("Channel already closed")
    closed = threading.Event()
    session.run_command_stream("bash", lambda _: None, closed.set)
    assert closed.wait(1)
    client.close.assert_called_once()
    assert session.client is None


@pytest.mark.parametrize("phase", ["pty", "command"])
def test_unanswered_paramiko_setup_request_cannot_hang_stream(monkeypatch, phase):
    monkeypatch.setattr(ssh_client, "SSH_CHANNEL_TIMEOUT_SECONDS", 0.03)
    channel = paramiko.Channel(7)
    transport, client = Mock(), Mock()
    transport.is_active.return_value = True
    transport.get_exception.return_value = None
    transport.get_log_channel.return_value = "test.ssh"
    transport.get_hexdump.return_value = False
    transport._sanitize_packet_size.side_effect = lambda size: size
    channel._set_transport(transport)
    channel._set_remote_channel(8, 1024*1024, 32768)
    if phase == "command":
        monkeypatch.setattr(channel, "get_pty", lambda **_: None)
    transport.open_session.return_value = channel
    client.get_transport.return_value = transport
    client.close.side_effect = channel.close
    session = ssh_client.SSHSession()
    session.client = client
    closed = threading.Event()
    session.run_command_stream("bash", lambda _: None, closed.set)
    assert closed.wait(1), "An unanswered PTY/exec request must release recovery"
    assert channel.closed
    assert session.client is None
    client.close.assert_called_once()
