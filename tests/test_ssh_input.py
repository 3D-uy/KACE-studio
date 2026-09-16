"""Terminal sends through simulated channels; no network access."""

import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from backend import ssh_client


@pytest.fixture
def terminal():
    session = ssh_client.SSHSession()
    session.channel = Mock(closed=False)
    session.channel.send_ready.return_value = True
    return session, session.channel


@pytest.mark.parametrize("chunk_size", [100, 2])
def test_input_sends_all_bytes(terminal, chunk_size):
    session, channel = terminal
    received = bytearray()
    data = "echo café 😀\r\n"

    def send(payload):
        payload = payload.encode("utf-8") if isinstance(payload, str) else payload
        accepted = payload[:chunk_size]
        received.extend(accepted)
        return len(accepted)

    channel.send.side_effect = send

    assert session.send_input(data) is True
    assert bytes(received) == data.encode("utf-8")
    if chunk_size == 100:
        assert channel.send.call_count == 1


@pytest.mark.parametrize("failure", [0, OSError("broken channel"), socket.timeout()])
def test_input_failure_after_partial_send_releases_lock(terminal, failure):
    session, channel = terminal
    channel.send.side_effect = [2, failure]

    assert session.send_input("abcd") is False
    assert channel.send.call_count == 2

    channel.send.side_effect = None
    channel.send.return_value = 2
    assert session.send_input("ok") is True


def test_input_times_out_when_window_stops_progressing(terminal, monkeypatch):
    session, channel = terminal
    monkeypatch.setattr(ssh_client, "SSH_INPUT_TIMEOUT_SECONDS", 0.02)
    channel.send.return_value = 2
    channel.send_ready.side_effect = [True] + [False] * 100

    assert session.send_input("abcd") is False
    channel.send.assert_called_once_with(b"abcd")
    channel.settimeout.assert_not_called()


def test_concurrent_inputs_do_not_interleave(terminal):
    session, channel = terminal
    first_chunk = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    received = bytearray()

    def send(payload):
        payload = payload.encode("utf-8") if isinstance(payload, str) else payload
        received.extend(payload[:1])
        if not first_chunk.is_set():
            first_chunk.set()
            assert release_first.wait(2)
        return 1

    def second_input():
        second_started.set()
        return session.send_input("BBB")

    channel.send.side_effect = send
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(session.send_input, "AAA")
        try:
            assert first_chunk.wait(2)
            second = pool.submit(second_input)
            assert second_started.wait(2)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.05)
            assert received == b"A"
        finally:
            release_first.set()
        assert first.result(timeout=2) is True
        assert second.result(timeout=2) is True
    assert received == b"AAABBB"
