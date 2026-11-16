"""A failed/omitted dependency must fail the required build check, not skip it."""
from pathlib import Path
import os
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATES = ("success", "failure", "cancelled", "skipped", "")


def build_gate():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = workflow.split("  build-windows:\n", 1)[1].split("  independent-windows-builder:", 1)[0]
    header, steps = job.split("    steps:\n", 1)
    assert "    name: Build Windows Executable\n" in header
    assert "    needs: [test, preflight-integration]\n" in header
    assert "    if: ${{ always() }}\n" in header
    gate, rest = steps.split("    - name: Checkout Code\n", 1)
    assert "SOURCE_TEST_RESULT: ${{ needs.test.result }}" in gate
    assert "PREFLIGHT_RESULT: ${{ needs.preflight-integration.result }}" in gate
    assert "      shell: pwsh\n" in gate
    assert "if:" not in gate and "continue-on-error" not in job
    assert "always()" not in rest
    command = gate.split("      run: |\n", 1)[1].rstrip()
    return re.sub(r"^        ", "", command, flags=re.MULTILINE)


def test_required_build_runs_dependency_guard_before_checkout():
    assert build_gate()


@pytest.mark.parametrize("source_result", STATES)
@pytest.mark.parametrize("preflight_result", STATES)
def test_required_build_rejects_every_non_success_dependency(source_result, preflight_result):
    shell = shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is required to execute the Windows CI guard")
    env = dict(os.environ, SOURCE_TEST_RESULT=source_result, PREFLIGHT_RESULT=preflight_result)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", build_gate()],
                            env=env, capture_output=True, text=True, timeout=20)
    assert (result.returncode == 0) == (source_result == preflight_result == "success")
    if result.returncode:
        assert "must both succeed" in result.stderr
