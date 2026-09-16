"""SFTP deadlines with simulated transports; no network or printer access."""

import socket
import threading
import time
from unittest.mock import MagicMock, Mock

import paramiko
import pytest

from backend import ssh_client


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setattr(ssh_client, "SFTP_TIMEOUT_SECONDS", 0.05, raising=False)
    channel = paramiko.Channel(7)
    transport = Mock()
    transport.is_active.return_value = True
    transport.get_exception.return_value = None
    transport.get_log_channel.return_value = "test.sftp"
    transport.get_hexdump.return_value = False
    transport._sanitize_packet_size.side_effect = lambda size: size
    channel._set_transport(transport)
    channel._set_remote_channel(8, 1024 * 1024, 32768)
    transport.open_session.return_value = channel
    client = Mock()
    client.get_transport.return_value = transport
    # Exercise the old unbounded path too, so these tests reproduce the bug.
    client.open_sftp.side_effect = lambda: paramiko.SFTPClient.from_transport(transport)
    client.close.side_effect = channel.close
    session = ssh_client.SSHSession()
    session.client = client
    yield session, client, transport, channel
    channel.close()


def fake_files(monkeypatch, channel):
    sftp = Mock()
    sftp.normalize.return_value = "/home/kace"
    sftp.stat.return_value.st_size = 2
    source = MagicMock()
    source.__enter__.return_value = source
    source.__exit__.side_effect = lambda *_args: source.close()
    source.close.return_value = None
    source.read.return_value = b"{}"
    sftp.open.return_value = source
    sftp.close.side_effect = channel.close
    monkeypatch.setattr(channel, "invoke_subsystem", Mock())

    def initialize(actual_channel):
        assert actual_channel is channel
        assert channel.gettimeout() == ssh_client.SFTP_TIMEOUT_SECONDS
        return sftp

    monkeypatch.setattr(ssh_client.paramiko, "SFTPClient", initialize)
    return sftp, source


def test_healthy_sftp_keeps_reading_and_closes_file_and_channel(storage, monkeypatch):
    session, _client, transport, channel = storage
    sftp, source = fake_files(monkeypatch, channel)
    assert session.get_sftp() is sftp
    time.sleep(0.1)
    assert not channel.closed, "the initialization timer must not expire a healthy session"
    monkeypatch.setattr(session, "get_sftp", lambda: sftp)
    assert session.read_text_file_result(".config/kace/power.json") == ("ok", "{}")
    transport.open_session.assert_called_once_with(timeout=0.05)
    channel.invoke_subsystem.assert_called_once_with("sftp")
    sftp.normalize.assert_called_once_with(".")
    source.read.assert_called_once_with(256 * 1024 + 1)
    source.close.assert_called_once()
    sftp.close.assert_called_once()
    assert channel.closed


def test_channel_open_timeout_discards_pending_transport(storage):
    session, client, transport, channel = storage

    def blocked_open(*_args, **kwargs):
        timeout = kwargs.get("timeout")
        assert timeout == 0.05
        raise socket.timeout("simulated channel open timeout")

    transport.open_session.side_effect = blocked_open
    assert session.read_text_file_result(".config/kace/power.json") == ("error", None)
    client.close.assert_called_once()
    assert session.client is None
    assert channel.closed


@pytest.mark.parametrize("phase", ["subsystem", "version"])
def test_blocked_sftp_initialization_has_a_deadline_and_closes_channel(storage, monkeypatch, phase):
    session, _client, _transport, channel = storage
    if phase == "version":
        monkeypatch.setattr(channel, "invoke_subsystem", lambda _name: None)
    # Emergency cleanup bounds the test even on the unfixed implementation.
    watchdog = threading.Timer(0.8, channel.close)
    watchdog.start()
    started = time.monotonic()
    try:
        assert session.read_text_file_result(".config/kace/power.json") == ("error", None)
        assert time.monotonic() - started < 0.5
        assert channel.closed
    finally:
        watchdog.cancel()
        watchdog.join(1)


@pytest.mark.parametrize("operation", ["normalize", "stat", "open", "read"])
def test_stalled_sftp_reads_report_existing_power_error_and_close(storage, monkeypatch, operation):
    from main import Api

    session, _client, _transport, channel = storage
    sftp, source = fake_files(monkeypatch, channel)
    target = source if operation == "read" else sftp
    getattr(target, operation).side_effect = lambda *_args: channel.recv(1)
    started = time.monotonic()
    result = Api()._read_remote_power_config(session)
    assert result["status"] == "error"
    assert result["config"] is None
    assert "could not be read" in result["detail"]
    assert time.monotonic() - started < 0.5
    sftp.close.assert_called_once()
    assert channel.closed
    if operation == "read":
        source.close.assert_called_once()


def test_session_close_still_cancels_pending_sftp_initialization(storage, monkeypatch):
    session, client, transport, channel = storage
    started = threading.Event()
    transport._send_user_message.side_effect = lambda _message: started.set()
    monkeypatch.setattr(ssh_client, "SFTP_TIMEOUT_SECONDS", 1.0)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(session.read_text_file_result(".config/kace/power.json")),
        daemon=True,
    )
    worker.start()
    try:
        assert started.wait(0.5)
        session.close()
        worker.join(0.5)
        assert not worker.is_alive()
        assert results == [("error", None)]
        client.close.assert_called_once()
        assert channel.closed
        assert session.client is None
    finally:
        channel.close()
        worker.join(1)
