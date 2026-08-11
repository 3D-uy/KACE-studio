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
import shutil
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
    metadata = contract.get("windows_metadata")
    if not isinstance(metadata, dict):
        raise ReleaseContractError("windows_metadata is missing")
    numeric_version = metadata.get("numeric_version")
    if (
        not isinstance(numeric_version, list)
        or len(numeric_version) != 4
        or any(not isinstance(item, int) or item < 0 or item > 65535 for item in numeric_version)
    ):
        raise ReleaseContractError("windows_metadata.numeric_version is invalid")
    metadata_keys = (
        "CompanyName",
        "FileDescription",
        "FileVersion",
        "InternalName",
        "OriginalFilename",
        "ProductName",
        "ProductVersion",
    )
    if any(not isinstance(metadata.get(key), str) or not metadata[key] for key in metadata_keys):
        raise ReleaseContractError("windows_metadata strings are incomplete")
    if metadata["OriginalFilename"] != contract.get("artifact"):
        raise ReleaseContractError("Windows OriginalFilename differs from artifact")
    if metadata["ProductName"] != contract.get("product"):
        raise ReleaseContractError("Windows ProductName differs from product")
    if metadata["ProductVersion"] != contract.get("version"):
        raise ReleaseContractError("Windows ProductVersion differs from version")
    signing = contract.get("signing")
    if not isinstance(signing, dict) or signing.get("required_for_release") is not True:
        raise ReleaseContractError("release signing gate is not mandatory")
    if signing.get("digest_algorithm") != "sha256":
        raise ReleaseContractError("release signing digest must be SHA-256")
    if signing.get("timestamp_required") is not True:
        raise ReleaseContractError("release signatures must be timestamped")
    if signing.get("timestamp_url") != "https://timestamp.digicert.com":
        raise ReleaseContractError("release timestamp service is not pinned")
    if signing.get("expected_certificate_sha256_env") != "KACE_SIGNING_CERT_SHA256":
        raise ReleaseContractError("release signer identity environment is invalid")
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


def _decode_pe_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict").rstrip("\x00")
    return str(value).rstrip("\x00")


def verify_windows_metadata(artifact: Path, contract: dict | None = None) -> dict:
    """Verify the PE version resource against the release contract."""
    try:
        import pefile
    except ImportError as exc:
        raise ReleaseContractError("pefile is required to inspect Windows metadata") from exc

    contract = contract or load_contract()
    expected = contract["windows_metadata"]
    try:
        pe = pefile.PE(str(artifact), fast_load=False)
    except Exception as exc:
        raise ReleaseContractError(f"artifact is not a readable PE file: {artifact}") from exc
    try:
        fixed_entries = getattr(pe, "VS_FIXEDFILEINFO", None) or []
        if len(fixed_entries) != 1:
            raise ReleaseContractError("artifact has no unambiguous fixed version metadata")
        fixed = fixed_entries[0]
        numeric_version = [
            fixed.FileVersionMS >> 16,
            fixed.FileVersionMS & 0xFFFF,
            fixed.FileVersionLS >> 16,
            fixed.FileVersionLS & 0xFFFF,
        ]
        strings: dict[str, str] = {}
        for group in getattr(pe, "FileInfo", None) or []:
            for entry in group:
                if _decode_pe_text(entry.Key) != "StringFileInfo":
                    continue
                for table in entry.StringTable:
                    for key, value in table.entries.items():
                        strings[_decode_pe_text(key)] = _decode_pe_text(value)
    finally:
        pe.close()

    if numeric_version != expected["numeric_version"]:
        raise ReleaseContractError(
            f"Windows numeric version mismatch: {numeric_version} != {expected['numeric_version']}"
        )
    for key, value in expected.items():
        if key == "numeric_version":
            continue
        if strings.get(key) != value:
            raise ReleaseContractError(
                f"Windows metadata mismatch for {key}: {strings.get(key)!r} != {value!r}"
            )
    return {"numeric_version": numeric_version, **{key: strings[key] for key in expected if key != "numeric_version"}}


def inspect_authenticode_signature(artifact: Path) -> dict:
    """Return Authenticode evidence without treating an unsigned artifact as valid."""
    if platform.system() != "Windows":
        raise ReleaseContractError("Authenticode verification requires Windows")
    script = r"""
$ErrorActionPreference = 'Stop'
$signature = Get-AuthenticodeSignature -LiteralPath $env:KACE_AUTHENTICODE_TARGET
$certificateSha256 = ''
if ($null -ne $signature.SignerCertificate) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $certificateSha256 = ([System.BitConverter]::ToString($sha.ComputeHash($signature.SignerCertificate.RawData))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}
[ordered]@{
    status = [string]$signature.Status
    status_message = [string]$signature.StatusMessage
    certificate_sha256 = $certificateSha256
    subject = $(if ($null -eq $signature.SignerCertificate) { '' } else { [string]$signature.SignerCertificate.Subject })
    timestamp_present = ($null -ne $signature.TimeStamperCertificate)
    timestamp_subject = $(if ($null -eq $signature.TimeStamperCertificate) { '' } else { [string]$signature.TimeStamperCertificate.Subject })
} | ConvertTo-Json -Compress
"""
    try:
        environment = os.environ.copy()
        environment["KACE_AUTHENTICODE_TARGET"] = str(artifact.resolve())
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if powershell is None:
            raise ReleaseContractError("PowerShell is required for Authenticode verification")
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
        )
        evidence = json.loads(result.stdout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise ReleaseContractError(
            f"Authenticode evidence command failed{suffix}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseContractError("Authenticode evidence could not be read") from exc
    if not isinstance(evidence, dict):
        raise ReleaseContractError("Authenticode evidence is malformed")
    return evidence


def verify_authenticode_signature(
    artifact: Path,
    expected_certificate_sha256: str,
    contract: dict | None = None,
) -> dict:
    contract = contract or load_contract()
    expected = _require_pattern(
        expected_certificate_sha256, SHA256, "expected signing certificate SHA-256"
    )
    evidence = inspect_authenticode_signature(artifact)
    if evidence.get("status") != "Valid":
        raise ReleaseContractError(
            f"Authenticode signature is not valid: {evidence.get('status', 'unknown')}"
        )
    if evidence.get("certificate_sha256") != expected:
        raise ReleaseContractError("Authenticode signer certificate SHA-256 mismatch")
    if contract["signing"]["timestamp_required"] and evidence.get("timestamp_present") is not True:
        raise ReleaseContractError("Authenticode signature has no trusted timestamp evidence")
    return evidence


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
    require_signature: bool = False,
    expected_certificate_sha256: str | None = None,
    root: Path = ROOT,
) -> dict:
    contract = load_contract(root / "release-contract.json")
    inputs = verify_inputs(contract, root)
    inventory = verify_bundle(artifact, contract, root)
    windows_metadata = verify_windows_metadata(artifact, contract)
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
    if require_signature:
        if expected_certificate_sha256 is None:
            raise ReleaseContractError("release signature gate requires an expected certificate SHA-256")
        signature = verify_authenticode_signature(
            artifact, expected_certificate_sha256, contract
        )
        signature["verified"] = True
    elif platform.system() == "Windows":
        signature = inspect_authenticode_signature(artifact)
        signature["verified"] = False
    else:
        signature = {"status": "NotChecked", "verified": False}

    return {
        "schema": "kace-studio-release-manifest/v1",
        "product": contract["product"],
        "version": contract["version"],
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
            "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "github_job": os.environ.get("GITHUB_JOB", ""),
            "runner_name": os.environ.get("RUNNER_NAME", ""),
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
            "windows_metadata": windows_metadata,
            "authenticode": signature,
        },
        "reproducibility": {
            "claim": "locked-inputs-and-attested-output",
            "same_environment_rebuild_verified": repeated_build_verified,
            "independent_builder_reproduced": False,
        },
    }


def _load_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ReleaseContractError(f"{label} is malformed")
    return value


def build_independent_attestation(
    primary_manifest_path: Path,
    primary_artifact: Path,
    independent_artifact: Path,
    *,
    root: Path = ROOT,
) -> dict:
    manifest = _load_json_object(primary_manifest_path, "primary build manifest")
    if manifest.get("schema") != "kace-studio-release-manifest/v1":
        raise ReleaseContractError("primary build manifest schema is unsupported")
    head = _git("rev-parse", "HEAD", root=root)
    if manifest.get("source", {}).get("commit") != head:
        raise ReleaseContractError("primary build manifest source differs from checkout")
    primary_hash = sha256_file(primary_artifact)
    if manifest.get("artifact", {}).get("sha256") != primary_hash:
        raise ReleaseContractError("primary artifact differs from its manifest")
    if manifest.get("reproducibility", {}).get("same_environment_rebuild_verified") is not True:
        raise ReleaseContractError("primary build lacks same-environment reproduction evidence")
    verified_hash = verify_rebuild(primary_artifact, independent_artifact)
    verify_bundle(independent_artifact, root=root)
    verify_windows_metadata(independent_artifact)
    return {
        "schema": "kace-studio-independent-build-attestation/v1",
        "source_commit": head,
        "artifact_sha256": verified_hash,
        "primary_builder": dict(manifest.get("toolchain", {})),
        "independent_builder": {
            "platform": platform.platform(),
            "runner_image_os": os.environ.get("ImageOS", ""),
            "runner_image_version": os.environ.get("ImageVersion", ""),
            "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "github_job": os.environ.get("GITHUB_JOB", ""),
            "runner_name": os.environ.get("RUNNER_NAME", ""),
        },
    }


def attach_independent_attestation(
    primary_manifest_path: Path,
    attestation_path: Path,
    *,
    root: Path = ROOT,
) -> dict:
    manifest = _load_json_object(primary_manifest_path, "primary build manifest")
    attestation = _load_json_object(attestation_path, "independent build attestation")
    if attestation.get("schema") != "kace-studio-independent-build-attestation/v1":
        raise ReleaseContractError("independent build attestation schema is unsupported")
    head = _git("rev-parse", "HEAD", root=root)
    if manifest.get("source", {}).get("commit") != head or attestation.get("source_commit") != head:
        raise ReleaseContractError("independent build evidence source differs from checkout")
    artifact_hash = manifest.get("artifact", {}).get("sha256")
    if attestation.get("artifact_sha256") != artifact_hash:
        raise ReleaseContractError("independent build evidence identifies a different artifact")
    reproducibility = manifest.get("reproducibility")
    if not isinstance(reproducibility, dict):
        raise ReleaseContractError("primary reproducibility evidence is malformed")
    result = json.loads(json.dumps(manifest))
    result["reproducibility"]["independent_builder_reproduced"] = True
    result["reproducibility"]["independent_attestation_sha256"] = sha256_file(
        attestation_path
    )
    result["reproducibility"]["independent_builder"] = attestation[
        "independent_builder"
    ]
    return result


def finalize_signed_manifest(
    unsigned_manifest_path: Path,
    signed_artifact: Path,
    attestation_path: Path,
    expected_certificate_sha256: str,
    *,
    root: Path = ROOT,
) -> dict:
    contract = load_contract(root / "release-contract.json")
    verify_inputs(contract, root)
    verify_bundle(signed_artifact, contract, root)
    metadata = verify_windows_metadata(signed_artifact, contract)
    signature = verify_authenticode_signature(
        signed_artifact, expected_certificate_sha256, contract
    )
    signature["verified"] = True
    manifest = _load_json_object(unsigned_manifest_path, "unsigned build manifest")
    attestation = _load_json_object(attestation_path, "independent build attestation")
    head = _git("rev-parse", "HEAD", root=root)
    if manifest.get("source", {}).get("commit") != head or attestation.get("source_commit") != head:
        raise ReleaseContractError("signed release evidence source differs from checkout")
    unsigned_hash = manifest.get("artifact", {}).get("sha256")
    if attestation.get("artifact_sha256") != unsigned_hash:
        raise ReleaseContractError("independent builder did not reproduce the unsigned artifact")
    if manifest.get("reproducibility", {}).get("same_environment_rebuild_verified") is not True:
        raise ReleaseContractError("unsigned artifact lacks double-build evidence")
    result = json.loads(json.dumps(manifest))
    result["artifact"] = {
        "name": signed_artifact.name,
        "sha256": sha256_file(signed_artifact),
        "size": signed_artifact.stat().st_size,
        "windows_metadata": metadata,
        "authenticode": signature,
    }
    result["reproducibility"]["unsigned_artifact_sha256"] = unsigned_hash
    result["reproducibility"]["signature_changes_artifact_bytes"] = True
    result["reproducibility"]["independent_builder_reproduced"] = True
    result["reproducibility"]["independent_attestation_sha256"] = sha256_file(
        attestation_path
    )
    result["reproducibility"]["independent_builder"] = attestation[
        "independent_builder"
    ]
    return result


def verify_release_gates(
    artifact: Path,
    manifest_path: Path,
    attestation_path: Path,
    expected_certificate_sha256: str,
    *,
    root: Path = ROOT,
) -> dict:
    """Fail closed unless metadata, provenance, independent build, and signing pass."""
    contract = load_contract(root / "release-contract.json")
    verify_inputs(contract, root)
    verify_bundle(artifact, contract, root)
    verify_windows_metadata(artifact, contract)
    signature = verify_authenticode_signature(
        artifact, expected_certificate_sha256, contract
    )
    manifest = _load_json_object(manifest_path, "release manifest")
    attestation = _load_json_object(attestation_path, "independent build attestation")
    head = _git("rev-parse", "HEAD", root=root)
    if bool(_git("status", "--porcelain", "--untracked-files=all", root=root)):
        raise ReleaseContractError("release gates require a clean worktree")
    if manifest.get("source", {}).get("commit") != head:
        raise ReleaseContractError("release manifest source differs from checkout")
    if manifest.get("artifact", {}).get("sha256") != sha256_file(artifact):
        raise ReleaseContractError("release artifact differs from manifest")
    if manifest.get("artifact", {}).get("authenticode", {}).get("verified") is not True:
        raise ReleaseContractError("release manifest does not attest a verified signature")
    reproducibility = manifest.get("reproducibility", {})
    if reproducibility.get("same_environment_rebuild_verified") is not True:
        raise ReleaseContractError("same-environment double build was not verified")
    if reproducibility.get("independent_builder_reproduced") is not True:
        raise ReleaseContractError("independent builder reproduction was not verified")
    if reproducibility.get("independent_attestation_sha256") != sha256_file(attestation_path):
        raise ReleaseContractError("independent attestation hash differs from manifest")
    reproduced_hash = reproducibility.get(
        "unsigned_artifact_sha256", sha256_file(artifact)
    )
    if attestation.get("artifact_sha256") != reproduced_hash:
        raise ReleaseContractError("independent builder reproduced a different unsigned artifact")
    return signature


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
    metadata = subparsers.add_parser("verify-metadata")
    metadata.add_argument("artifact", type=Path)
    signature = subparsers.add_parser("verify-signature")
    signature.add_argument("artifact", type=Path)
    signature.add_argument("--expected-certificate-sha256", required=True)
    rebuild = subparsers.add_parser("verify-rebuild")
    rebuild.add_argument("reference", type=Path)
    rebuild.add_argument("candidate", type=Path)
    manifest = subparsers.add_parser("write-manifest")
    manifest.add_argument("artifact", type=Path)
    manifest.add_argument("output", type=Path)
    manifest.add_argument("--allow-dirty", action="store_true")
    manifest.add_argument("--reproducible-reference", type=Path)
    manifest.add_argument("--require-signature", action="store_true")
    manifest.add_argument("--expected-certificate-sha256")
    independent = subparsers.add_parser("write-independent-attestation")
    independent.add_argument("primary_manifest", type=Path)
    independent.add_argument("primary_artifact", type=Path)
    independent.add_argument("independent_artifact", type=Path)
    independent.add_argument("output", type=Path)
    attach = subparsers.add_parser("attach-independent-attestation")
    attach.add_argument("primary_manifest", type=Path)
    attach.add_argument("attestation", type=Path)
    attach.add_argument("output", type=Path)
    signed = subparsers.add_parser("finalize-signed-manifest")
    signed.add_argument("unsigned_manifest", type=Path)
    signed.add_argument("signed_artifact", type=Path)
    signed.add_argument("attestation", type=Path)
    signed.add_argument("output", type=Path)
    signed.add_argument("--expected-certificate-sha256", required=True)
    gates = subparsers.add_parser("verify-release-gates")
    gates.add_argument("artifact", type=Path)
    gates.add_argument("manifest", type=Path)
    gates.add_argument("attestation", type=Path)
    gates.add_argument("--expected-certificate-sha256", required=True)
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
        elif args.command == "verify-metadata":
            verify_windows_metadata(args.artifact)
        elif args.command == "verify-signature":
            verify_authenticode_signature(
                args.artifact, args.expected_certificate_sha256
            )
        elif args.command == "verify-rebuild":
            verify_rebuild(args.reference, args.candidate)
        elif args.command == "write-manifest":
            write_json_atomically(
                args.output,
                build_manifest(
                    args.artifact,
                    allow_dirty=args.allow_dirty,
                    reproducible_reference=args.reproducible_reference,
                    require_signature=args.require_signature,
                    expected_certificate_sha256=args.expected_certificate_sha256,
                ),
            )
        elif args.command == "write-independent-attestation":
            write_json_atomically(
                args.output,
                build_independent_attestation(
                    args.primary_manifest,
                    args.primary_artifact,
                    args.independent_artifact,
                ),
            )
        elif args.command == "attach-independent-attestation":
            write_json_atomically(
                args.output,
                attach_independent_attestation(
                    args.primary_manifest, args.attestation
                ),
            )
        elif args.command == "finalize-signed-manifest":
            write_json_atomically(
                args.output,
                finalize_signed_manifest(
                    args.unsigned_manifest,
                    args.signed_artifact,
                    args.attestation,
                    args.expected_certificate_sha256,
                ),
            )
        elif args.command == "verify-release-gates":
            verify_release_gates(
                args.artifact,
                args.manifest,
                args.attestation,
                args.expected_certificate_sha256,
            )
    except (ReleaseContractError, OSError, subprocess.SubprocessError) as exc:
        print(f"Release contract failed: {exc}", file=sys.stderr)
        return 1
    print(f"Release contract passed: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
