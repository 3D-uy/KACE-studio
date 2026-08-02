"""Contract tests for the exact bootstrap bundled into KACE Studio."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow_value(name: str) -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(rf"^\s{{2}}{name}:\s*([0-9a-f]+)\s*$", workflow, re.MULTILINE)
    assert match, f"Missing workflow value: {name}"
    return match.group(1)


def test_packaged_bootstrap_matches_pinned_sha256():
    assert BOOTSTRAP.is_file(), "bootstrap.sh must be fetched before tests/build"
    actual = hashlib.sha256(BOOTSTRAP.read_bytes()).hexdigest()
    assert actual == _workflow_value("KACE_BOOTSTRAP_SHA256")


def test_workflow_fetches_bootstrap_from_immutable_reference():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    ref = _workflow_value("KACE_BOOTSTRAP_REF")
    assert re.fullmatch(r"[0-9a-f]{40}", ref)
    assert "KACE/{ref}/scripts/bootstrap.sh" in workflow
    assert "KACE/main/scripts/bootstrap.sh" not in workflow


def test_bootstrap_contains_immutable_installer_contract_and_terminal_failure():
    script = BOOTSTRAP.read_text(encoding="utf-8")
    assert re.search(r'^KACE_INSTALL_REF="[0-9a-f]{40}"$', script, re.MULTILINE)
    assert re.search(r'^KACE_INSTALL_SHA256="[0-9a-f]{64}"$', script, re.MULTILINE)
    assert 'KACE/${KACE_INSTALL_REF}/install.sh' in script
    assert "/main/install.sh" not in script
    assert 'KACE_SOURCE_REF="$KACE_INSTALL_REF"' in script
    assert "KACE_NO_LAUNCH=1" in script
    assert "=== KACE_BOOTSTRAP_ERROR: KACE_INSTALL ===" in script
    failure_block = script.split('if [ "$INSTALL_OK" -ne 1 ]; then', 1)[1].split("fi", 1)[0]
    assert "exit 1" in failure_block


def test_bootstrap_enables_native_klipper_features_idempotently():
    script = BOOTSTRAP.read_text(encoding="utf-8")
    assert "# BEGIN KACE_CONFIG_DEFAULT_HELPER" in script
    assert "os.replace(temporary_name, path)" in script
    assert '"exclude_object"' in script
    assert '"force_move" "enable_force_move" "True"' in script
    assert '"file_manager" "enable_object_processing" "True"' in script


def test_bootstrap_preserves_existing_moonraker_configuration():
    script = BOOTSTRAP.read_text(encoding="utf-8")
    creation_guard = (
        'if [ ! -f "$PRINTER_HOME/printer_data/config/moonraker.conf" ]; then'
    )
    assert creation_guard in script
    assert '! grep -q "\\[authorization\\]"' not in script


def test_pyinstaller_bundles_the_verified_bootstrap():
    spec = (ROOT / "main.spec").read_text(encoding="utf-8")
    assert "('bootstrap.sh', '.')" in spec


def test_frontend_rejects_bootstrap_error_marker():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "KACE_BOOTSTRAP_ERROR" in app_js
    assert "!bootstrapFailureHandled" in app_js
    assert "finishBtn.disabled = true" in app_js
