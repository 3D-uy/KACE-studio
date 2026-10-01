"""Login -> bootstrap -> firmware events/checkpoints with unmodified app.js."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", [
    "sftp_persists_between_tabs", "first_boot_stop_lifecycle", "sftp_delayed_download_feedback", "sftp_error_and_retry_feedback",
    "progress_and_download", "checkpoint_survives_bootstrap",
    "disconnect_reconnect_rejects_delayed_results", "double_login_and_session_replacement",
    "failed_bootstrap_is_not_completion", "reconnect_restores_remote_checkpoint",
    "sftp_pending_list_survives_status",
    "robin_final_download",
    "disconnect_diagnostic_is_not_host_diagnosis",
])
def test_bootstrap_frontend_session(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the frontend regression")
    result = subprocess.run([
        node, str(ROOT / "tests/bootstrap_session_harness.cjs"),
        str(ROOT / "web/app.js"), case,
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("completed"), "The asynchronous scenario did not finish"
