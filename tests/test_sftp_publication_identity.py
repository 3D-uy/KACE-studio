"""Publication races using real local staging and an offline SFTP double."""
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

import main
from backend import ssh_client


@pytest.fixture
def download(tmp_path, monkeypatch):
    api = main.Api()
    session = api._ssh
    session.is_connected = lambda: True
    api._ssh_gen = 7
    target = tmp_path / "previous.cfg"
    target.write_bytes(b"preserve previous backup")
    hooks = SimpleNamespace(dialog=lambda: None, transfer=lambda: None)

    def dialog(*_args, **_kwargs):
        hooks.dialog()
        return str(target)

    def get(_remote, temporary):
        Path(temporary).write_bytes(b"downloaded")
        hooks.transfer()

    sftp = SimpleNamespace(get=get, close=lambda: None)
    monkeypatch.setattr(session, "get_sftp", lambda: sftp)
    api._window = SimpleNamespace(create_file_dialog=dialog)

    def invalidate():
        with api._ssh_lock:
            api._ssh = ssh_client.SSHSession()
            api._ssh_gen += 1

    return api, session, target, hooks, invalidate


@pytest.mark.parametrize("phase", ["dialog", "transfer", "after_fsync"])
def test_invalidated_download_preserves_destination_and_removes_own_temporary(download, monkeypatch, phase):
    api, _session, target, hooks, invalidate = download
    if phase == "after_fsync":
        original = ssh_client.os.fsync
        def fsync(descriptor):
            original(descriptor)
            invalidate()
        monkeypatch.setattr(ssh_client.os, "fsync", fsync)
    else:
        setattr(hooks, phase, invalidate)
    assert api.download_file("/synthetic/printer.cfg", 7) is False
    assert target.read_bytes() == b"preserve previous backup"
    assert list(target.parent.iterdir()) == [target]


def test_publication_holds_identity_lock_but_transfer_does_not(download, monkeypatch):
    api, _session, target, hooks, _invalidate = download
    def transfer():
        assert api._ssh_lock.acquire(blocking=False), "network must not hold the session lock"
        api._ssh_lock.release()
    hooks.transfer = transfer
    replace = ssh_client.os.replace
    held = []
    def publish(source, destination):
        acquired = api._ssh_lock.acquire(blocking=False)
        held.append(not acquired)
        if acquired:
            api._ssh_lock.release()
        replace(source, destination)
    monkeypatch.setattr(ssh_client.os, "replace", publish)
    assert api.download_file("/synthetic/printer.cfg", 7) is True
    assert held == [True]
    assert target.read_bytes() == b"downloaded"
    assert list(target.parent.iterdir()) == [target]


def test_disconnected_transport_before_publication_cannot_replace(download):
    api, session, target, hooks, _invalidate = download
    hooks.transfer = lambda: setattr(session, "is_connected", lambda: False)
    assert api.download_file("/synthetic/printer.cfg", 7) is False
    assert target.read_bytes() == b"preserve previous backup"


def test_replace_failure_keeps_previous_destination(download, monkeypatch):
    api, _session, target, _hooks, _invalidate = download
    monkeypatch.setattr(ssh_client.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk failure")))
    assert api.download_file("/synthetic/printer.cfg", 7) is False
    assert target.read_bytes() == b"preserve previous backup"
    assert list(target.parent.iterdir()) == [target]


def test_session_change_can_complete_while_transfer_waits(download):
    api, _session, target, hooks, invalidate = download
    transferring, release = threading.Event(), threading.Event()
    def transfer():
        transferring.set()
        assert release.wait(2)
    hooks.transfer = transfer
    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(api.download_file("/synthetic/file", 7)))
    worker.start()
    try:
        assert transferring.wait(1)
        invalidate()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert outcome == [False]
    assert target.read_bytes() == b"preserve previous backup"
    assert list(target.parent.iterdir()) == [target]
