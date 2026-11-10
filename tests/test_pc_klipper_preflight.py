"""The desktop bundles the same Docker runner and fails closed on stale inputs."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from backend.klipper_preflight import DockerPreflight, FILES, verify_preflight_resources
from scripts.sync_preflight import synchronize

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def backend(tmp_path):
    resources = tmp_path / "resources"
    shutil.copytree(ROOT / "preflight", resources)
    return DockerPreflight(root=resources, receipt_path=tmp_path / "receipt.json")


def test_bundled_runner_matches_authoritative_kace():
    source = ROOT.parent / "KACE"
    if not source.is_dir():
        pytest.skip("independent Studio checkout has no KACE source to compare")
    synchronize(source, ROOT / "preflight", check=True)


@pytest.mark.parametrize("filename", FILES)
def test_tampered_resource_rejected_before_loading(tmp_path, filename):
    resources = tmp_path / "resources"
    shutil.copytree(ROOT / "preflight", resources)
    (resources / filename).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="identity mismatch"):
        DockerPreflight(root=resources, receipt_path=tmp_path / "receipt.json")


def test_prepare_missing_docker_is_actionable(backend, monkeypatch):
    monkeypatch.setattr(backend.engine.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="this PC"):
        backend.prepare()


def test_prepare_reuses_only_exact_existing_image(backend, monkeypatch):
    receipt = {"schema": 1, "pin": backend.identity["pin"],
               "image_id": "sha256:" + "a" * 64,
               "dockerfile_sha256": backend.identity["dockerfile_sha256"],
               "worker_sha256": backend.identity["worker_sha256"]}
    backend.receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(backend.engine.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(backend.engine, "_run", lambda *_: SimpleNamespace(returncode=0, stdout=receipt["image_id"] + "\n"))
    monkeypatch.setattr(backend.engine, "provision", lambda **_: pytest.fail("unexpected build"))
    assert backend.prepare() == receipt


@pytest.mark.parametrize("old", [{}, {"schema": 1, "pin": "0" * 40}, {"image_id": "mutable:latest"}])
def test_prepare_rebuilds_stale_receipt(backend, monkeypatch, old):
    backend.receipt_path.write_text(json.dumps(old))
    monkeypatch.setattr(backend.engine.shutil, "which", lambda _: "docker")
    calls = []
    monkeypatch.setattr(backend.engine, "provision", lambda **kw: calls.append(kw) or {"built": True})
    assert backend.prepare() == {"built": True}
    assert calls[0]["pin"] == backend.identity["pin"]
    assert calls[0]["receipt_path"] == backend.receipt_path


def test_changed_manifest_after_session_is_unverified(backend):
    manifest = json.loads((backend.root / "manifest.json").read_text())
    manifest["pin"] = "0" * 40
    (backend.root / "manifest.json").write_text(json.dumps(manifest))
    result = backend.validate_files({"printer.cfg": b"# cfg\n"})
    assert result["status"] == "unverified"
    assert "changed" in result["detail"]


def test_validate_delegates_exact_bytes_without_regeneration(backend, monkeypatch):
    files = {"printer.cfg": b"[include a.cfg]\n#*# SAVE_CONFIG\n", "a.cfg": b"# explicit bytes\r\n"}
    seen = []
    def validate(actual, **options):
        seen.append((actual, options))
        return {"status": "valid", "detail": "bounded", "tree_digest": backend.engine._digest(actual)}
    monkeypatch.setattr(backend.engine, "validate_files", validate)
    result = backend.validate_files(files)
    assert result["status"] == "valid" and seen[0][0] is files
    assert seen[0][1]["pin"] == backend.identity["pin"]


def test_changed_resources_during_loader_cannot_authorize(backend, monkeypatch):
    def validate(files, **options):
        (backend.root / FILES[2]).write_bytes(b"changed during Docker")
        return {"status": "valid", "detail": "would accept", "tree_digest": backend.engine._digest(files)}
    monkeypatch.setattr(backend.engine, "validate_files", validate)
    result = backend.validate_files({"printer.cfg": b"# cfg"})
    assert result["status"] == "unverified" and not result["tree_digest"]


@pytest.mark.parametrize("name", ["../outside.cfg", "a/../../outside.cfg", "C:/outside.cfg", "a\\b.cfg", "/abs.cfg", "a/./x.cfg"])
def test_engine_rejects_escape_before_docker(backend, monkeypatch, name):
    monkeypatch.setattr(backend.engine.shutil, "which", lambda _: pytest.fail("must reject before docker"))
    result = backend.validate_files({"printer.cfg": b"# cfg", name: b"# never stage"})
    assert result["status"] == "unverified" and not result["tree_digest"]


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows path normalization")
@pytest.mark.parametrize("name", [".. /outside.cfg", "CON", "a.", "printer.CFG"])
def test_windows_aliases_are_unverified_before_write(backend, monkeypatch, name):
    monkeypatch.setattr(backend.engine.shutil, "which", lambda _: pytest.fail("must reject before docker"))
    assert backend.validate_files({"printer.cfg": b"# cfg", name: b"# alias"})["status"] == "unverified"


def test_source_and_frozen_resolve_verified_bundled_resources(monkeypatch, tmp_path):
    from backend import resources
    shutil.copytree(ROOT / "preflight", tmp_path / "preflight")
    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resources.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert verify_preflight_resources()["files"] == verify_preflight_resources(ROOT / "preflight")["files"]


def test_loading_does_not_write_inside_packaged_resources(backend):
    assert not (backend.root / "__pycache__").exists()
    assert set(path.name for path in backend.root.iterdir()) == {*FILES, "manifest.json"}
