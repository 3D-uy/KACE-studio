"""Exercise the exact remote enrollment program with temporary files and mocks."""

import configparser
import json
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend import moonraker_authorization as access


CONFIG = (b"# User configuration\r\n[server]\r\nport: 7125\r\n"
          b"[authorization]\r\ntrusted_clients:\r\n    127.0.0.1\r\n    ::1/128\r\n"
          b"cors_domains:\r\n    *.local\r\n    *://my.mainsail.xyz\r\n"
          b"[power existing_relay]\r\npin: !gpiochip0/gpio20\r\n")


@pytest.fixture
def remote(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Real network/hardware is forbidden")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    program = {"__name__": "simulated_remote"}
    exec(compile(access.REMOTE_PROGRAM, "remote_enrollment", "exec"), program)
    return SimpleNamespace(**program)


@pytest.mark.parametrize("ip", ["192.168.1.23", "10.0.0.42", "2001:db8::5", "fe80::2"])
def test_single_ip_preserves_every_other_byte_and_is_idempotent(remote, ip):
    result = remote.enroll_content(CONFIG, ip)
    assert result == CONFIG.replace(b"trusted_clients:\r\n",
                                   f"trusted_clients:\r\n    {ip}\r\n".encode())
    assert remote.enroll_content(result, ip) == result
    parser = configparser.ConfigParser()
    parser.read_string(result.decode())
    assert parser["authorization"]["trusted_clients"].split() == [ip, "127.0.0.1", "::1/128"]
    # These are the same API/WebSocket trust entries, including the browser's
    # source IP forwarded by nginx. No changes to CORS or power semantics.
    assert parser["power existing_relay"]["pin"] == "!gpiochip0/gpio20"


@pytest.mark.parametrize("content", [
    b"[authorization]\ntrusted_clients: 192.168.1.23/32 # approved\n",
    b"[authorization]\ntrusted_clients:\n    192.168.1.23\n",
])
def test_equivalent_existing_single_address_is_not_duplicated(remote, content):
    assert remote.enroll_content(content, "192.168.1.23") == content


def test_existing_private_ranges_are_preserved_on_explicit_enrollment(remote):
    original = CONFIG.replace(b"127.0.0.1", b"192.168.0.0/16")
    result = remote.enroll_content(original, "192.168.1.23")
    assert result == original.replace(b"trusted_clients:\r\n",
                                     b"trusted_clients:\r\n    192.168.1.23\r\n")


@pytest.mark.parametrize("content", [b"[authorization]", b"[authorization]\n    trusted_clients: ::1\n"])
def test_missing_list_or_indented_list_remains_valid(remote, content):
    result = remote.enroll_content(content, "192.168.1.23")
    parser = configparser.ConfigParser()
    parser.read_string(result.decode())
    assert "192.168.1.23" in parser["authorization"]["trusted_clients"].split()
    assert remote.enroll_content(result, "192.168.1.23") == result


@pytest.mark.parametrize("connection", ["", "host 12 10.0.0.1 22", "10.0.0.0/8 12 10.0.0.1 22",
    "10.0.0.2 0 10.0.0.1 22", "10.0.0.2 12 10.0.0.1 99999", "0.0.0.0 12 10.0.0.1 22",
    "fe80::1%eth0 12 fe80::2 22", "10.0.0.2 12 10.0.0.1 22 extra"])
def test_unverifiable_ip_never_reaches_config(remote, monkeypatch, connection):
    monkeypatch.setenv("SSH_CONNECTION", connection)
    monkeypatch.setattr(sys, "argv", ["remote", "authorize", "10.0.0.2"])
    writer = Mock(side_effect=AssertionError("must not write"))
    remote.main.__globals__["authorize"] = writer
    with pytest.raises(ValueError):
        remote.main()
    writer.assert_not_called()


def test_ssh_peer_is_read_again_on_approval(remote, monkeypatch):
    monkeypatch.setenv("SSH_CONNECTION", "192.168.1.23 45678 192.168.1.9 22")
    monkeypatch.setattr(sys, "argv", ["remote", "inspect"])
    assert remote.main() == {"ip": "192.168.1.23", "changed": False}
    monkeypatch.setenv("SSH_CONNECTION", "192.168.1.24 45679 192.168.1.9 22")
    monkeypatch.setattr(sys, "argv", ["remote", "authorize", "192.168.1.23"])
    with pytest.raises(ValueError, match="approved IP"):
        remote.main()


@pytest.mark.parametrize("config", [b"[server]\nport: 7125\n",
    CONFIG + b"[include extra.conf]\n", CONFIG + b"[authorization]\n",
    CONFIG.replace(b"cors_domains:", b"force_logins: True\r\ncors_domains:")])
def test_ambiguous_or_incompatible_config_is_rejected(remote, config):
    with pytest.raises((ValueError, configparser.Error)):
        remote.enroll_content(config, "192.168.1.23")


@pytest.fixture
def remote_file(remote, monkeypatch, tmp_path):
    # POSIX operations are simulated on Windows; no sudo/systemctl is executed.
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(flock=Mock(), LOCK_EX=2, LOCK_NB=4))
    if os.name == "nt":
        monkeypatch.setattr(os, "fchmod", lambda *_: None, raising=False)
    restart = Mock()
    monkeypatch.setattr(remote.subprocess, "run", restart)
    path = tmp_path / "moonraker.conf"
    path.write_bytes(CONFIG)
    return remote, path, restart


def test_atomic_enrollment_restarts_only_once(remote_file):
    remote, path, restart = remote_file
    assert remote.authorize(path, "192.168.1.23") is True
    assert remote.authorize(path, "192.168.1.23") is False
    restart.assert_called_once_with(["sudo", "-n", "systemctl", "restart", "moonraker"],
                                    check=True, capture_output=True, timeout=10)
    assert not list(path.parent.glob(".kace-authorization-*"))


def test_restart_failure_restores_original_bytes(remote_file):
    remote, path, restart = remote_file
    restart.side_effect = OSError("sudo unavailable")
    with pytest.raises(RuntimeError, match="configuration restored"):
        remote.authorize(path, "192.168.1.23")
    assert path.read_bytes() == CONFIG


def test_concurrent_edit_is_preserved_before_atomic_replace(remote_file, monkeypatch):
    remote, path, restart = remote_file
    original_fsync = remote.os.fsync
    def edited(descriptor):
        original_fsync(descriptor)
        path.write_bytes(CONFIG + b"# concurrent edit\n")
    monkeypatch.setattr(remote.os, "fsync", edited)
    with pytest.raises(ValueError, match="concurrently"):
        remote.authorize(path, "192.168.1.23")
    assert path.read_bytes() == CONFIG + b"# concurrent edit\n"
    restart.assert_not_called()


@pytest.mark.parametrize("authenticated", [False, True])
def test_transport_requires_authentication_and_closes_channel(authenticated):
    session = Mock()
    transport = session.client.get_transport.return_value
    transport.is_active.return_value = True
    transport.is_authenticated.return_value = authenticated
    channel = transport.open_session.return_value
    channel.recv.side_effect = [json.dumps({"ok": True, "ip": "192.168.1.23", "changed": False}).encode(), b""]
    channel.recv_exit_status.return_value = 0
    if not authenticated:
        with pytest.raises(ValueError, match="authenticated"):
            access.moonraker_client_access(session)
        transport.open_session.assert_not_called()
    else:
        assert access.moonraker_client_access(session)["ip"] == "192.168.1.23"
        arguments = shlex.split(channel.exec_command.call_args.args[0])
        assert arguments == ["python3", "-c", access.REMOTE_PROGRAM, "inspect"]
        channel.close.assert_called_once()


def test_transport_timeout_closes_only_its_command_channel():
    session = Mock()
    transport = session.client.get_transport.return_value
    channel = transport.open_session.return_value
    channel.recv.side_effect = TimeoutError("remote command timed out")
    with pytest.raises(TimeoutError):
        access.moonraker_client_access(session, "192.168.1.23")
    channel.close.assert_called_once()
    session.close.assert_not_called()
    session.client.close.assert_not_called()
