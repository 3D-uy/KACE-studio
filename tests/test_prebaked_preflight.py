"""Fail-closed preflight contracts for pre-baked OS images."""

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
        assert entry.preflight.family
        assert entry.preflight.version == entry.version
        assert len(entry.preflight.source_commit) == 40
        int(entry.preflight.source_commit, 16)
        assert REQUIRED_SERVICES <= set(entry.preflight.services)
        assert REQUIRED_CAPABILITIES <= set(entry.preflight.capabilities)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("version", "2.0.0", "version"),
        ("source_commit", "1" * 40, "source_commit"),
        ("services", ["klipper.service"], "services"),
        ("capabilities", ["printer_data_layout"], "capabilities"),
    ],
)
def test_manifest_rejects_incompatible_prebaked_contract(tmp_path, field, value, message):
    preflight = {
        "family": "mainsailos",
        "version": "3.0.0",
        "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "services": sorted(REQUIRED_SERVICES),
        "capabilities": sorted(REQUIRED_CAPABILITIES),
    }
    preflight[field] = value
    payload = {
        "schema": "kace-studio-image-manifest/v1",
        "images": [
            {
                "image_type": "mainsailos_prebaked",
                "architecture": "64bit",
                "version": "3.0.0",
                "url": "https://example.invalid/mainsailos.img.xz",
                "sha256": "2" * 64,
                "preflight": preflight,
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


def test_custom_prebaked_requires_checksum_bound_preflight_contract(tmp_path):
    api = main.Api()
    image = tmp_path / "custom.img"
    content = _raw_image()
    image.write_bytes(content)
    (tmp_path / "custom.img.sha256").write_text(
        hashlib.sha256(content).hexdigest() + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="preflight contract"):
        api._preflight_prebaked_image(str(image), _provisioning(ImageType.CUSTOM_PREBAKED, str(image)))


def test_custom_prebaked_accepts_matching_complete_preflight_contract(tmp_path):
    api = main.Api()
    image = tmp_path / "custom.img"
    content = _raw_image()
    image.write_bytes(content)
    contract = {
        "schema": "kace-studio-prebaked-preflight/v1",
        "family": "mainsailos",
        "version": "3.0.0",
        "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "services": sorted(REQUIRED_SERVICES),
        "capabilities": sorted(REQUIRED_CAPABILITIES),
        "image_sha256": hashlib.sha256(content).hexdigest(),
    }
    (tmp_path / "custom.img.kace-preflight.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )

    result = api._preflight_prebaked_image(
        str(image), _provisioning(ImageType.CUSTOM_PREBAKED, str(image))
    )

    assert result.family == "mainsailos"
    assert set(result.services) >= REQUIRED_SERVICES


def test_custom_prebaked_rejects_contract_for_different_image(tmp_path):
    api = main.Api()
    image = tmp_path / "custom.img"
    image.write_bytes(_raw_image())
    contract = {
        "schema": "kace-studio-prebaked-preflight/v1",
        "family": "mainsailos",
        "version": "3.0.0",
        "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "services": sorted(REQUIRED_SERVICES),
        "capabilities": sorted(REQUIRED_CAPABILITIES),
        "image_sha256": "4" * 64,
    }
    (tmp_path / "custom.img.kace-preflight.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="does not match"):
        api._preflight_prebaked_image(
            str(image), _provisioning(ImageType.CUSTOM_PREBAKED, str(image))
        )


def test_automatic_prebaked_preflight_rehashes_the_resolved_image(tmp_path):
    api = main.Api()
    image = tmp_path / "mainsail.img"
    content = _raw_image()
    image.write_bytes(content)
    image_sha256 = hashlib.sha256(content).hexdigest()
    (tmp_path / "mainsail.img.sha256").write_text(image_sha256 + "\n", encoding="utf-8")
    entry = ImageManifest.load_bundled().resolve("mainsailos_prebaked", "64bit")
    (tmp_path / "mainsail.img.provenance.json").write_text(
        json.dumps(api._image_provenance_payload(entry, image_sha256), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert api._preflight_prebaked_image(str(image), _provisioning()).version == "3.0.0"

    tampered = bytearray(content)
    tampered[1024] = 1
    image.write_bytes(tampered)
    with pytest.raises(ValueError, match="provenance"):
        api._preflight_prebaked_image(str(image), _provisioning())


def test_custom_prebaked_rejects_unvalidated_distribution_version(tmp_path):
    api = main.Api()
    image = tmp_path / "custom.img"
    content = _raw_image()
    image.write_bytes(content)
    contract = {
        "schema": "kace-studio-prebaked-preflight/v1",
        "family": "mainsailos",
        "version": "99.0.0",
        "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "services": sorted(REQUIRED_SERVICES),
        "capabilities": sorted(REQUIRED_CAPABILITIES),
        "image_sha256": hashlib.sha256(content).hexdigest(),
    }
    (tmp_path / "custom.img.kace-preflight.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="not supported"):
        api._preflight_prebaked_image(
            str(image), _provisioning(ImageType.CUSTOM_PREBAKED, str(image))
        )
