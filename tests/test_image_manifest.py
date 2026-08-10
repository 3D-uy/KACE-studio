"""Regression tests for immutable automatic OS image selection."""

from __future__ import annotations

import json

import pytest

from backend.image_manifest import ImageManifest, ManifestError


def test_bundled_manifest_pins_version_url_and_sha256_for_every_entry():
    manifest = ImageManifest.load_bundled()

    assert manifest.entries
    for entry in manifest.entries:
        assert entry.version
        assert entry.url.startswith("https://")
        assert len(entry.sha256) == 64
        int(entry.sha256, 16)


def test_manifest_rejects_an_entry_without_a_checksum(tmp_path):
    path = tmp_path / "images.json"
    path.write_text(
        json.dumps(
            {
                "schema": "kace-studio-image-manifest/v1",
                "images": [
                    {
                        "image_type": "raspios_vanilla",
                        "architecture": "64bit",
                        "version": "1.0",
                        "url": "https://example.invalid/image.img.xz",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="sha256"):
        ImageManifest.load(path)


def test_unverified_fluiddpi_image_fails_closed():
    manifest = ImageManifest.load_bundled()

    with pytest.raises(ManifestError, match="verified manifest entry"):
        manifest.resolve("fluiddpi_prebaked", "32bit")


def test_manifest_resolves_a_fixed_image_without_latest_lookup():
    entry = ImageManifest.load_bundled().resolve("mainsailos_prebaked", "64bit")

    assert entry.version == "3.0.0"
    assert "/releases/download/3.0.0/" in entry.url
    assert entry.filename.endswith(".img.xz")
