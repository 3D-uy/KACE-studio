"""Stable per-user writable paths shared by source and packaged runtimes."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APPLICATION_DIRECTORY_NAME = "KACE Studio"


class ApplicationPathError(RuntimeError):
    """Raised when a required writable application path is unsafe or unavailable."""


def _local_appdata_root() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    if configured:
        root = Path(configured).expanduser()
    elif os.name == "nt":
        raise ApplicationPathError("LOCALAPPDATA is unavailable for KACE Studio cache storage.")
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser()
    if not root.is_absolute():
        raise ApplicationPathError("The application cache root must be an absolute path.")
    return root.resolve()


def application_cache_dir(*, create: bool = True) -> Path:
    """Return `%LOCALAPPDATA%/KACE Studio/cache`, never the source or `_MEIPASS`."""
    cache = (_local_appdata_root() / APPLICATION_DIRECTORY_NAME / "cache").resolve()
    if create:
        cache.mkdir(parents=True, exist_ok=True)
    return cache


def verify_application_cache_contract() -> Path:
    """Validate that packaged operation cannot place persistent cache in `_MEIPASS`."""
    cache = application_cache_dir(create=False)
    extraction_root = getattr(sys, "_MEIPASS", None)
    if extraction_root:
        runtime_root = Path(extraction_root).resolve()
        try:
            if os.path.commonpath((str(runtime_root), str(cache))) == str(runtime_root):
                raise ApplicationPathError(
                    "The persistent application cache cannot be inside the PyInstaller runtime."
                )
        except ValueError as exc:
            raise ApplicationPathError("The application cache path is invalid.") from exc
    return cache
