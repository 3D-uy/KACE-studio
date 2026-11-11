"""The bundled candidate must carry the PC preflight bridge across sudo."""
from pathlib import Path
import copy
import hashlib
import shutil

import pytest

from scripts import release

ROOT = Path(__file__).resolve().parents[1]
SOURCE_BOOTSTRAP = ROOT.parent / "KACE" / "scripts" / "bootstrap.sh"
pytestmark = pytest.mark.skipif(
    not SOURCE_BOOTSTRAP.is_file(), reason="requires sibling KACE bootstrap source"
)


def fixture_root(tmp_path, broken):
    contract = copy.deepcopy(release.load_contract())
    for source in release.resource_files(contract, ROOT):
        destination = tmp_path / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for filename in ("image-manifest.json", "requirements.txt",
                     "requirements-dev.txt", "requirements.lock"):
        shutil.copy2(ROOT / filename, tmp_path / filename)
    # Preserve the real candidate's other bootstrap invariants and recompute
    # the digest in this explicitly synthetic contract; no real pin changes.
    script = SOURCE_BOOTSTRAP.read_text(encoding="utf-8")
    if broken:
        script = script.replace("STUDIO_PREFLIGHT_CONTEXT=0", "STUDIO_PREFLIGHT_CONTEXT_BROKEN=0", 1)
    raw = script.encode("utf-8")
    (tmp_path / "bootstrap.sh").write_bytes(raw)
    contract["kace"]["bootstrap_sha256"] = hashlib.sha256(raw).hexdigest()
    return contract


def test_pinned_candidate_without_pc_context_is_rejected(tmp_path):
    contract = fixture_root(tmp_path, broken=True)
    with pytest.raises(release.ReleaseContractError, match="Studio PC preflight"):
        release.verify_inputs(contract, tmp_path)


def test_coherent_synthetic_source_passes_input_gate(tmp_path):
    contract = fixture_root(tmp_path, broken=False)
    facts = release.verify_inputs(contract, tmp_path)
    assert facts["bootstrap_sha256"] == contract["kace"]["bootstrap_sha256"]


def test_bundle_verification_cannot_skip_preflight_gate(tmp_path):
    pytest.importorskip("PyInstaller.archive.readers")
    contract = fixture_root(tmp_path, broken=True)
    with pytest.raises(release.ReleaseContractError, match="Studio PC preflight"):
        release.verify_bundle(tmp_path / "unused.exe", contract, tmp_path)


def test_local_bootstrap_has_structural_pc_context_contract():
    release.verify_bootstrap_pc_context(SOURCE_BOOTSTRAP.read_text(encoding="utf-8"))


@pytest.mark.parametrize("missing", [
    'STUDIO_PREFLIGHT_CONTEXT=0',
    'KACE_STUDIO_PREFLIGHT_PORT+x',
    'KACE_STUDIO_PREFLIGHT_TOKEN+x',
    'KACE_STUDIO_PREFLIGHT_SESSION+x',
    'KACE_STUDIO_PREFLIGHT_CONTEXT="$STUDIO_PREFLIGHT_CONTEXT"',
    'IFS= read -r KACE_STUDIO_PREFLIGHT_PORT',
    'IFS= read -r KACE_STUDIO_PREFLIGHT_TOKEN',
    'IFS= read -r KACE_STUDIO_PREFLIGHT_SESSION',
    'export KACE_STUDIO_PREFLIGHT_PORT KACE_STUDIO_PREFLIGHT_TOKEN KACE_STUDIO_PREFLIGHT_SESSION',
    "builtin printf '%s\\n%s\\n%s\\n'",
    '"${KACE_STUDIO_PREFLIGHT_PORT:-}"',
    '"${KACE_STUDIO_PREFLIGHT_TOKEN:-}"',
    '"${KACE_STUDIO_PREFLIGHT_SESSION:-}")',
])
def test_incomplete_pc_context_contract_is_rejected(missing):
    script = SOURCE_BOOTSTRAP.read_text(encoding="utf-8")
    assert missing in script
    if missing in ('"${KACE_STUDIO_PREFLIGHT_PORT:-}"',
                   '"${KACE_STUDIO_PREFLIGHT_TOKEN:-}"'):
        before, after = script.rsplit(missing, 1)
        script = before + after
    else:
        script = script.replace(missing, "", 1)
    with pytest.raises(release.ReleaseContractError, match="Studio PC preflight"):
        release.verify_bootstrap_pc_context(script)


def test_reordered_port_and_token_reads_are_rejected():
    script = SOURCE_BOOTSTRAP.read_text(encoding="utf-8")
    first = "IFS= read -r KACE_STUDIO_PREFLIGHT_PORT"
    second = "IFS= read -r KACE_STUDIO_PREFLIGHT_TOKEN"
    script = script.replace(first, "SWAP_READ", 1).replace(second, first, 1).replace("SWAP_READ", second, 1)
    with pytest.raises(release.ReleaseContractError, match="Studio PC preflight"):
        release.verify_bootstrap_pc_context(script)


def test_commented_out_export_does_not_count():
    script = SOURCE_BOOTSTRAP.read_text(encoding="utf-8")
    export = "export KACE_STUDIO_PREFLIGHT_PORT KACE_STUDIO_PREFLIGHT_TOKEN KACE_STUDIO_PREFLIGHT_SESSION"
    with pytest.raises(release.ReleaseContractError, match="Studio PC preflight"):
        release.verify_bootstrap_pc_context(script.replace(export, "# " + export, 1))


@pytest.mark.parametrize("original,replacement", [
    ("STUDIO_PREFLIGHT_CONTEXT=1", "STUDIO_PREFLIGHT_CONTEXT=0"),
    ('"${command[@]}" < <(builtin printf', 'true < <(builtin printf'),
])
def test_disabled_context_or_discarded_frame_is_rejected(original, replacement):
    script = SOURCE_BOOTSTRAP.read_text(encoding="utf-8")
    assert script.count(original) == 1
    with pytest.raises(release.ReleaseContractError, match="Studio PC preflight"):
        release.verify_bootstrap_pc_context(script.replace(original, replacement, 1))
