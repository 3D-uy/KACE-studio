from __future__ import annotations

import json
import hashlib
import io
import re
from pathlib import Path

import pytest

from backend import resources
from scripts import release


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_resources_do_not_depend_on_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resources.bundled_path("web/index.html") == ROOT / "web" / "index.html"
    assert resources.load_release_contract()["schema"] == "kace-studio-release-contract/v1"


def test_frozen_runtime_requires_and_uses_meipass(tmp_path, monkeypatch):
    (tmp_path / "bootstrap.sh").write_bytes(b"bootstrap")
    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resources.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.resolve_bootstrap_source() == tmp_path / "bootstrap.sh"


def test_bundled_path_rejects_escape():
    with pytest.raises(resources.ResourceContractError):
        resources.bundled_path("../bootstrap.sh")


def test_source_runtime_resource_contract_is_complete():
    resources.verify_runtime_resources()


def test_release_inputs_have_one_consistent_immutable_bootstrap_contract():
    facts = release.verify_inputs()
    assert len(facts["bootstrap_ref"]) == 40
    assert len(facts["bootstrap_sha256"]) == 64
    assert len(facts["installer_ref"]) == 40
    assert len(facts["installer_sha256"]) == 64
    assert ROOT / "image-manifest.json" in facts["resources"]


def test_release_verification_rejects_unchecksummed_image_manifest(tmp_path):
    manifest = tmp_path / "image-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "kace-studio-image-manifest/v2",
                "images": [
                    {
                        "image_type": "raspios_vanilla",
                        "architecture": "64bit",
                        "version": "unsafe",
                        "url": "https://example.invalid/image.img.xz",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(release.ReleaseContractError, match="image manifest"):
        release.verify_image_manifest(manifest)


def test_ci_uses_locked_inputs_and_immutable_actions():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements.lock" in workflow
    assert "python scripts/release.py verify-remote-installer" in workflow
    assert "windows-latest" not in workflow
    assert "ubuntu-latest" not in workflow
    assert 'python-version: ["3.11.9", "3.12.10"]' in workflow
    assert 'python-version: "3.12.10"' in workflow
    action_refs = re.findall(r"uses:\s*[^@\s]+@([^\s]+)", workflow)
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
    assert "workflow_dispatch:" in workflow
    assert "bash -n bootstrap.sh" in workflow
    assert "shellcheck --severity=warning bootstrap.sh" in workflow
    assert "independent-windows-builder:" in workflow
    assert "python scripts/release.py verify-release-gates" in workflow


def test_windows_ci_fails_closed_on_native_command_errors():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert workflow.count("$PSNativeCommandUseErrorActionPreference = $true") >= 5
    assert "cat pytest_run.log" not in workflow
    assert "python -m pytest -v --junitxml=pytest-results.xml" in workflow


def test_reproducible_build_excludes_host_windows_runtime_and_normalizes_text():
    spec = (ROOT / "main.spec").read_text(encoding="utf-8")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "HOST_WINDOWS_RUNTIME_PREFIXES" in spec
    assert "api-ms-win-" in spec
    assert "ucrtbase.dll" in spec
    assert "a.binaries =" in spec
    for pattern in ("*.json", "*.js", "*.css", "*.html"):
        assert f"{pattern} text eol=lf" in attributes


def test_spec_bundles_every_runtime_contract_root():
    spec = (ROOT / "main.spec").read_text(encoding="utf-8")
    for source in ("web", "bootstrap.sh", "image-manifest.json", "release-contract.json"):
        assert repr(source) in spec
    assert "version=version_info" in spec
    assert "contract['windows_metadata']" in spec


def test_rebuild_verification_rejects_different_artifacts(tmp_path):
    reference = tmp_path / "first.exe"
    candidate = tmp_path / "second.exe"
    reference.write_bytes(b"first")
    candidate.write_bytes(b"second")
    with pytest.raises(release.ReleaseContractError, match="repeated clean build"):
        release.verify_rebuild(reference, candidate)


def test_release_signature_gate_rejects_unsigned_wrong_or_untimestamped(monkeypatch):
    expected = "a" * 64
    cases = (
        ({"status": "NotSigned", "certificate_sha256": "", "timestamp_present": False}, "not valid"),
        ({"status": "Valid", "certificate_sha256": "b" * 64, "timestamp_present": True}, "certificate"),
        ({"status": "Valid", "certificate_sha256": expected, "timestamp_present": False}, "timestamp"),
    )
    for evidence, message in cases:
        monkeypatch.setattr(release, "inspect_authenticode_signature", lambda _artifact, value=evidence: value)
        with pytest.raises(release.ReleaseContractError, match=message):
            release.verify_authenticode_signature(Path("artifact.exe"), expected)


def test_independent_attestation_is_bound_to_source_and_artifact(tmp_path, monkeypatch):
    head = "a" * 40
    artifact_hash = "b" * 64
    primary = tmp_path / "primary.json"
    attestation = tmp_path / "independent.json"
    primary.write_text(
        json.dumps(
            {
                "schema": "kace-studio-release-manifest/v1",
                "source": {"commit": head},
                "artifact": {"sha256": artifact_hash},
                "reproducibility": {"same_environment_rebuild_verified": True},
            }
        ),
        encoding="utf-8",
    )
    attestation.write_text(
        json.dumps(
            {
                "schema": "kace-studio-independent-build-attestation/v1",
                "source_commit": head,
                "artifact_sha256": artifact_hash,
                "independent_builder": {"github_job": "independent-windows-builder"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "_git", lambda *_args, **_kwargs: head)
    result = release.attach_independent_attestation(primary, attestation, root=tmp_path)
    assert result["reproducibility"]["independent_builder_reproduced"] is True
    assert result["reproducibility"]["independent_attestation_sha256"] == release.sha256_file(attestation)

    changed = json.loads(attestation.read_text(encoding="utf-8"))
    changed["artifact_sha256"] = "c" * 64
    attestation.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(release.ReleaseContractError, match="different artifact"):
        release.attach_independent_attestation(primary, attestation, root=tmp_path)


def test_verified_contract_download_is_atomic(tmp_path, monkeypatch):
    payload = b"immutable remote bytes"
    destination = tmp_path / "bootstrap.sh"
    destination.write_bytes(b"previous")
    monkeypatch.setattr(release.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    release._download_verified(
        "https://example.invalid/bootstrap.sh",
        destination,
        hashlib.sha256(payload).hexdigest(),
    )
    assert destination.read_bytes() == payload
    assert not list(tmp_path.glob("*.download"))


def test_verified_contract_download_preserves_destination_on_hash_failure(tmp_path, monkeypatch):
    destination = tmp_path / "install.sh"
    destination.write_bytes(b"previous")
    monkeypatch.setattr(
        release.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"tampered")
    )
    with pytest.raises(release.ReleaseContractError, match="SHA-256 mismatch"):
        release._download_verified(
            "https://example.invalid/install.sh", destination, "0" * 64
        )
    assert destination.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.download"))


def test_release_input_verification_rejects_internal_installer_drift(tmp_path):
    for name in (
        "release-contract.json",
        "requirements.lock",
        "requirements.txt",
        "requirements-dev.txt",
        "bootstrap.sh",
        "image-manifest.json",
    ):
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("ok", encoding="utf-8")
    contract_path = tmp_path / "release-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["bundled_resources"] = [
        "bootstrap.sh", "image-manifest.json", "release-contract.json", "web"
    ]
    contract["kace"]["installer_ref"] = "f" * 40
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(release.ReleaseContractError, match="installer ref"):
        release.verify_inputs(contract, tmp_path)


@pytest.mark.skipif(not (ROOT / "dist" / "KACE-studio.exe").is_file(), reason="build not present")
def test_built_archive_contains_exact_contract_resources():
    inventory = release.verify_bundle(ROOT / "dist" / "KACE-studio.exe")
    assert {entry["path"] for entry in inventory} == {
        path.relative_to(ROOT).as_posix() for path in release.resource_files(release.load_contract())
    }
    metadata = release.verify_windows_metadata(ROOT / "dist" / "KACE-studio.exe")
    assert metadata == release.load_contract()["windows_metadata"]
