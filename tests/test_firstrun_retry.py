import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.imager import (
    _build_retryable_first_run,
    _first_run_hostname_reconciliation_body,
)


def _bash():
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            candidate = Path(program_files) / "Git" / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    return shutil.which("bash")


def _state(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )


@pytest.mark.skipif(_bash() is None, reason="bash is unavailable")
def test_prebaked_hostname_reconciliation_is_safe_and_idempotent(tmp_path):
    hostname_file = tmp_path / "hostname"
    hosts_file = tmp_path / "hosts"
    hostname_file.write_text("mainsailos\n", encoding="utf-8")
    hosts_file.write_text(
        "127.0.0.1\tlocalhost\n127.0.1.1\tmainsailos\n",
        encoding="utf-8",
    )
    script = tmp_path / "reconcile-hostname.sh"
    script.write_text(
        "#!/bin/bash\nset -Eeuo pipefail\n"
        + _first_run_hostname_reconciliation_body("kace"),
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        KACE_HOSTNAME_FILE=hostname_file.as_posix(),
        KACE_HOSTS_FILE=hosts_file.as_posix(),
        KACE_SKIP_RUNTIME_HOSTNAME="true",
    )

    first = subprocess.run([_bash(), str(script)], env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert hostname_file.read_text(encoding="utf-8") == "kace\n"
    first_hosts = hosts_file.read_text(encoding="utf-8")
    assert first_hosts.count("127.0.1.1\tkace") == 1
    assert "127.0.1.1\tmainsailos" in first_hosts

    second = subprocess.run([_bash(), str(script)], env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert hosts_file.read_text(encoding="utf-8") == first_hosts


@pytest.mark.skipif(_bash() is None, reason="bash is unavailable")
def test_prebaked_hostname_reconciliation_preserves_already_correct_hosts(tmp_path):
    hostname_file = tmp_path / "hostname"
    hosts_file = tmp_path / "hosts"
    hostname_file.write_text("kace\n", encoding="utf-8")
    original = "127.0.0.1\tlocalhost\n127.0.1.1\tkace\n"
    hosts_file.write_text(original, encoding="utf-8")
    script = tmp_path / "reconcile-hostname.sh"
    script.write_text(
        "#!/bin/bash\nset -Eeuo pipefail\n"
        + _first_run_hostname_reconciliation_body("kace"),
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        KACE_HOSTNAME_FILE=hostname_file.as_posix(),
        KACE_HOSTS_FILE=hosts_file.as_posix(),
        KACE_SKIP_RUNTIME_HOSTNAME="true",
    )

    result = subprocess.run([_bash(), str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert hosts_file.read_text(encoding="utf-8") == original


@pytest.mark.skipif(_bash() is None, reason="bash is unavailable")
def test_failed_first_run_retries_then_completes_idempotently(tmp_path):
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(
        "rootwait systemd.run=/boot/firmware/firstrun.sh "
        "systemd.run_success_action=reboot systemd.run_failure_action=reboot "
        "systemd.unit=kernel-command-line.target\n",
        encoding="utf-8",
    )
    for relative in (
        "wpa_supplicant.conf",
        "headless_nm.txt",
        "network-config",
        "system-connections/preconfigured-wifi.nmconnection",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("credential\n", encoding="utf-8")

    body = r'''
if [ ! -f "$BOOT_MNT/allow-success" ]; then
    echo "synthetic failure" >&2
    exit 7
fi
CLEAN_WIFI_ON_SUCCESS=true
'''
    script = tmp_path / "firstrun.sh"
    script.write_text(_build_retryable_first_run("test", body), encoding="utf-8")
    env = dict(os.environ, KACE_FIRST_RUN_BOOT_MNT=tmp_path.as_posix())

    first = subprocess.run([_bash(), str(script)], env=env, capture_output=True, text=True)
    assert first.returncode == 7
    first_state = _state(tmp_path / "kace-firstrun.state")
    assert first_state["status"] == "failed"
    assert first_state["attempts"] == "1"
    assert "systemd.run=" in cmdline.read_text(encoding="utf-8")
    assert script.exists()
    assert (tmp_path / "kace-firstrun.log").exists()
    assert (tmp_path / "network-config").exists()

    (tmp_path / "allow-success").touch()
    second = subprocess.run([_bash(), str(script)], env=env, capture_output=True, text=True)
    assert second.returncode == 0
    second_state = _state(tmp_path / "kace-firstrun.state")
    assert second_state["status"] == "complete"
    assert second_state["attempts"] == "2"
    assert "systemd.run=" not in cmdline.read_text(encoding="utf-8")
    assert not (tmp_path / "network-config").exists()
    assert script.exists()

    third = subprocess.run([_bash(), str(script)], env=env, capture_output=True, text=True)
    assert third.returncode == 0
    assert _state(tmp_path / "kace-firstrun.state")["attempts"] == "2"


@pytest.mark.skipif(_bash() is None, reason="bash is unavailable")
def test_retry_limit_disables_trigger_without_deleting_diagnostics(tmp_path):
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(
        "rootwait systemd.run=/boot/firmware/firstrun.sh "
        "systemd.run_failure_action=reboot systemd.unit=kernel-command-line.target\n",
        encoding="utf-8",
    )
    script = tmp_path / "firstrun.sh"
    script.write_text(
        _build_retryable_first_run("test", "echo failed >&2\nexit 9"),
        encoding="utf-8",
    )
    env = dict(os.environ, KACE_FIRST_RUN_BOOT_MNT=tmp_path.as_posix())

    for _ in range(3):
        result = subprocess.run([_bash(), str(script)], env=env, capture_output=True, text=True)
        assert result.returncode == 9

    state = _state(tmp_path / "kace-firstrun.state")
    assert state["status"] == "failed"
    assert state["attempts"] == "3"
    assert "systemd.run=" not in cmdline.read_text(encoding="utf-8")
    assert script.exists()
    assert (tmp_path / "kace-firstrun.log").exists()
    assert (tmp_path / "kace-firstrun-error.log").exists()
