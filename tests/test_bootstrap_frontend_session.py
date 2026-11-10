"""Login -> bootstrap -> firmware events/checkpoints with unmodified app.js."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", [
    "host_key_confirmation",
    "sftp_persists_between_tabs", "first_boot_stop_lifecycle", "sftp_delayed_download_feedback", "sftp_error_and_retry_feedback",
    "progress_and_download", "checkpoint_survives_bootstrap",
    "disconnect_reconnect_rejects_delayed_results", "double_login_and_session_replacement",
    "failed_bootstrap_is_not_completion", "reconnect_restores_remote_checkpoint",
    "sftp_pending_list_survives_status",
    "robin_final_download",
    "disconnect_diagnostic_is_not_host_diagnosis",
    "recoverable_disconnect_invalidates_session",
    "post_bootstrap_disconnect_invalidates_session",
    "stale_bootstrap_callbacks_are_rejected", "terminal_fragments_do_not_cross_sessions",
    "checkpoint_precedes_late_manifest", "checkpoint_replaces_manifest_with_higher_sequence",
    "manual_login_restores_pending_deployment",
    "terminal_output_waits_for_login_identity", "bootstrap_start_reply_is_bound_to_origin",
    "reconnect_preserves_early_terminal_output_and_close", "manual_login_preserves_early_terminal_close",
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
