"""Suite-wide isolation for per-user writable application data."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_local_application_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))


@pytest.fixture
def finalized_release_contract(monkeypatch):
    """Exercise provisioning with a finalized candidate, independently of release staging.

    Tests of the pending candidate use the actual contract and do not use this
    fixture. Resource bytes and pin/hash validation remain unchanged here.
    """
    from backend import resources
    load = resources.load_release_contract
    def finalized():
        contract = load()
        contract["kace"]["runtime_status"] = "pinned"
        return contract
    monkeypatch.setattr(resources, "load_release_contract", finalized)


@pytest.fixture
def isolated_boot_refresh(monkeypatch):
    """Keep content-only injection tests off the host storage-cache refresh.

    Replace imager's bindings, not the shared time/subprocess modules: the
    upstream WiFi parser and other shell/timing contracts still execute normally.
    Check calls in teardown because inject_config catches refresh exceptions.
    Password hashing, file publication and bootstrap validation remain real.
    """
    from subprocess import CompletedProcess
    from types import SimpleNamespace

    from backend import imager

    calls = []

    def sleep(*args, **kwargs):
        calls.append(("sleep", args, kwargs))

    def run(*args, **kwargs):
        calls.append(("run", args, kwargs))
        return CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(imager, "time", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(imager, "subprocess", SimpleNamespace(run=run))
    yield

    expected_pair = [
        ("sleep", (1,), {}),
        ("run", (["powershell", "-Command", "Update-HostStorageCache"],),
         {"capture_output": True, **imager.SUBPROCESS_FLAGS}),
    ]
    assert calls and len(calls) % 2 == 0, calls
    assert calls == expected_pair * (len(calls) // 2)
