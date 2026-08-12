"""Cryptographically image-bound capability attestations for pre-baked OS images."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


ATTESTATION_SCHEMA = "kace-studio-prebaked-attestation/v1"
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

# Identities published by the upstream MainsailOS 3.0.0 release. Keeping them
# outside the image manifest makes isolated manifest tampering fail closed.
TRUSTED_RELEASES = {
    ("mainsailos", "3.0.0"): {
        "source_commit": "77ff5c1eb2731f53440ff2f251b379e1916964ba",
        "source_url": (
            "https://github.com/mainsail-crew/MainsailOS/tree/"
            "77ff5c1eb2731f53440ff2f251b379e1916964ba"
        ),
        "architectures": {
            "64bit": {
                "archive_sha256": (
                    "a4610653b041b80c8283ec7c3ae629fcf700c09f894a156624ef44d8eda15339"
                ),
                "image_sha256": (
                    "2616affb20ee47a1334577713a5542f04015b64e0667c9f071eba68449a65e5a"
                ),
                "image_checksum_url": (
                    "https://github.com/mainsail-crew/MainsailOS/releases/download/3.0.0/"
                    "2026-05-06-MainsailOS-raspberry_pi-arm64-trixie-3.0.0.img.sha256"
                ),
            },
            "32bit": {
                "archive_sha256": (
                    "00065697ef89eb66b001ef8e774f30609cdfdbb634125cf5a1726468ffe2aa55"
                ),
                "image_sha256": (
                    "d96dba4342af5bfaeee30d767d98a9946a55efab7f93002083aa61a63ad29939"
                ),
                "image_checksum_url": (
                    "https://github.com/mainsail-crew/MainsailOS/releases/download/3.0.0/"
                    "2026-05-06-MainsailOS-raspberry_pi-armhf-trixie-3.0.0.img.sha256"
                ),
            },
        },
    }
}


class PrebakedAttestationError(ValueError):
    """Raised when a pre-baked image cannot prove Studio compatibility."""


@dataclass(frozen=True)
class PrebakedAttestation:
    family: str
    version: str
    source_commit: str
    services: tuple[str, ...]
    capabilities: tuple[str, ...]
    image_sha256: str
    archive_sha256: str = ""
    source_url: str = ""
    image_checksum_url: str = ""


def _https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def parse_prebaked_attestation(
    raw: object,
    *,
    image_type: str,
    expected_version: str | None = None,
    expected_architecture: str | None = None,
    expected_archive_sha256: str | None = None,
) -> PrebakedAttestation:
    if not isinstance(raw, dict):
        raise PrebakedAttestationError("attestation must be an object")
    if raw.get("schema") != ATTESTATION_SCHEMA:
        raise PrebakedAttestationError("attestation has an unsupported schema")

    family = raw.get("family")
    version = raw.get("version")
    source_commit = raw.get("source_commit")
    services = raw.get("services")
    capabilities = raw.get("capabilities")
    archive_sha256 = raw.get("archive_sha256", "")
    image_sha256 = raw.get("image_sha256")
    source_url = raw.get("source_url", "")
    image_checksum_url = raw.get("image_checksum_url", "")

    if not isinstance(family, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", family):
        raise PrebakedAttestationError("attestation family is invalid")
    expected_family = AUTOMATIC_FAMILIES.get(image_type)
    if expected_family and family != expected_family:
        raise PrebakedAttestationError(
            f"attestation family {family!r} does not match {image_type!r}"
        )
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise PrebakedAttestationError("attestation version is invalid")
    if expected_version is not None and version != expected_version:
        raise PrebakedAttestationError(
            f"attestation version {version!r} does not match image version {expected_version!r}"
        )

    trusted_release = TRUSTED_RELEASES.get((family, version))
    if trusted_release is None:
        raise PrebakedAttestationError(
            f"attestation version {version!r} is not supported for family {family!r}"
        )
    if not isinstance(source_commit, str) or not FULL_SHA.fullmatch(source_commit):
        raise PrebakedAttestationError("attestation source_commit must be a full commit SHA")
    if source_commit.lower() != trusted_release["source_commit"]:
        raise PrebakedAttestationError(
            "attestation source_commit does not match the reviewed distribution release"
        )
    if not isinstance(image_sha256, str) or not SHA256.fullmatch(image_sha256):
        raise PrebakedAttestationError("attestation requires a valid image_sha256")
    if archive_sha256 and (
        not isinstance(archive_sha256, str) or not SHA256.fullmatch(archive_sha256)
    ):
        raise PrebakedAttestationError("attestation archive_sha256 is invalid")
    if (
        expected_archive_sha256 is not None
        and archive_sha256.lower() != expected_archive_sha256.lower()
    ):
        raise PrebakedAttestationError(
            "attestation archive_sha256 does not match the pinned image archive"
        )

    if not isinstance(services, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9@_.-]+\.service", item)
        for item in services
    ):
        raise PrebakedAttestationError("attestation services must be systemd service names")
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[a-z0-9][a-z0-9_]{1,63}", item)
        for item in capabilities
    ):
        raise PrebakedAttestationError("attestation capabilities are invalid")

    missing_services = sorted(REQUIRED_SERVICES - set(services))
    if missing_services:
        raise PrebakedAttestationError(
            "attestation services are missing: " + ", ".join(missing_services)
        )
    missing_capabilities = sorted(REQUIRED_CAPABILITIES - set(capabilities))
    if missing_capabilities:
        raise PrebakedAttestationError(
            "attestation capabilities are missing: " + ", ".join(missing_capabilities)
        )

    if expected_architecture is not None:
        trusted_architecture = trusted_release["architectures"].get(expected_architecture)
        if trusted_architecture is None:
            raise PrebakedAttestationError("attestation architecture is not supported")
        trusted_fields = {
            "archive_sha256": archive_sha256.lower(),
            "image_sha256": image_sha256.lower(),
            "source_url": source_url,
            "image_checksum_url": image_checksum_url,
        }
        expected_fields = {
            "archive_sha256": trusted_architecture["archive_sha256"],
            "image_sha256": trusted_architecture["image_sha256"],
            "source_url": trusted_release["source_url"],
            "image_checksum_url": trusted_architecture["image_checksum_url"],
        }
        for field, actual in trusted_fields.items():
            if actual != expected_fields[field]:
                raise PrebakedAttestationError(
                    f"attestation {field} does not match the trusted upstream release"
                )
    else:
        if source_url and not _https_url(source_url):
            raise PrebakedAttestationError("attestation source_url must use HTTPS")
        if image_checksum_url and not _https_url(image_checksum_url):
            raise PrebakedAttestationError("attestation image_checksum_url must use HTTPS")

    return PrebakedAttestation(
        family=family,
        version=version,
        source_commit=source_commit.lower(),
        services=tuple(sorted(set(services))),
        capabilities=tuple(sorted(set(capabilities))),
        image_sha256=image_sha256.lower(),
        archive_sha256=archive_sha256.lower(),
        source_url=source_url,
        image_checksum_url=image_checksum_url,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_custom_attestation(image_path: str) -> PrebakedAttestation:
    image = Path(image_path)
    candidates = (
        image.with_name(image.name + ".kace-attestation.json"),
        image.with_suffix(".kace-attestation.json"),
    )
    attestation_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if attestation_path is None:
        raise PrebakedAttestationError(
            "Custom pre-baked image requires a .kace-attestation.json attestation."
        )
    try:
        payload = json.loads(attestation_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrebakedAttestationError("Custom pre-baked attestation is unreadable.") from exc

    attestation = parse_prebaked_attestation(
        payload,
        image_type="custom_prebaked",
    )
    actual_sha256 = sha256_file(image)
    if attestation.image_sha256 != actual_sha256:
        raise PrebakedAttestationError(
            "Custom pre-baked attestation image_sha256 does not match the selected image."
        )
    return attestation
