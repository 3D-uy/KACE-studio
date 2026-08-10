#!/usr/bin/env python3
"""Verify release inputs, packaged resources, and emit an external manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONTRACT_PATH = ROOT / "release-contract.json"
LOCK_PATH = ROOT / "requirements.lock"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_CONTRACT_DOWNLOAD_BYTES = 5 * 1024 * 1024


class ReleaseContractError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseContractError("release-contract.json is unreadable") from exc
    if contract.get("schema") != "kace-studio-release-contract/v1":
        raise ReleaseContractError("unsupported release contract schema")
    return contract


def verify_image_manifest(path: Path) -> int:
    """Require every releasable automatic image to satisfy the runtime contract."""
    from backend.image_manifest import ImageManifest, ManifestError

    try:
        manifest = ImageManifest.load(path)
    except ManifestError as exc:
        raise ReleaseContractError(f"image manifest is invalid: {exc}") from exc
    return len(manifest.entries)


def _require_pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    text = str(value or "")
    if not pattern.fullmatch(text):
        raise ReleaseContractError(f"{label} is not an immutable digest/ref")
    return text


def resource_files(contract: dict, root: Path = ROOT) -> list[Path]:
    result: list[Path] = []
    for relative_name in contract.get("bundled_resources", []):
        relative = Path(str(relative_name))
        if relative.is_absolute() or ".." in relative.parts:
            raise ReleaseContractError(f"unsafe bundled resource: {relative_name!r}")
        source = (root / relative).resolve()
        if os.path.commonpath((str(root.resolve()), str(source))) != str(root.resolve()):
            raise ReleaseContractError(f"bundled resource escapes repository: {relative_name!r}")
        if source.is_dir():
            result.extend(path for path in sorted(source.rglob("*")) if path.is_file())
        elif source.is_file():
            result.append(source)
        else:
            raise ReleaseContractError(f"bundled resource is missing: {relative_name}")
    unique = {path.relative_to(root).as_posix(): path for path in result}
    return [unique[name] for name in sorted(unique)]


def verify_inputs(contract: dict | None = None, root: Path = ROOT) -> dict:
    contract = contract or load_contract(root / "release-contract.json")
    kace = contract.get("kace", {})
    bootstrap_ref = _require_pattern(kace.get("bootstrap_ref"), FULL_SHA, "bootstrap_ref")
    bootstrap_hash = _require_pattern(
        kace.get("bootstrap_sha256"), SHA256, "bootstrap_sha256"
    )
    installer_ref = _require_pattern(kace.get("installer_ref"), FULL_SHA, "installer_ref")
    installer_hash = _require_pattern(
        kace.get("installer_sha256"), SHA256, "installer_sha256"
    )
    image_count = verify_image_manifest(root / "image-manifest.json")

    bootstrap = root / "bootstrap.sh"
    if sha256_file(bootstrap) != bootstrap_hash:
        raise ReleaseContractError("bootstrap.sh does not match release contract SHA-256")
    script = bootstrap.read_text(encoding="utf-8")
    internal_ref = re.search(r'^KACE_INSTALL_REF="([0-9a-f]{40})"$', script, re.MULTILINE)
    internal_hash = re.search(
        r'^KACE_INSTALL_SHA256="([0-9a-f]{64})"$', script, re.MULTILINE
    )
    if not internal_ref or internal_ref.group(1) != installer_ref:
        raise ReleaseContractError("bootstrap installer ref differs from release contract")
    if not internal_hash or internal_hash.group(1) != installer_hash:
        raise ReleaseContractError("bootstrap installer hash differs from release contract")

    lock_path = root / "requirements.lock"
    if not lock_path.is_file():
        raise ReleaseContractError("hashed dependency lock is missing")
    lock_text = lock_path.read_text(encoding="utf-8")
    if "--hash=sha256:" not in lock_text:
        raise ReleaseContractError("dependency lock does not contain package hashes")
    for filename in ("requirements.txt", "requirements-dev.txt"):
        for line in (root / filename).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line not in lock_text:
                raise ReleaseContractError(f"{filename} is not represented exactly in requirements.lock: {line}")

    files = resource_files(contract, root)
    return {
        "bootstrap_ref": bootstrap_ref,
        "bootstrap_sha256": bootstrap_hash,
        "installer_ref": installer_ref,
        "installer_sha256": installer_hash,
        "resources": files,
        "requirements_lock_sha256": sha256_file(lock_path),
        "image_manifest_entries": image_count,
    }


def _archive_name(path: Path, root: Path = ROOT) -> str:
    return path.relative_to(root).as_posix()


def verify_bundle(artifact: Path, contract: dict | None = None, root: Path = ROOT) -> list[dict]:
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise ReleaseContractError("PyInstaller is required to inspect the packaged archive") from exc

    contract = contract or load_contract(root / "release-contract.json")
    inputs = verify_inputs(contract, root)
    try:
        archive = CArchiveReader(str(artifact))
    except Exception as exc:
        raise ReleaseContractError(f"artifact is not an inspectable PyInstaller archive: {artifact}") from exc

    inventory: list[dict] = []
    normalized_entries = {name.replace("\\", "/"): name for name in archive.toc}
    for source in inputs["resources"]:
        relative = _archive_name(source, root)
        archive_name = normalized_entries.get(relative)
        if not archive_name:
            raise ReleaseContractError(f"packaged resource is missing: {relative}")
        packaged = archive.extract(archive_name)
        expected = source.read_bytes()
        if packaged != expected:
            raise ReleaseContractError(f"packaged resource bytes differ: {relative}")
        inventory.append(
            {"path": relative, "sha256": sha256_bytes(expected), "size": len(expected)}
        )
    return inventory


def _git(*args: str, root: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def verify_rebuild(reference: Path, candidate: Path) -> str:
    reference_hash = sha256_file(reference)
    candidate_hash = sha256_file(candidate)
    if reference_hash != candidate_hash:
        raise ReleaseContractError(
            f"repeated clean build SHA-256 mismatch: {reference_hash} != {candidate_hash}"
        )
    return candidate_hash


def build_manifest(
    artifact: Path,
    *,
    allow_dirty: bool = False,
    reproducible_reference: Path | None = None,
    root: Path = ROOT,
) -> dict:
    contract = load_contract(root / "release-contract.json")
    inputs = verify_inputs(contract, root)
    inventory = verify_bundle(artifact, contract, root)
    head = _git("rev-parse", "HEAD", root=root)
    dirty = bool(_git("status", "--porcelain", "--untracked-files=all", root=root))
    if dirty and not allow_dirty:
        raise ReleaseContractError("release manifest requires a clean worktree")

    python_version = platform.python_version()
    pyinstaller_version = importlib.metadata.version("pyinstaller")
    if python_version != contract["python"]:
        raise ReleaseContractError(
            f"Python toolchain mismatch: {python_version} != {contract['python']}"
        )
    if pyinstaller_version != contract["pyinstaller"]:
        raise ReleaseContractError(
            f"PyInstaller toolchain mismatch: {pyinstaller_version} != {contract['pyinstaller']}"
        )
    commit_epoch = _git("show", "-s", "--format=%ct", "HEAD", root=root)
    expected_environment = contract.get("build_environment", {})
    if os.environ.get("PYTHONHASHSEED") != expected_environment.get("PYTHONHASHSEED"):
        raise ReleaseContractError("PYTHONHASHSEED does not match release contract")
    if os.environ.get("SOURCE_DATE_EPOCH") != commit_epoch:
        raise ReleaseContractError("SOURCE_DATE_EPOCH is not the source commit timestamp")
    repeated_build_verified = False
    if reproducible_reference is not None:
        verify_rebuild(reproducible_reference, artifact)
        repeated_build_verified = True

    return {
        "schema": "kace-studio-release-manifest/v1",
        "product": contract["product"],
        "source": {
            "repository": "https://github.com/3D-uy/KACE-studio.git",
            "commit": head,
            "dirty": dirty,
        },
        "kace": dict(contract["kace"]),
        "toolchain": {
            "python": python_version,
            "implementation": platform.python_implementation(),
            "pyinstaller": pyinstaller_version,
            "platform": platform.platform(),
            "runner_image_os": os.environ.get("ImageOS", ""),
            "runner_image_version": os.environ.get("ImageVersion", ""),
            "python_hash_seed": os.environ["PYTHONHASHSEED"],
            "source_date_epoch": commit_epoch,
        },
        "inputs": {
            "release_contract_sha256": sha256_file(root / "release-contract.json"),
            "release_tool_sha256": sha256_file(root / "scripts" / "release.py"),
            "ci_workflow_sha256": sha256_file(root / ".github" / "workflows" / "ci.yml"),
            "requirements_lock_sha256": inputs["requirements_lock_sha256"],
            "spec_sha256": sha256_file(root / contract["spec"]),
            "bundled_resources": inventory,
        },
        "artifact": {
            "name": artifact.name,
            "sha256": sha256_file(artifact),
            "size": artifact.stat().st_size,
        },
        "reproducibility": {
            "claim": "locked-inputs-and-attested-output",
            "same_environment_rebuild_verified": repeated_build_verified,
            "independent_builder_reproduced": False,
        },
    }


def write_json_atomically(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump(data, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _download_verified(url: str, destination: Path, expected_sha256: str) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "KACE-Studio-release-contract/1"})
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".download", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(request, timeout=30) as response:
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_CONTRACT_DOWNLOAD_BYTES:
                    raise ReleaseContractError("release-contract download exceeds size limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        actual = sha256_file(temporary)
        if actual != expected_sha256:
            raise ReleaseContractError(
                f"downloaded contract SHA-256 mismatch: {actual} != {expected_sha256}"
            )
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


def fetch_bootstrap(contract: dict | None = None, destination: Path | None = None) -> Path:
    contract = contract or load_contract()
    kace = contract["kace"]
    ref = _require_pattern(kace.get("bootstrap_ref"), FULL_SHA, "bootstrap_ref")
    expected = _require_pattern(kace.get("bootstrap_sha256"), SHA256, "bootstrap_sha256")
    destination = destination or ROOT / "bootstrap.sh"
    url = f"https://raw.githubusercontent.com/3D-uy/KACE/{ref}/scripts/bootstrap.sh"
    return _download_verified(url, destination, expected)


def verify_remote_installer(contract: dict | None = None) -> str:
    contract = contract or load_contract()
    kace = contract["kace"]
    ref = _require_pattern(kace.get("installer_ref"), FULL_SHA, "installer_ref")
    expected = _require_pattern(kace.get("installer_sha256"), SHA256, "installer_sha256")
    url = f"https://raw.githubusercontent.com/3D-uy/KACE/{ref}/install.sh"
    with tempfile.TemporaryDirectory(prefix="kace-studio-installer-contract-") as directory:
        _download_verified(url, Path(directory) / "install.sh", expected)
    return expected


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify-inputs")
    subparsers.add_parser("fetch-bootstrap")
    subparsers.add_parser("verify-remote-installer")
    bundle = subparsers.add_parser("verify-bundle")
    bundle.add_argument("artifact", type=Path)
    rebuild = subparsers.add_parser("verify-rebuild")
    rebuild.add_argument("reference", type=Path)
    rebuild.add_argument("candidate", type=Path)
    manifest = subparsers.add_parser("write-manifest")
    manifest.add_argument("artifact", type=Path)
    manifest.add_argument("output", type=Path)
    manifest.add_argument("--allow-dirty", action="store_true")
    manifest.add_argument("--reproducible-reference", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-inputs":
            verify_inputs()
        elif args.command == "fetch-bootstrap":
            fetch_bootstrap()
        elif args.command == "verify-remote-installer":
            verify_remote_installer()
        elif args.command == "verify-bundle":
            verify_bundle(args.artifact)
        elif args.command == "verify-rebuild":
            verify_rebuild(args.reference, args.candidate)
        elif args.command == "write-manifest":
            write_json_atomically(
                args.output,
                build_manifest(
                    args.artifact,
                    allow_dirty=args.allow_dirty,
                    reproducible_reference=args.reproducible_reference,
                ),
            )
    except (ReleaseContractError, OSError, subprocess.SubprocessError) as exc:
        print(f"Release contract failed: {exc}", file=sys.stderr)
        return 1
    print(f"Release contract passed: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
