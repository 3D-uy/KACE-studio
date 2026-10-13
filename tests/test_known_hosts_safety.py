"""Regression tests for durable, serialized SSH trust storage."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace

import pytest

from backend import ssh_client


ROOT = Path(__file__).resolve().parents[1]


def _set_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(ssh_client.os.path, "expanduser", lambda _value: str(home))


def test_known_hosts_directory_and_files_are_private(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)

    known_hosts = Path(ssh_client._get_known_hosts_path())

    lock_path = known_hosts.with_name("known_hosts.lock")
    assert lock_path.is_file()
    if os.name == "nt":
        source = Path(ssh_client.__file__).read_text(encoding="utf-8")
        assert '"/inheritance:r"' in source
        assert "S-1-5-18" in source
        assert "S-1-5-32-544" in source
    else:
        assert stat.S_IMODE(known_hosts.parent.stat().st_mode) & 0o077 == 0
        assert stat.S_IMODE(known_hosts.stat().st_mode) & 0o077 == 0
        assert stat.S_IMODE(lock_path.stat().st_mode) & 0o077 == 0


def test_known_hosts_lock_serializes_threads(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    known_hosts = ssh_client._get_known_hosts_path()
    active = 0
    maximum_active = 0
    state_lock = threading.Lock()

    def worker():
        nonlocal active, maximum_active
        with ssh_client._known_hosts_lock(known_hosts):
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.02)
            with state_lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum_active == 1


def test_clear_host_key_publish_failure_preserves_previous_file(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    known_hosts = Path(ssh_client._get_known_hosts_path())
    original = (
        "printer.local ssh-ed25519 AAAATEST1\n"
        "other.local ssh-ed25519 AAAATEST2\n"
    )
    known_hosts.write_text(original, encoding="utf-8")
    monkeypatch.setattr(ssh_client.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("publish failed")))

    assert ssh_client.clear_host_key("printer.local") is False
    assert known_hosts.read_text(encoding="utf-8") == original
    assert not list(known_hosts.parent.glob(".known_hosts.*.part"))


def test_clear_host_key_holds_lock_for_read_modify_write(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    known_hosts = Path(ssh_client._get_known_hosts_path())
    known_hosts.write_text("printer.local ssh-ed25519 AAAATEST\n", encoding="utf-8")
    events = []

    @contextmanager
    def observed_lock(path, **_kwargs):
        events.append(("enter", path))
        yield
        events.append(("exit", path))

    monkeypatch.setattr(ssh_client, "_known_hosts_lock", observed_lock)
    monkeypatch.setattr(
        ssh_client, "_get_known_hosts_path", lambda **_kwargs: str(known_hosts)
    )
    real_atomic_write = ssh_client._atomic_write_known_hosts

    def observed_write(path, content):
        assert events == [("enter", str(known_hosts))]
        real_atomic_write(path, content)

    monkeypatch.setattr(ssh_client, "_atomic_write_known_hosts", observed_write)

    assert ssh_client.clear_host_key("printer.local") is True
    assert events == [("enter", str(known_hosts)), ("exit", str(known_hosts))]


@pytest.fixture
def paramiko_trust(tmp_path, monkeypatch):
    """Run real SSHClient verification/policy with an in-process transport."""
    paramiko = ssh_client.paramiko
    old_key = paramiko.RSAKey.generate(1024)
    new_key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    original = f"other.local {old_key.get_name()} {old_key.get_base64()}\n"
    known_hosts.write_bytes(original.encode())
    (tmp_path / "known_hosts.lock").write_bytes(b"\0")
    state = SimpleNamespace(
        path=known_hosts, original=original.encode(), old_key=old_key,
        new_key=new_key, clients=[], saves=[], publications=[], authenticated=[],
        on_auth=lambda: None,
    )

    class SimulatedTransport:
        def __init__(self, *_args, **_kwargs):
            self.closed = False

        def use_compression(self, **_kwargs):
            pass

        def start_client(self, **_kwargs):
            pass

        def get_remote_server_key(self):
            return state.new_key

        def get_security_options(self):
            return SimpleNamespace(key_types=["rsa-sha2-512", "ssh-rsa"])

        def _log(self, *_args):
            pass

        def is_active(self):
            return not self.closed

        def close(self):
            self.closed = True

    class ObservedClient(paramiko.SSHClient):
        def __init__(self):
            super().__init__()
            self.closed = False
            state.clients.append(self)

        def connect(self, *args, **kwargs):
            return super().connect(
                *args, **kwargs, sock=object(), transport_factory=SimulatedTransport
            )

        def _auth(self, *_args, **_kwargs):
            state.authenticated.append(self)
            state.on_auth()

        def save_host_keys(self, filename):
            state.saves.append(filename)
            return super().save_host_keys(filename)

        def close(self):
            self.closed = True
            super().close()

    real_replace = ssh_client.os.replace

    def observed_replace(source, destination):
        state.publications.append((source, destination))
        return real_replace(source, destination)

    monkeypatch.setattr(ssh_client.paramiko, "SSHClient", ObservedClient)
    monkeypatch.setattr(ssh_client, "_get_known_hosts_path", lambda **_kw: str(known_hosts))
    monkeypatch.setattr(ssh_client.os, "replace", observed_replace)
    return state


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_real_policy_persistence_failure_preserves_all_prior_keys(paramiko_trust, monkeypatch, failure):
    state = paramiko_trust
    if failure == "write":
        real_fdopen = ssh_client.os.fdopen

        @contextmanager
        def failed_output(descriptor, *args, **kwargs):
            with real_fdopen(descriptor, *args, **kwargs) as output:
                def partial_write(content):
                    output.write(content[:12])
                    raise OSError("simulated temporary write failure")
                yield SimpleNamespace(write=partial_write)

        def fdopen(descriptor, mode, **kwargs):
            if mode == "w":
                return failed_output(descriptor, mode, **kwargs)
            return real_fdopen(descriptor, mode, **kwargs)

        monkeypatch.setattr(ssh_client.os, "fdopen", fdopen)
    else:
        def failed_replace(*_args):
            raise OSError("simulated replace failure")
        monkeypatch.setattr(ssh_client.os, "replace", failed_replace)

    # Exercise the unchanged retry loop without waiting through its backoff.
    cancellation = SimpleNamespace(is_set=lambda: False, wait=lambda _seconds: False)
    session = ssh_client.SSHSession()
    with pytest.raises(OSError, match="simulated .* failure"):
        session._connect_until_deadline(
            "printer.local", "simulated", "simulated", time.monotonic() + 30, cancellation
        )

    assert state.path.read_bytes() == state.original
    assert state.saves == []
    assert not list(state.path.parent.glob(".known_hosts.*.part"))
    assert session.client is None
    assert not session.is_connected()
    assert len(state.clients) == 6
    assert all(client.closed for client in state.clients)


@pytest.mark.parametrize("address,stored_name", [
    ("printer.local", "printer.local"),
    ("printer.local:2222", "[printer.local]:2222"),
])
def test_real_policy_publishes_new_host_once_and_preserves_known_key(paramiko_trust, address, stored_name):
    state = paramiko_trust
    session = ssh_client.SSHSession()
    try:
        assert session.connect(address, "simulated", "simulated") is True
        assert session.is_connected()
        stored = ssh_client.paramiko.HostKeys(str(state.path))
        assert stored[stored_name]["ssh-rsa"] == state.new_key
        assert stored["other.local"]["ssh-rsa"] == state.old_key
        assert state.saves == []
        assert len(state.publications) == 1
        assert len(state.authenticated) == 1
    finally:
        session.close()


@pytest.mark.parametrize("mismatch", [False, True])
def test_real_client_validates_known_host_before_policy_or_auth(paramiko_trust, mismatch):
    state = paramiko_trust
    state.path.write_bytes(f"printer.local ssh-rsa {state.old_key.get_base64()}\n".encode())
    original = state.path.read_bytes()
    if not mismatch:
        state.new_key = state.old_key
    session = ssh_client.SSHSession()
    try:
        if mismatch:
            with pytest.raises(ssh_client.paramiko.BadHostKeyException):
                session.connect("printer.local", "simulated", "simulated")
            assert session.client is None
            assert state.authenticated == []
            assert state.publications == []
            assert all(client.closed for client in state.clients)
        else:
            assert session.connect("printer.local", "simulated", "simulated") is True
            assert session.is_connected()
            assert len(state.authenticated) == 1
        assert len(state.clients) == 1
        assert state.path.read_bytes() == original
        assert state.saves == []
    finally:
        session.close()


def test_cancelled_real_connection_does_not_publish_staged_host_key(paramiko_trust):
    state = paramiko_trust
    cancellation = threading.Event()
    state.on_auth = cancellation.set
    session = ssh_client.SSHSession()

    with pytest.raises(ssh_client.SSHConnectionDeadlineExceeded):
        session._connect_until_deadline(
            "printer.local", "simulated", "simulated", time.monotonic() + 30, cancellation
        )

    assert state.path.read_bytes() == state.original
    assert state.saves == []
    assert state.publications == []
    assert len(state.authenticated) == 1
    assert len(state.clients) == 1
    assert state.clients[0].closed
    assert session.client is None


def test_ssh_trust_transaction_holds_lock_through_connect_and_publish(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    known_hosts = Path(ssh_client._get_known_hosts_path())
    lock_depth = 0
    events = []

    @contextmanager
    def observed_lock(path, **_kwargs):
        nonlocal lock_depth
        assert path == str(known_hosts)
        lock_depth += 1
        try:
            yield
        finally:
            lock_depth -= 1

    class FakeClient:
        def load_host_keys(self, path):
            assert path == str(known_hosts)
            assert lock_depth == 1
            events.append("load")

        def set_missing_host_key_policy(self, _policy):
            pass

        def connect(self, *_args, **_kwargs):
            assert lock_depth == 1
            events.append("connect")

        def close(self):
            events.append("close")

    def observed_persist(_client, path, *, lock_held=False):
        assert path == str(known_hosts)
        assert lock_held is True
        assert lock_depth == 1
        events.append("persist")

    monkeypatch.setattr(
        ssh_client, "_get_known_hosts_path", lambda **_kwargs: str(known_hosts)
    )
    monkeypatch.setattr(ssh_client, "_known_hosts_lock", observed_lock)
    monkeypatch.setattr(ssh_client.paramiko, "SSHClient", FakeClient)
    monkeypatch.setattr(ssh_client, "_persist_host_keys_atomically", observed_persist)

    assert ssh_client.SSHSession().connect("printer.local", "pi", "secret") is True
    assert events == ["load", "connect", "persist"]


def test_frontend_does_not_reconnect_when_host_key_clear_fails():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    mismatch_block = app_js.split("clear_stored_host_key(currentDeviceIp)", 1)[1].split(
        "} else if (char === 'n')", 1
    )[0]

    assert ".then((cleared) =>" in mismatch_block
    assert "if (!cleared)" in mismatch_block
    assert ".catch(" in mismatch_block
    assert "loginState = 'PROMPTING_HOST_KEY_MISMATCH'" in mismatch_block
