"""Contract tests for the exact bootstrap bundled into KACE Studio."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.provisioning import EXPECTED_BOOTSTRAP_SHA256


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_CONTRACT = ROOT / "release-contract.json"
AUTHORITATIVE_BOOTSTRAP = ROOT.parent / "KACE" / "scripts" / "bootstrap.sh"


def _find_bash() -> str | None:
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            git_bash = Path(program_files) / "Git" / "bin" / "bash.exe"
            if git_bash.is_file():
                return str(git_bash)
    return shutil.which("bash")


def _workflow_value(name: str) -> str:
    contract = json.loads(RELEASE_CONTRACT.read_text(encoding="utf-8"))
    key = name.removeprefix("KACE_BOOTSTRAP_").lower()
    return contract["kace"][f"bootstrap_{key}"]


def test_packaged_bootstrap_matches_pinned_sha256():
    assert BOOTSTRAP.is_file(), "bootstrap.sh must be fetched before tests/build"
    actual = hashlib.sha256(BOOTSTRAP.read_bytes()).hexdigest()
    if AUTHORITATIVE_BOOTSTRAP.is_file():
        assert BOOTSTRAP.read_bytes() == AUTHORITATIVE_BOOTSTRAP.read_bytes()
    assert actual == _workflow_value("KACE_BOOTSTRAP_SHA256")
    assert actual == EXPECTED_BOOTSTRAP_SHA256


def test_workflow_fetches_bootstrap_from_immutable_reference():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    ref = _workflow_value("KACE_BOOTSTRAP_REF")
    assert re.fullmatch(r"[0-9a-f]{40}", ref)
    assert "python scripts/release.py fetch-bootstrap" in workflow
    assert "KACE_BOOTSTRAP_REF:" not in workflow
    assert ref in RELEASE_CONTRACT.read_text(encoding="utf-8")
    assert "KACE/main/scripts/bootstrap.sh" not in workflow


def test_bootstrap_contains_immutable_installer_contract_and_terminal_failure():
    script = BOOTSTRAP.read_text(encoding="utf-8")
    assert re.search(r'^KACE_INSTALL_REF="[0-9a-f]{40}"$', script, re.MULTILINE)
    assert re.search(r'^KACE_INSTALL_SHA256="[0-9a-f]{64}"$', script, re.MULTILINE)
    assert 'KACE/${KACE_INSTALL_REF}/install.sh' in script
    assert "/main/install.sh" not in script
    assert 'KACE_SOURCE_REF="$KACE_INSTALL_REF"' in script
    assert "KACE_NO_LAUNCH=1" not in script
    assert "=== KACE_BOOTSTRAP_ERROR: KACE_INSTALL ===" in script
    failure_block = script.split('if [ "$INSTALL_OK" -ne 1 ]; then', 1)[1].split("fi", 1)[0]
    assert 'exit "$INSTALL_EXIT"' in failure_block
    for exit_code in (2, 10, 20, 30, 40):
        assert re.search(rf"(?m)^\s*{exit_code}\)\s*$", failure_block)


def test_bootstrap_emits_versioned_guarded_terminal_events():
    script = BOOTSTRAP.read_text(encoding="utf-8")
    assert 'BOOTSTRAP_PROTOCOL="kace-bootstrap/v1"' in script
    assert "BOOTSTRAP_TERMINAL_EMITTED" in script
    assert 'emit_bootstrap_event "workflow_started" "INIT"' in script
    assert 'emit_bootstrap_event "stage_started" "$id"' in script
    assert 'emit_bootstrap_terminal "workflow_succeeded" "SUCCESS" 0' in script
    assert 'emit_bootstrap_terminal "workflow_failed" "BOOTSTRAP_ERROR" "$exit_status"' in script
    assert 'emit_bootstrap_terminal "workflow_cancelled" "SIGNAL_${signal_name}" 2' in script


def test_bootstrap_pins_every_critical_external_dependency():
    script = BOOTSTRAP.read_text(encoding="utf-8")
    pin_block = script.split("# BEGIN KACE_DEPENDENCY_PINS", 1)[1].split(
        "# END KACE_DEPENDENCY_PINS", 1
    )[0]
    for name in (
        "KLIPPER_REF",
        "MOONRAKER_REF",
        "CROWSNEST_REF",
        "MAINSAIL_CONFIG_REF",
        "FLUIDD_CONFIG_REF",
        "KACE_INSTALL_REF",
    ):
        assert re.search(rf'^{name}="[0-9a-f]{{40}}"$', pin_block, re.MULTILINE)
    for name in (
        "MAINSAIL_SHA256",
        "FLUIDD_SHA256",
        "MAINSAIL_CONFIG_SHA256",
        "FLUIDD_CONFIG_SHA256",
        "KACE_INSTALL_SHA256",
    ):
        assert re.search(rf'^{name}="[0-9a-f]{{64}}"$', pin_block, re.MULTILINE)

    assert "git pull" not in script
    assert "releases/latest" not in script
    assert "/master/client.cfg" not in script
    assert "/main/client.cfg" not in script
    assert 'git -C "$staging" rev-parse HEAD' in script
    assert "download_verified_file" in script


def test_bootstrap_preserves_printer_cfg_and_reconciles_only_moonraker_default():
    script = BOOTSTRAP.read_text(encoding="utf-8")
    assert "# BEGIN KACE_CONFIG_DEFAULT_HELPER" in script
    assert "os.replace(temporary_name, path)" in script
    config_block = script.split('# ── 5. Printer Data Directories & Config Files', 1)[1].split(
        '# ── 6. Dashboard UI', 1
    )[0]
    assert '"exclude_object"' not in config_block
    assert '"force_move" "enable_force_move" "True"' not in config_block
    assert 'if [ ! -f "$PRINTER_HOME/printer_data/config/printer.cfg" ]' in config_block
    assert '"file_manager" "enable_object_processing" "True"' in script


def test_bootstrap_preserves_existing_moonraker_configuration():
    script = BOOTSTRAP.read_text(encoding="utf-8")
    assert "ensure_moonraker_config" in script
    assert 'cat <<EOF > "$PRINTER_HOME/printer_data/config/moonraker.conf"' not in script
    assert '! grep -q "\\[authorization\\]"' not in script


@pytest.mark.skipif(_find_bash() is None, reason="bash is not available")
def test_gpio_relay_is_final_before_moonraker_install_and_restart(tmp_path):
    config_dir = tmp_path / "printer_data" / "config"
    config_dir.mkdir(parents=True)
    moonraker_conf = config_dir / "moonraker.conf"
    moonraker_conf.write_text(
        "[server]\nport: 7125\n\n[authorization]\ntrusted_clients:\n    127.0.0.1\n"
        "\n[file_manager]\ncustom_option: preserved\n",
        encoding="utf-8",
    )

    command = """
set -euo pipefail
python3() { "$KACE_TEST_PYTHON" "$@"; }
export KACE_BOOTSTRAP_LIB_ONLY=1
source "$1"
PRINTER_HOME="$2"
POWER_RELAY=true
POWER_DEVICE=printer
POWER_GPIO=20
POWER_ACTIVE_LOW=true
POWER_RESTART_KLIPPER=true
POWER_INITIAL_STATE=on
POWER_OFF_WHEN_SHUTDOWN=false
ensure_moonraker_config "$PRINTER_HOME/printer_data/config/moonraker.conf" "$PRINTER_HOME/printer_data/comms/klippy.sock"
ensure_moonraker_config "$PRINTER_HOME/printer_data/config/moonraker.conf" "$PRINTER_HOME/printer_data/comms/klippy.sock"
verify_requested_power_relay "$PRINTER_HOME/printer_data/config/moonraker.conf"
"""
    result = subprocess.run(
        [_find_bash(), "-c", command, "bootstrap-test", BOOTSTRAP.as_posix(), tmp_path.as_posix()],
        capture_output=True,
        text=True,
        env={**os.environ, "KACE_TEST_PYTHON": Path(sys.executable).as_posix()},
    )
    assert result.returncode == 0, result.stderr

    final_content = moonraker_conf.read_text(encoding="utf-8")
    assert "[power printer]" in final_content
    assert "type: gpio" in final_content
    assert "pin: !gpiochip0/gpio20" in final_content
    assert "restart_klipper_when_powered: true" in final_content
    assert "initial_state: on" in final_content
    assert "off_when_shutdown: false" in final_content
    assert "[authorization]" in final_content
    assert "trusted_clients:" in final_content
    assert "custom_option: preserved" in final_content
    assert final_content.count("[power printer]") == 1
    assert not list(config_dir.glob(".moonraker.conf.*.part"))

    script = BOOTSTRAP.read_text(encoding="utf-8")
    config_call = script.index(
        '    "$PRINTER_HOME/printer_data/config/moonraker.conf"'
    )
    install_call = script.index('"$PRINTER_HOME/moonraker/scripts/install-moonraker.sh"')
    # Ignore the earlier rollback helper's recovery restart; this assertion is
    # about the normal install/start-services path.
    restart_call = script.index("systemctl restart moonraker", install_call)
    assert config_call < install_call < restart_call


def test_pyinstaller_bundles_the_verified_bootstrap():
    spec = (ROOT / "main.spec").read_text(encoding="utf-8")
    assert "('bootstrap.sh', '.')" in spec


def test_frontend_rejects_bootstrap_error_marker():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "KACE_BOOTSTRAP_ERROR" in app_js
    assert "!bootstrapFailureHandled" in app_js
    assert "finishBtn.disabled = true" in app_js


def test_frontend_uses_versioned_terminal_event_and_keeps_legacy_fallback():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "window.updateBootstrapEvent" in app_js
    assert "eventName === 'workflow_succeeded'" in app_js
    assert "!bootstrapAuthoritativeSeen" in app_js
    assert "Bootstrap complete! KACE wizard finished successfully." in app_js
    assert "window.pywebview.api.start_bootstrap(selectedUi)" in app_js
    assert "window.pywebview.api.send_ssh_input(bootstrapCmd)" not in app_js
    assert "Bootstrap complete! KACE Node is fully ready." not in app_js

    script = BOOTSTRAP.read_text(encoding="utf-8")
    installer_index = script.index('bash "$tmp_script"')
    finalization_index = script.index(
        'finalize_bootstrap_success "$MOONRAKER_CONFIG" "$BOOT_CFG"'
    )
    assert installer_index < finalization_index
