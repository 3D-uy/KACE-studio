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
