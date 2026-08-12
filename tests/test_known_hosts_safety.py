"""Regression tests for durable, serialized SSH trust storage."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading
import time

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


def test_ssh_connect_does_not_use_paramiko_non_atomic_save():
    source = Path(ssh_client.__file__).read_text(encoding="utf-8")
    connect_body = source.split("def connect(", 1)[1].split("def run_command_stream", 1)[0]

    assert ".save_host_keys(" not in connect_body
    assert "_persist_host_keys_atomically" in connect_body


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
    monkeypatch.setattr(ssh_client.paramiko, "AutoAddPolicy", lambda: object())
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
