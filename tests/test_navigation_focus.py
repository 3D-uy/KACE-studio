"""Production Next routing with a focus-observable DOM model (not layout)."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", [
    "next_moves_focus", "skip_disabled_or_hidden", "skip_visibility_hidden",
    "heading_fallback", "sidebar_and_background_keep_focus", "error_focus_wins",
])
def test_explicit_next_navigation_focus(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend execution")
    result = subprocess.run([
        node, str(ROOT / "tests/navigation_focus_harness.cjs"),
        str(ROOT / "web/app.js"), str(ROOT / "web/index.html"), case,
    ], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("completed")
