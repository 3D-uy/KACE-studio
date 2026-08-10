"""Authoritative runtime-resource and release-contract resolution."""

from __future__ import annotations

import json
import hashlib
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_CONTRACT_NAME = "release-contract.json"


class ResourceContractError(RuntimeError):
    """Raised when bundled inputs are absent or internally inconsistent."""


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))


def runtime_root() -> Path:
    """Return the extraction root when frozen and repository root from source."""
    if is_frozen():
        extraction_root = getattr(sys, "_MEIPASS", None)
        if not extraction_root:
            raise ResourceContractError("Frozen runtime does not expose a PyInstaller resource root.")
        return Path(extraction_root).resolve()
    return PROJECT_ROOT


def bundled_path(relative_path: str) -> Path:
    """Resolve one contract-owned resource without allowing path escape."""
    relative = PurePosixPath(str(relative_path).replace("\\", "/"))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ResourceContractError(f"Invalid bundled resource path: {relative_path!r}")
    root = runtime_root()
    candidate = root.joinpath(*relative.parts).resolve()
    try:
        if os.path.commonpath((str(root), str(candidate))) != str(root):
            raise ResourceContractError(f"Bundled resource escapes runtime root: {relative_path!r}")
    except ValueError as exc:
        raise ResourceContractError(f"Invalid bundled resource path: {relative_path!r}") from exc
    return candidate


def load_release_contract() -> dict[str, Any]:
    path = bundled_path(RELEASE_CONTRACT_NAME)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResourceContractError(f"Release contract is unreadable: {path.name}") from exc
    if not isinstance(data, dict) or data.get("schema") != "kace-studio-release-contract/v1":
        raise ResourceContractError("Unsupported KACE Studio release contract.")
    return data


def resolve_bootstrap_source() -> Path:
    """Resolve the exact bootstrap used by source and packaged injection."""
    if is_frozen():
        return bundled_path("bootstrap.sh")
    sibling = (PROJECT_ROOT.parent / "KACE" / "scripts" / "bootstrap.sh").resolve()
    if sibling.is_file():
        return sibling
    return bundled_path("bootstrap.sh")


def verify_runtime_resources() -> None:
    """Fail closed when a source or frozen runtime is missing contract-owned bytes."""
    contract = load_release_contract()
    for relative_name in contract.get("bundled_resources", []):
        path = bundled_path(str(relative_name))
        if not path.exists():
            raise ResourceContractError(f"Required runtime resource is missing: {relative_name}")
    bootstrap = bundled_path("bootstrap.sh")
    actual = hashlib.sha256(bootstrap.read_bytes()).hexdigest()
    if actual != contract.get("kace", {}).get("bootstrap_sha256"):
        raise ResourceContractError("Bundled bootstrap does not match release contract.")
    if not bundled_path("web/index.html").is_file():
        raise ResourceContractError("Bundled frontend entrypoint is missing.")
