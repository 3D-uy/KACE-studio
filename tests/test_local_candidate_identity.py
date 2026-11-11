"""Verify the actual sibling Git objects, without substituting a fixture."""
import hashlib
from pathlib import Path
import subprocess

import pytest

from backend.resources import load_release_contract
from scripts.release import verify_local_candidate


def test_sibling_candidate_contains_complete_sparse_runtime_and_bootstrap():
    root = Path(__file__).resolve().parents[1]
    source = root.parent / "KACE"
    if not (source / ".git").exists():
        pytest.skip("sibling KACE Git checkout is not available")
    contract = load_release_contract()
    kace = contract["kace"]
    runtime = kace["candidate_ref"]
    scopes = ("core", "firmware", "data", "templates", "kace.py", "VERSION",
              "requirements.txt", "requirements-ssh.txt", "scripts/cc_wrapper.py",
              "scripts/board_contract_cc_wrapper.py", "install.sh")
    paths = subprocess.check_output(
        ["git", "-C", str(source), "ls-tree", "-r", "--name-only", runtime, "--", *scopes],
        text=True,
    ).splitlines()
    assert set(paths) == set(kace["required_runtime_files"])
    verify_local_candidate(source, runtime, contract)
    bootstrap = subprocess.check_output(
        ["git", "-C", str(source), "show", kace["bootstrap_ref"] + ":scripts/bootstrap.sh"]
    )
    assert hashlib.sha256(bootstrap).hexdigest() == kace["bootstrap_sha256"]
    assert bootstrap == (root / "bootstrap.sh").read_bytes()
