"""Production frontend: pause, continue, cancellation and relay visibility."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("language", ["en", "es", "pt"])
def test_discovery_and_relay_controls(language):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend regression tests")
    result = subprocess.run([node, str(ROOT / "tests/discovery_controls_harness.cjs"),
                             str(ROOT / "web/app.js"), language],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Discovery and relay controls completed" in result.stdout
