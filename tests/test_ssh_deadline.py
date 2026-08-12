"""Wall-clock deadline regressions for the complete SSH connection transaction."""

from __future__ import annotations

from contextlib import contextmanager
import threading
import time

import pytest

from backend import ssh_client


@contextmanager
def _unlocked(_path, **_kwargs):
    yield


def test_blocked_paramiko_connect_cannot_exceed_total_deadline(tmp_path, monkeypatch):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("", encoding="utf-8")
    release = threading.Event()
    started = threading.Event()

    class BlockingClient:
        def load_host_keys(self, _path):
            pass

        def set_missing_host_key_policy(self, _policy):
            pass

        def connect(self, *_args, **_kwargs):
            started.set()
            release.wait(5)

        def close(self):
            release.wait(5)

    monkeypatch.setattr(
        ssh_client, "_get_known_hosts_path", lambda **_kwargs: str(known_hosts)
    )
    monkeypatch.setattr(ssh_client, "_known_hosts_lock", _unlocked)
    monkeypatch.setattr(ssh_client.paramiko, "SSHClient", BlockingClient)
    monkeypatch.setattr(ssh_client.paramiko, "AutoAddPolicy", lambda: object())

    session = ssh_client.SSHSession()
    started_at = time.monotonic()
    try:
        with pytest.raises(ssh_client.SSHConnectionDeadlineExceeded):
            session.connect(
                "printer.local", "kace", "secret", deadline_seconds=0.05
            )
    finally:
        release.set()

    assert started.wait(0.5)
    assert time.monotonic() - started_at < 0.5


def test_paramiko_receives_timeouts_for_every_connection_phase(tmp_path, monkeypatch):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("", encoding="utf-8")
    observed = {}

    class SuccessfulClient:
        def load_host_keys(self, _path):
            pass

        def set_missing_host_key_policy(self, _policy):
            pass

        def connect(self, *_args, **kwargs):
            observed.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(
        ssh_client, "_get_known_hosts_path", lambda **_kwargs: str(known_hosts)
    )
    monkeypatch.setattr(ssh_client, "_known_hosts_lock", _unlocked)
    monkeypatch.setattr(ssh_client.paramiko, "SSHClient", SuccessfulClient)
    monkeypatch.setattr(ssh_client.paramiko, "AutoAddPolicy", lambda: object())
    monkeypatch.setattr(ssh_client, "_persist_host_keys_atomically", lambda *_args, **_kwargs: None)

    assert ssh_client.SSHSession().connect(
        "printer.local", "kace", "secret", deadline_seconds=2.0
    )
    for name in ("timeout", "banner_timeout", "auth_timeout", "channel_timeout"):
        assert 0 < observed[name] <= 2.0


@pytest.mark.parametrize("value", ["", "zero", "0", "-1", "nan", "inf"])
def test_invalid_ssh_deadline_configuration_fails_before_network(monkeypatch, value):
    monkeypatch.setenv("KACE_STUDIO_SSH_CONNECT_DEADLINE_S", value)

    with pytest.raises(ValueError, match="KACE_STUDIO_SSH_CONNECT_DEADLINE_S"):
        ssh_client.ssh_connect_deadline_seconds()
