"""Studio's PC-side adapter for the authoritative bundled KACE Docker runner."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import threading
import types

from backend.app_paths import application_cache_dir
from backend.resources import bundled_path

FILES = ("klipper_preflight_engine.py", "klipper_preflight.Dockerfile", "klipper_preflight_worker.py")


def verify_preflight_resources(root: Path | None = None):
    root = root if root is not None else bundled_path("preflight")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("schema") != "kace-studio-preflight/v1"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("pin", "")))
            or not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != set(FILES)):
        raise ValueError("Invalid bundled preflight manifest")
    for name in FILES:
        path = root / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"][name]:
            raise ValueError(f"Bundled preflight resource identity mismatch: {name}")
    return manifest


class DockerPreflight:
    """Prepare locally on the PC; never install or run Docker on the SSH host."""

    def __init__(self, *, root: Path | None = None, receipt_path: Path | None = None):
        self.root = root if root is not None else bundled_path("preflight")
        self.manifest = verify_preflight_resources(self.root)
        self.receipt_path = (receipt_path if receipt_path is not None else
                             application_cache_dir() / "preflight/receipt.json")
        self._lock = threading.RLock()
        engine_path = self.root / FILES[0]
        engine_bytes = engine_path.read_bytes()
        if hashlib.sha256(engine_bytes).hexdigest() != self.manifest["files"][FILES[0]]:
            raise ValueError("Bundled preflight engine changed before loading")
        # Execute verified packaged bytes without creating pycache in source or
        # PyInstaller resources; only the application cache is writable state.
        self.engine = types.ModuleType("_kace_studio_preflight_engine")
        self.engine.__file__ = str(engine_path)
        exec(compile(engine_bytes, str(engine_path), "exec"), self.engine.__dict__)
        self._identity = {
            "pin": self.manifest["pin"],
            "engine_sha256": self.manifest["files"][FILES[0]],
            "dockerfile_sha256": self.manifest["files"][FILES[1]],
            "worker_sha256": self.manifest["files"][FILES[2]],
        }

    @property
    def identity(self):
        return dict(self._identity)

    def _options(self):
        if verify_preflight_resources(self.root) != self.manifest:
            raise ValueError("Preflight resources changed after session preparation")
        return {"pin": self.identity["pin"], "recipe": self.root / FILES[1],
                "worker": self.root / FILES[2], "receipt_path": self.receipt_path,
                "run": self.engine._run, "which": self.engine.shutil.which}

    def prepare(self):
        """Build missing/stale image explicitly before Studio launches bootstrap."""
        with self._lock:
            options = self._options()
            docker = self.engine.shutil.which("docker")
            if not docker:
                raise RuntimeError("Docker is required on this PC. Start Docker Desktop and retry.")
            try:
                receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                receipt = {}
            if (isinstance(receipt, dict) and receipt.get("schema") == 1
                    and receipt.get("pin") == self.identity["pin"]
                    and receipt.get("dockerfile_sha256") == self.identity["dockerfile_sha256"]
                    and receipt.get("worker_sha256") == self.identity["worker_sha256"]
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", str(receipt.get("image_id", "")))):
                inspected = self.engine._run([docker, "image", "inspect", "--format", "{{.Id}}", receipt["image_id"]], 30)
                if inspected.returncode == 0 and inspected.stdout.strip() == receipt["image_id"]:
                    self._options()
                    return receipt
            receipt = self.engine.provision(**options)
            self._options()
            return receipt

    def validate_files(self, files):
        """An unavailable PC validator is unverified; it never authorizes writing."""
        with self._lock:
            try:
                result = self.engine.validate_files(files, **self._options())
                self._options()
                return result
            except Exception as exc:
                return {"status": "unverified", "detail": f"Studio preflight unavailable: {exc}", "tree_digest": ""}
