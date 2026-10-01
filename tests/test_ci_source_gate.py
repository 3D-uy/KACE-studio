"""Source CI rejects substituted bootstrap bytes and missing frontend execution."""
from pathlib import Path
import xml.etree.ElementTree as ET
from unittest.mock import patch

import pytest

from scripts.check_ci_source import (
    FRONTEND_CASES, FRONTEND_MODULES, check_frontend_report, verify_bootstrap,
)


def report(path, outcome=None, omit=None):
    root = ET.Element("testsuite")
    identities = {module + ".test_example" for module in FRONTEND_MODULES} | FRONTEND_CASES
    for identity in sorted(identities):
        if identity == omit:
            continue
        module, name = identity.rsplit(".", 1)
        case = ET.SubElement(root, "testcase", classname="tests." + module, name=name)
        if outcome and identity == sorted(identities)[0]:
            ET.SubElement(case, outcome, message="coverage unavailable")
    ET.ElementTree(root).write(path)
    return len(identities)


def test_current_commit_bootstrap_required(tmp_path):
    (tmp_path / "bootstrap.sh").write_bytes(b"current commit")
    with patch("scripts.check_ci_source.subprocess.check_output", return_value=b"current commit"):
        assert len(verify_bootstrap(tmp_path)) == 64
        (tmp_path / "bootstrap.sh").write_bytes(b"old release")
        with pytest.raises(RuntimeError, match="checked-out commit"):
            verify_bootstrap(tmp_path)


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error"])
def test_frontend_non_success_fails(tmp_path, outcome):
    path = tmp_path / "results.xml"
    report(path, outcome)
    with pytest.raises(RuntimeError, match="unsuccessful"):
        check_frontend_report(path)


def test_missing_frontend_collection_fails(tmp_path):
    path = tmp_path / "results.xml"
    report(path, omit=sorted(FRONTEND_CASES)[0])
    with pytest.raises(RuntimeError, match="missing"):
        check_frontend_report(path)


def test_complete_frontend_passes(tmp_path):
    path = tmp_path / "results.xml"
    count = report(path)
    assert check_frontend_report(path) == count


def test_workflow_separates_source_from_release_fetch():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    source = workflow.split("  test:", 1)[1].split("  build-windows:", 1)[0]
    release = workflow.split("  build-windows:", 1)[1]
    assert "fetch-bootstrap" not in source
    assert "verify-inputs" not in source
    assert "check_ci_source.py" in source
    assert 'node-version: "24.19.0"' in source
    assert "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020" in source
    assert "python scripts/release.py fetch-bootstrap" in release
    assert "--junit" in source
    assert "!cancelled()" in source
    assert "continue-on-error" not in source
