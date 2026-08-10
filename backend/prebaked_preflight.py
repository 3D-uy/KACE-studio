"""Cryptographically bound capability contracts for pre-baked OS images."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


CUSTOM_PREFLIGHT_SCHEMA = "kace-studio-prebaked-preflight/v1"
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")

REQUIRED_SERVICES = frozenset(
    {"klipper.service", "moonraker.service", "headless_nm.service"}
)
REQUIRED_CAPABILITIES = frozenset(
    {
        "boot_firmware_mount",
        "networkmanager_boot_profile",
        "printer_data_layout",
        "systemd_first_boot",
    }
)
AUTOMATIC_FAMILIES = {
    "mainsailos_prebaked": "mainsailos",
    "fluiddpi_prebaked": "fluiddpi",
}
TRUSTED_SOURCE_COMMITS = {
    ("mainsailos", "3.0.0"): "77ff5c1eb2731f53440ff2f251b379e1916964ba",
}


class PrebakedPreflightError(ValueError):
    """Raised when a pre-baked image cannot prove Studio compatibility."""


@dataclass(frozen=True)
class PrebakedContract:
    family: str
    version: str
    source_commit: str
    services: tuple[str, ...]
    capabilities: tuple[str, ...]
    image_sha256: str = ""


def parse_prebaked_contract(
    raw: object,
    *,
    image_type: str,
    expected_version: str | None = None,
    require_image_sha256: bool = False,
) -> PrebakedContract:
    if not isinstance(raw, dict):
        raise PrebakedPreflightError("preflight contract must be an object")

    family = raw.get("family")
    version = raw.get("version")
    source_commit = raw.get("source_commit")
    services = raw.get("services")
    capabilities = raw.get("capabilities")
    image_sha256 = raw.get("image_sha256", "")

    if not isinstance(family, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", family):
        raise PrebakedPreflightError("preflight family is invalid")
    expected_family = AUTOMATIC_FAMILIES.get(image_type)
    if expected_family and family != expected_family:
        raise PrebakedPreflightError(
            f"preflight family {family!r} does not match {image_type!r}"
        )
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise PrebakedPreflightError("preflight version is invalid")
    if expected_version is not None and version != expected_version:
        raise PrebakedPreflightError(
            f"preflight version {version!r} does not match image version {expected_version!r}"
        )
    trusted_source_commit = TRUSTED_SOURCE_COMMITS.get((family, version))
    if trusted_source_commit is None:
        raise PrebakedPreflightError(
            f"preflight version {version!r} is not supported for family {family!r}"
        )
    if not isinstance(source_commit, str) or not FULL_SHA.fullmatch(source_commit):
        raise PrebakedPreflightError("preflight source_commit must be a full commit SHA")
    if source_commit.lower() != trusted_source_commit:
        raise PrebakedPreflightError(
            "preflight source_commit does not match the reviewed distribution release"
        )
    if not isinstance(services, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9@_.-]+\.service", item)
        for item in services
    ):
        raise PrebakedPreflightError("preflight services must be systemd service names")
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[a-z0-9][a-z0-9_]{1,63}", item)
        for item in capabilities
    ):
        raise PrebakedPreflightError("preflight capabilities are invalid")

    missing_services = sorted(REQUIRED_SERVICES - set(services))
    if missing_services:
        raise PrebakedPreflightError(
            "preflight services are missing: " + ", ".join(missing_services)
        )
    missing_capabilities = sorted(REQUIRED_CAPABILITIES - set(capabilities))
    if missing_capabilities:
        raise PrebakedPreflightError(
            "preflight capabilities are missing: " + ", ".join(missing_capabilities)
        )
    if require_image_sha256 and (
        not isinstance(image_sha256, str) or not SHA256.fullmatch(image_sha256)
    ):
        raise PrebakedPreflightError(
            "custom preflight contract requires a valid image_sha256"
        )

    return PrebakedContract(
        family=family,
        version=version,
        source_commit=source_commit.lower(),
        services=tuple(sorted(set(services))),
        capabilities=tuple(sorted(set(capabilities))),
        image_sha256=image_sha256.lower() if isinstance(image_sha256, str) else "",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_custom_preflight(image_path: str) -> PrebakedContract:
    image = Path(image_path)
    candidates = (
        image.with_name(image.name + ".kace-preflight.json"),
        image.with_suffix(".kace-preflight.json"),
    )
    contract_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if contract_path is None:
        raise PrebakedPreflightError(
            "Custom pre-baked image requires a .kace-preflight.json preflight contract."
        )
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrebakedPreflightError("Custom preflight contract is unreadable.") from exc
    if not isinstance(payload, dict) or payload.get("schema") != CUSTOM_PREFLIGHT_SCHEMA:
        raise PrebakedPreflightError("Custom preflight contract has an unsupported schema.")

    contract = parse_prebaked_contract(
        payload,
        image_type="custom_prebaked",
        require_image_sha256=True,
    )
    actual_sha256 = sha256_file(image)
    if contract.image_sha256 != actual_sha256:
        raise PrebakedPreflightError(
            "Custom preflight contract image_sha256 does not match the selected image."
        )
    return contract
