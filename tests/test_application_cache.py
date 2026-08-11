"""Stable per-user image-cache paths in source and frozen runtimes."""

from __future__ import annotations

import sys
from pathlib import Path

import main
import pytest
from backend import app_paths
from backend.provisioning import ImageType


def test_cache_is_under_localappdata_in_source_and_frozen_modes(tmp_path, monkeypatch):
    from backend.app_paths import application_cache_dir

    local_appdata = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    source_cache = application_cache_dir(create=False)

    extraction_root = tmp_path / "_MEI-frozen"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extraction_root), raising=False)
    frozen_cache = application_cache_dir(create=False)

    expected = (local_appdata / "KACE Studio" / "cache").resolve()
    assert source_cache == expected
    assert frozen_cache == expected
    assert extraction_root.resolve() not in frozen_cache.parents


def test_all_automatic_image_resolvers_use_application_cache(tmp_path, monkeypatch):
    expected = tmp_path / "LocalAppData" / "KACE Studio" / "cache"
    observed = []
    api = main.Api()

    monkeypatch.setattr(main, "application_cache_dir", lambda: expected)
    monkeypatch.setattr(
        api,
        "_resolve_manifest_image",
        lambda image_type, architecture, cache_dir: (
            observed.append((image_type, architecture, Path(cache_dir))) or "image.img"
        ),
    )

    assert api._resolve_default_image("64bit") == "image.img"
    assert api._resolve_prebaked_image(ImageType.MAINSAILOS_PREBAKED, "32bit") == "image.img"
    assert observed == [
        (ImageType.RASPIOS_VANILLA.value, "64bit", expected),
        (ImageType.MAINSAILOS_PREBAKED.value, "32bit", expected),
    ]


def test_frozen_runtime_rejects_persistent_cache_inside_meipass(tmp_path, monkeypatch):
    extraction_root = tmp_path / "_MEI-frozen"
    monkeypatch.setenv("LOCALAPPDATA", str(extraction_root))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extraction_root), raising=False)

    with pytest.raises(app_paths.ApplicationPathError, match="PyInstaller"):
        app_paths.verify_application_cache_contract()
