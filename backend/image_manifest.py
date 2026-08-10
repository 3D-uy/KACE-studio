"""Strict, immutable OS image selection for automatic provisioning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from backend.resources import bundled_path


SCHEMA = "kace-studio-image-manifest/v1"
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
SUPPORTED_ARCHITECTURES = {"32bit", "64bit"}
SUPPORTED_IMAGE_TYPES = {"raspios_vanilla", "mainsailos_prebaked", "fluiddpi_prebaked"}


class ManifestError(ValueError):
    """Raised when an image manifest or requested entry is not trustworthy."""


@dataclass(frozen=True)
class ImageManifestEntry:
    image_type: str
    architecture: str
    version: str
    url: str
    sha256: str

    @property
    def filename(self) -> str:
        return Path(unquote(urlparse(self.url).path)).name


class ImageManifest:
    def __init__(self, entries: tuple[ImageManifestEntry, ...]):
        self.entries = entries
        self._by_identity = {(entry.image_type, entry.architecture): entry for entry in entries}

    @classmethod
    def load_bundled(cls) -> "ImageManifest":
        return cls.load(bundled_path("image-manifest.json"))

    @classmethod
    def load(cls, path: Path) -> "ImageManifest":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManifestError("Image manifest is unreadable.") from exc
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise ManifestError("Unsupported image manifest schema.")
        raw_entries = payload.get("images")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ManifestError("Image manifest must contain at least one image.")

        entries: list[ImageManifestEntry] = []
        identities: set[tuple[str, str]] = set()
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, dict):
                raise ManifestError(f"Image manifest entry {index} must be an object.")
            image_type = raw.get("image_type")
            architecture = raw.get("architecture")
            version = raw.get("version")
            url = raw.get("url")
            sha256 = raw.get("sha256")
            if image_type not in SUPPORTED_IMAGE_TYPES:
                raise ManifestError(f"Image manifest entry {index} has an invalid image_type.")
            if architecture not in SUPPORTED_ARCHITECTURES:
                raise ManifestError(f"Image manifest entry {index} has an invalid architecture.")
            if not isinstance(version, str) or not version.strip():
                raise ManifestError(f"Image manifest entry {index} requires a version.")
            parsed_url = urlparse(url) if isinstance(url, str) else None
            if not parsed_url or parsed_url.scheme != "https" or not parsed_url.netloc:
                raise ManifestError(f"Image manifest entry {index} requires an HTTPS URL.")
            filename = Path(unquote(parsed_url.path)).name
            if not filename.lower().endswith((".img.xz", ".zip")):
                raise ManifestError(f"Image manifest entry {index} has an unsupported image URL.")
            if not isinstance(sha256, str) or not SHA256.fullmatch(sha256):
                raise ManifestError(f"Image manifest entry {index} requires a valid sha256.")
            identity = (image_type, architecture)
            if identity in identities:
                raise ManifestError(f"Duplicate image manifest entry: {image_type}/{architecture}.")
            identities.add(identity)
            entries.append(
                ImageManifestEntry(
                    image_type=image_type,
                    architecture=architecture,
                    version=version.strip(),
                    url=url,
                    sha256=sha256.lower(),
                )
            )
        return cls(tuple(entries))

    def resolve(self, image_type: str, architecture: str) -> ImageManifestEntry:
        try:
            return self._by_identity[(image_type, architecture)]
        except KeyError as exc:
            raise ManifestError(
                f"No verified manifest entry exists for {image_type}/{architecture}."
            ) from exc
