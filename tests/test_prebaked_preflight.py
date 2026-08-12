"""Fail-closed, image-bound attestations for pre-baked OS images."""

from __future__ import annotations

import hashlib
import json

import pytest

import main
from backend.image_manifest import ImageManifest, ManifestError
from backend.provisioning import ImageType, validate_provisioning


REQUIRED_SERVICES = {"klipper.service", "moonraker.service", "headless_nm.service"}
REQUIRED_CAPABILITIES = {
    "boot_firmware_mount",
    "networkmanager_boot_profile",
    "printer_data_layout",
    "systemd_first_boot",
}


def _provisioning(image_type=ImageType.MAINSAILOS_PREBAKED, image_path="default_prebaked"):
    return validate_provisioning(
        image_type=image_type,
        image_path=image_path,
        hostname="printer-one",
        wifi_ssid="",
        wifi_password="",
        ssh_password="validpass123",
        dashboard_ui="mainsail",
        os_arch="64bit",
    )


def _raw_image() -> bytes:
    content = bytearray(4096)
    content[510:512] = b"\x55\xaa"
    return bytes(content)


def test_bundled_prebaked_entries_declare_a_complete_runtime_contract():
    entries = [entry for entry in ImageManifest.load_bundled().entries if "prebaked" in entry.image_type]

    assert entries
    for entry in entries:
        assert entry.attestation.family
        assert entry.attestation.version == entry.version
        assert len(entry.attestation.source_commit) == 40
        int(entry.attestation.source_commit, 16)
        assert REQUIRED_SERVICES <= set(entry.attestation.services)
        assert REQUIRED_CAPABILITIES <= set(entry.attestation.capabilities)


def test_bundled_prebaked_entries_have_image_bound_attestations():
    entries = [entry for entry in ImageManifest.load_bundled().entries if "prebaked" in entry.image_type]

    assert entries
    for entry in entries:
        assert entry.attestation.image_sha256
        assert len(entry.attestation.image_sha256) == 64
        int(entry.attestation.image_sha256, 16)
        assert entry.attestation.archive_sha256 == entry.sha256


def test_manifest_rejects_prebaked_entry_without_attestation(tmp_path):
    payload = {
        "schema": "kace-studio-image-manifest/v2",
        "images": [
            {
                "image_type": "mainsailos_prebaked",
                "architecture": "64bit",
                "version": "3.0.0",
                "url": "https://example.invalid/mainsailos.img.xz",
                "sha256": "2" * 64,
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ManifestError, match="attestation"):
        ImageManifest.load(path)


def test_manifest_rejects_attestation_for_different_archive(tmp_path):
    payload = {
        "schema": "kace-studio-image-manifest/v2",
        "images": [
            {
                "image_type": "mainsailos_prebaked",
                "architecture": "64bit",
                "version": "3.0.0",
                "url": "https://example.invalid/mainsailos.img.xz",
                "sha256": "2" * 64,
                "attestation": {
                    "schema": "kace-studio-prebaked-attestation/v1",
                    "family": "mainsailos",
                    "version": "3.0.0",
                    "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
                    "archive_sha256": "3" * 64,
                    "image_sha256": "4" * 64,
                    "services": sorted(REQUIRED_SERVICES),
                    "capabilities": sorted(REQUIRED_CAPABILITIES),
                },
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ManifestError, match="archive_sha256"):
        ImageManifest.load(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", "kace-studio-prebaked-attestation/v0", "schema"),
        ("version", "2.0.0", "version"),
        ("source_commit", "1" * 40, "source_commit"),
        ("services", ["klipper.service"], "services"),
        ("capabilities", ["printer_data_layout"], "capabilities"),
    ],
)
def test_manifest_rejects_incompatible_prebaked_attestation(tmp_path, field, value, message):
    attestation = {
        "schema": "kace-studio-prebaked-attestation/v1",
        "family": "mainsailos",
        "version": "3.0.0",
        "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "source_url": "https://github.com/mainsail-crew/MainsailOS/tree/77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "archive_sha256": "a4610653b041b80c8283ec7c3ae629fcf700c09f894a156624ef44d8eda15339",
        "image_sha256": "2616affb20ee47a1334577713a5542f04015b64e0667c9f071eba68449a65e5a",
        "image_checksum_url": "https://github.com/mainsail-crew/MainsailOS/releases/download/3.0.0/2026-05-06-MainsailOS-raspberry_pi-arm64-trixie-3.0.0.img.sha256",
        "services": sorted(REQUIRED_SERVICES),
        "capabilities": sorted(REQUIRED_CAPABILITIES),
    }
    attestation[field] = value
    payload = {
        "schema": "kace-studio-image-manifest/v2",
        "images": [
            {
                "image_type": "mainsailos_prebaked",
                "architecture": "64bit",
                "version": "3.0.0",
                "url": "https://github.com/mainsail-crew/MainsailOS/releases/download/3.0.0/2026-05-06-MainsailOS-raspberry_pi-arm64-trixie-3.0.0.img.xz",
                "sha256": "a4610653b041b80c8283ec7c3ae629fcf700c09f894a156624ef44d8eda15339",
                "attestation": attestation,
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ManifestError, match=message):
        ImageManifest.load(path)


def test_prebaked_preflight_runs_before_any_block_write(tmp_path, monkeypatch):
    api = main.Api()
    image = tmp_path / "mainsail.img"
    image.write_bytes(_raw_image())
    order = []

    monkeypatch.setattr(api, "_resolve_prebaked_image", lambda *_args: str(image))
    monkeypatch.setattr(api, "_validate_raw_image", lambda *_args: len(_raw_image()))
    monkeypatch.setattr(
        api,
        "_preflight_prebaked_image",
        lambda *_args: order.append("preflight"),
        raising=False,
    )
    monkeypatch.setattr(main, "flash_drive", lambda *_args: (order.append("flash") or True, ""))
    monkeypatch.setattr(main, "inject_config", lambda *_args, **_kwargs: order.append("inject") or True)

    api._flash_worker(8, _provisioning(), {"number": 8})

    assert order == ["preflight", "flash", "inject"]


def test_prebaked_preflight_failure_blocks_writer_and_injection(tmp_path, monkeypatch):
    api = main.Api()
    image = tmp_path / "mainsail.img"
    image.write_bytes(_raw_image())
    calls = []
    states = []

    monkeypatch.setattr(api, "_resolve_prebaked_image", lambda *_args: str(image))
    monkeypatch.setattr(api, "_validate_raw_image", lambda *_args: len(_raw_image()))
    monkeypatch.setattr(api, "set_device_state", lambda state, *_args: states.append(state))
    monkeypatch.setattr(
        api,
        "_preflight_prebaked_image",
        lambda *_args: (_ for _ in ()).throw(ValueError("missing required service")),
        raising=False,
    )
    monkeypatch.setattr(main, "flash_drive", lambda *_args: calls.append("flash"))
    monkeypatch.setattr(main, "inject_config", lambda *_args, **_kwargs: calls.append("inject"))

    api._flash_worker(8, _provisioning(), {"number": 8})

    assert calls == []
    assert states[-1] == "ERROR"


def test_custom_prebaked_requires_checksum_bound_attestation(tmp_path):
    api = main.Api()
    image = tmp_path / "custom.img"
    content = _raw_image()
    image.write_bytes(content)
    (tmp_path / "custom.img.sha256").write_text(
        hashlib.sha256(content).hexdigest() + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="attestation"):
        api._preflight_prebaked_image(str(image), _provisioning(ImageType.CUSTOM_PREBAKED, str(image)))


def test_custom_prebaked_accepts_matching_complete_attestation(tmp_path):
    api = main.Api()
    image = tmp_path / "custom.img"
    content = _raw_image()
    image.write_bytes(content)
    contract = {
        "schema": "kace-studio-prebaked-attestation/v1",
        "family": "mainsailos",
        "version": "3.0.0",
        "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "services": sorted(REQUIRED_SERVICES),
        "capabilities": sorted(REQUIRED_CAPABILITIES),
        "image_sha256": hashlib.sha256(content).hexdigest(),
    }
    (tmp_path / "custom.img.kace-attestation.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )

    result = api._preflight_prebaked_image(
        str(image), _provisioning(ImageType.CUSTOM_PREBAKED, str(image))
    )

    assert result.family == "mainsailos"
    assert set(result.services) >= REQUIRED_SERVICES


def test_custom_prebaked_rejects_attestation_for_different_image(tmp_path):
    api = main.Api()
    image = tmp_path / "custom.img"
    image.write_bytes(_raw_image())
    contract = {
        "schema": "kace-studio-prebaked-attestation/v1",
        "family": "mainsailos",
        "version": "3.0.0",
        "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "services": sorted(REQUIRED_SERVICES),
        "capabilities": sorted(REQUIRED_CAPABILITIES),
        "image_sha256": "4" * 64,
    }
    (tmp_path / "custom.img.kace-attestation.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="does not match"):
        api._preflight_prebaked_image(
            str(image), _provisioning(ImageType.CUSTOM_PREBAKED, str(image))
        )


def test_automatic_prebaked_preflight_rehashes_the_resolved_image(tmp_path, monkeypatch):
    api = main.Api()
    image = tmp_path / "mainsail.img"
    content = _raw_image()
    image.write_bytes(content)
    entry = ImageManifest.load_bundled().resolve("mainsailos_prebaked", "64bit")
    image_sha256 = entry.attestation.image_sha256
    (tmp_path / "mainsail.img.sha256").write_text(image_sha256 + "\n", encoding="utf-8")
    (tmp_path / "mainsail.img.provenance.json").write_text(
        json.dumps(api._image_provenance_payload(entry, image_sha256), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(api, "_compute_sha256", lambda *_args: image_sha256)
    assert api._preflight_prebaked_image(str(image), _provisioning()).version == "3.0.0"

    tampered = bytearray(content)
    tampered[1024] = 1
    image.write_bytes(tampered)
    monkeypatch.setattr(
        api,
        "_compute_sha256",
        lambda *_args: hashlib.sha256(bytes(tampered)).hexdigest(),
    )
    with pytest.raises(ValueError, match="attestation"):
        api._preflight_prebaked_image(str(image), _provisioning())


def test_custom_prebaked_rejects_unvalidated_distribution_version(tmp_path):
    api = main.Api()
    image = tmp_path / "custom.img"
    content = _raw_image()
    image.write_bytes(content)
    contract = {
        "schema": "kace-studio-prebaked-attestation/v1",
        "family": "mainsailos",
        "version": "99.0.0",
        "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "services": sorted(REQUIRED_SERVICES),
        "capabilities": sorted(REQUIRED_CAPABILITIES),
        "image_sha256": hashlib.sha256(content).hexdigest(),
    }
    (tmp_path / "custom.img.kace-attestation.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="not supported"):
        api._preflight_prebaked_image(
            str(image), _provisioning(ImageType.CUSTOM_PREBAKED, str(image))
        )
