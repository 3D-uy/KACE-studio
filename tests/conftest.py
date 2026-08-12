"""Suite-wide isolation for per-user writable application data."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_local_application_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
