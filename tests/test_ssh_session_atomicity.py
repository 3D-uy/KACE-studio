"""Regressions for isolated, atomically published SSH sessions."""

from __future__ import annotations

import threading

import main


class FakeSession:
    def __init__(self, *, connect_gate=None):
        self.connect_gate = connect_gate
        self.connect_started = threading.Event()
        self.closed = False
        self.stream_started = False

    def connect(self, *_args):
        self.connect_started.set()
        if self.connect_gate is not None:
            assert self.connect_gate.wait(2)
        return True

    def close(self):
        self.closed = True

    def run_command_stream(self, *_args, **_kwargs):
        self.stream_started = True


def prepare_api(monkeypatch):
    api = main.Api()
    api.set_device_state = lambda *_args, **_kwargs: None
    api._refresh_remote_power_authority = lambda: None
    return api


def test_connection_is_built_on_an_isolated_candidate_before_atomic_swap(monkeypatch):
    api = prepare_api(monkeypatch)
    previous = FakeSession()
    candidate = FakeSession()
    api._ssh = previous
    monkeypatch.setattr(main, "SSHSession", lambda: candidate)

    result = api.connect_ssh("printer.local", "kace", "secret")

    assert result["status"] == "success"
    assert api._ssh is candidate
    assert candidate.stream_started is True
    assert previous.closed is True


def test_slower_superseded_connection_cannot_replace_or_close_new_session(monkeypatch):
    api = prepare_api(monkeypatch)
    previous = FakeSession()
    release_first = threading.Event()
    first = FakeSession(connect_gate=release_first)
    second = FakeSession()
    candidates = iter((first, second))
    api._ssh = previous
    monkeypatch.setattr(main, "SSHSession", lambda: next(candidates))
    results = {}

    older = threading.Thread(
        target=lambda: results.setdefault(
            "older", api.connect_ssh("old.local", "kace", "secret")
        )
    )
    newer = threading.Thread(
        target=lambda: results.setdefault(
            "newer", api.connect_ssh("new.local", "kace", "secret")
        )
    )
    older.start()
    assert first.connect_started.wait(1)
    newer.start()
    newer.join(2)
    release_first.set()
    older.join(2)

    assert results["newer"]["status"] == "success"
    assert results["older"]["message"] == "Connection superseded by a newer attempt."
    assert api._ssh is second
    assert second.closed is False
    assert second.stream_started is True
    assert first.closed is True
    assert first.stream_started is False
