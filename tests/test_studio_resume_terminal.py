"""Resume commands must never be delivered into a previously used PTY."""
import threading
from unittest.mock import Mock

import pytest

from backend import ssh_client
from backend.ssh_client import SSHSession


def terminal():
    session = SSHSession()
    channel = Mock(closed=False)
    channel.send_ready.return_value = True
    channel.send.side_effect = len
    session.channel = channel
    session._resume_terminal_channel = channel
    return session, channel


@pytest.mark.parametrize('input_text', ['kace\n', 'x', '\x03', '\r', ''])
def test_any_prior_input_blocks_resume_without_sending_another_byte(input_text):
    session, channel = terminal()
    assert session.send_input(input_text)
    before = channel.send.call_count
    assert not session.resume_terminal_available()
    assert not session.send_resume_command('must-not-reach-wizard\n')
    assert channel.send.call_count == before


def test_pristine_shell_allows_one_resume_and_consumes_eligibility():
    session, channel = terminal()
    assert session.resume_terminal_available()
    assert session.send_resume_command('guarded-resume\n')
    assert not session.send_resume_command('second\n')
    channel.send.assert_called_once_with(b'guarded-resume\n')


@pytest.mark.parametrize('change', ['closed', 'replacement', 'unrecognized-command'])
def test_closed_or_changed_terminal_rejects_resume(change):
    session, channel = terminal()
    if change == 'closed':
        channel.closed = True
    elif change == 'replacement':
        session.channel = Mock(closed=False)
    else:
        session._resume_terminal_channel = None
    assert not session.send_resume_command('never\n')
    channel.send.assert_not_called()


@pytest.mark.parametrize('command,typed,expected', [
    ('env KACE_MACHINE_OUTPUT=1 bash', False, True),
    ('env KACE_MACHINE_OUTPUT=1 bash', True, False),
    ('kace', False, False),
])
def test_only_known_shell_with_no_setup_input_becomes_eligible(monkeypatch, command, typed, expected):
    session = SSHSession()
    client, transport, channel = Mock(), Mock(), Mock(closed=False)
    client.get_transport.return_value = transport
    transport.open_session.return_value = channel
    channel.send_ready.return_value = True
    channel.send.side_effect = len
    channel.exec_command.side_effect = lambda _: session.send_input('kace\n') if typed else None
    session.client = client
    observed = []
    def poll(*_):
        observed.append(session.resume_terminal_available())
        session.running = False
        return [], [], []
    monkeypatch.setattr(ssh_client.select, 'select', poll)
    closed = threading.Event()
    session.run_command_stream(command, lambda _: None, closed.set)
    assert closed.wait(2)
    assert observed == [expected]
