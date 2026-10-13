import os
import ast
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.imager import (
    _build_retryable_first_run,
    _first_run_hostname_reconciliation_body,
)
from backend import imager


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


def _user_migration_lab(tmp_path, username="testuser"):
    """Execute the actual generated body with only accounts/home simulated."""
    tree = ast.parse(Path(imager.__file__).read_text(encoding="utf-8"))
    assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "firstrun_body"
                              for t in node.targets))
    body = eval(compile(ast.Expression(assignment.value), "imager.py", "eval"), {
        "hashed_pw": "synthetic-unused", "username": username, "ssh_enabled_str": "false",
        "hostname_reconciliation": "", "pw_auth_str": "false", "wifi_ssid": "",
        "_first_run_network_import_body": lambda _: "",
    })
    body = body[:body.index("# ── 3. Configure SSH")].replace("/home/", "$LAB_HOME/")
    home = tmp_path / "homes"
    (home / "pi" / "klipper").mkdir(parents=True)
    (home / "pi" / "klipper" / "sentinel").write_text("preserved", encoding="utf-8")
    setup = r'''
LAB_HOME="$(pwd)/homes"
getent() { [ "$1" = passwd ] && awk -F: -v user="$2" '$1 == user {print; found=1} END {exit !found}' accounts; }
id() { getent passwd "$1" >/dev/null; }
groupmod() {
    echo groupmod >> actions
    if [ -f fail-groupmod ]; then rm fail-groupmod; exit 9; fi
}
usermod() {
    echo usermod >> actions
    local target="$2" destination="$4" original="$6"
    printf '%s:x:1000:1000::%s:/bin/bash\n' "$target" "$destination" > accounts
    if [ -f fail-during-usermod ]; then exit 9; fi
    command mv "$LAB_HOME/$original" "$destination"
    if [ -f fail-after-usermod ]; then rm fail-after-usermod; exit 9; fi
}
chpasswd() { cat >/dev/null; echo chpasswd >> actions; }
ln() {
    echo ln >> actions
    if [ -f fail-ln ]; then rm fail-ln; return 9; fi
    command ln "$@"
}
sync() {
    if [ -f fail-identity-sync ] && [[ "${1:-}" == *kace-firstrun-user* ]]; then return 9; fi
    if [ -f fail-published-sync ] && [[ "${1:-}" == */kace-firstrun-user.state ]]; then return 9; fi
    if [ -f fail-directory-sync ] && [ "${1:-}" = "$BOOT_MNT" ]; then return 9; fi
    command sync "$@"
}
mktemp() {
    if [ -f fail-identity-write ]; then printf '%s\n' "$BOOT_MNT"; return; fi
    command mktemp "$@"
}
mv() {
    if [ -f fail-identity-mv ] && [[ "${*: -1}" == *kace-firstrun-user* ]]; then return 9; fi
    command mv "$@"
}
'''
    script = tmp_path / "firstrun.sh"
    script.write_text(_build_retryable_first_run("prebaked", setup + body), encoding="utf-8")
    (tmp_path / "accounts").write_text(
        f"pi:x:1000:1000::{home.as_posix()}/pi:/bin/bash\n", encoding="utf-8")
    (tmp_path / "cmdline.txt").write_text("rootwait systemd.run=/boot/firstrun.sh\n", encoding="utf-8")

    def run():
        return subprocess.run([_bash(), str(script)], cwd=tmp_path, capture_output=True,
                              text=True, timeout=20, env=dict(os.environ,
                              KACE_FIRST_RUN_BOOT_MNT=tmp_path.as_posix()))
    return home, run


# Git Bash can emulate ln -s with a copy. These contracts require actual Linux
# symlinks; run them on Linux CI/Docker, never treat a Windows copy as evidence.
_linux_shell = pytest.mark.skipif(os.name == "nt" or _bash() is None,
                                 reason="requires Linux Bash and real symlinks")


@_linux_shell
@pytest.mark.parametrize("failure", ["fail-after-usermod", "fail-ln", "fail-groupmod"])
def test_user_migration_retry_repairs_alias_then_third_run_is_idempotent(tmp_path, failure):
    home, run = _user_migration_lab(tmp_path)
    (tmp_path / failure).touch()
    assert run().returncode != 0
    assert _state(tmp_path / "kace-firstrun.state")["status"] == "failed"
    identity = (tmp_path / "kace-firstrun-user.state").read_bytes()
    result = run()
    assert result.returncode == 0, (tmp_path / "kace-firstrun.log").read_text()
    assert (home / "pi").is_symlink()
    assert (home / "pi" / "klipper" / "sentinel").read_text() == "preserved"
    assert (home / "pi").resolve() == home / "testuser"
    assert _state(tmp_path / "kace-firstrun.state")["status"] == "complete"
    state = (tmp_path / "kace-firstrun.state").read_bytes()
    actions = (tmp_path / "actions").read_bytes()
    assert run().returncode == 0
    assert (tmp_path / "actions").read_bytes() == actions
    assert (tmp_path / "kace-firstrun.state").read_bytes() == state
    assert (tmp_path / "kace-firstrun-user.state").read_bytes() == identity


@_linux_shell
@pytest.mark.parametrize("conflict", ["directory", "symlink", "uid", "partial_move"])
def test_user_migration_conflict_is_preserved_and_requires_recovery(tmp_path, conflict):
    home, run = _user_migration_lab(tmp_path)
    (tmp_path / ("fail-during-usermod" if conflict == "partial_move" else "fail-after-usermod")).touch()
    assert run().returncode != 0
    if conflict == "directory":
        (home / "pi").mkdir()
        (home / "pi" / "foreign").write_text("do not delete")
    elif conflict == "symlink":
        (home / "pi").symlink_to(home / "missing")
    elif conflict == "uid":
        accounts = tmp_path / "accounts"
        accounts.write_text(accounts.read_text().replace(":1000:1000:", ":2000:1000:"))
    assert run().returncode != 0
    assert _state(tmp_path / "kace-firstrun.state")["status"] == "failed"
    assert "manual recovery" in (tmp_path / "kace-firstrun.log").read_text().lower()
    if conflict == "directory":
        assert (home / "pi" / "foreign").read_text() == "do not delete"
    elif conflict == "symlink":
        assert os.readlink(home / "pi") == str(home / "missing")
    elif conflict == "partial_move":
        assert (home / "pi" / "klipper" / "sentinel").read_text() == "preserved"
        assert not (home / "testuser").exists()


@_linux_shell
@pytest.mark.parametrize("failure", ["fail-identity-write", "fail-identity-sync", "fail-identity-mv", "fail-published-sync", "fail-directory-sync"])
def test_identity_persistence_failure_prevents_account_changes(tmp_path, failure):
    home, run = _user_migration_lab(tmp_path)
    (tmp_path / failure).touch()
    assert run().returncode != 0
    assert not (tmp_path / "actions").exists()
    assert (home / "pi" / "klipper" / "sentinel").read_text() == "preserved"
    assert not (home / "testuser").exists()
    assert "manual recovery" in (tmp_path / "kace-firstrun.log").read_text().lower()


@_linux_shell
def test_retry_cannot_bypass_failed_flush_of_published_identity(tmp_path):
    home, run = _user_migration_lab(tmp_path)
    marker = tmp_path / "fail-published-sync"
    marker.touch()
    assert run().returncode != 0
    assert (tmp_path / "kace-firstrun-user.state").is_file()
    assert run().returncode != 0
    assert not (tmp_path / "actions").exists()
    marker.unlink()
    assert run().returncode == 0
    assert (home / "pi").is_symlink()


@_linux_shell
def test_existing_requested_printer_user_needs_no_rename_or_alias(tmp_path):
    home, run = _user_migration_lab(tmp_path, username="pi")
    assert run().returncode == 0
    assert not (home / "pi").is_symlink()
    assert (tmp_path / "actions").read_text().splitlines() == ["chpasswd"]
    assert _state(tmp_path / "kace-firstrun.state")["status"] == "complete"


@_linux_shell
def test_retry_preserves_an_already_correct_alias(tmp_path):
    home, run = _user_migration_lab(tmp_path)
    (tmp_path / "fail-after-usermod").touch()
    assert run().returncode != 0
    (home / "pi").symlink_to("testuser")
    assert run().returncode == 0
    assert os.readlink(home / "pi") == "testuser"
    assert "ln" not in (tmp_path / "actions").read_text().splitlines()
    assert (home / "pi" / "klipper" / "sentinel").read_text() == "preserved"


@_linux_shell
@pytest.mark.parametrize("corruption", ["duplicate", "missing", "destination", "source_home", "symlink"])
def test_corrupt_or_untrusted_identity_never_changes_accounts_or_homes(tmp_path, corruption):
    home, run = _user_migration_lab(tmp_path)
    (tmp_path / "fail-after-usermod").touch()
    assert run().returncode != 0
    identity = tmp_path / "kace-firstrun-user.state"
    value = identity.read_text()
    if corruption == "duplicate":
        identity.write_text(value + "source_user=pi\n")
    elif corruption == "missing":
        identity.write_text(value.replace("source_uid=1000\n", ""))
    elif corruption == "destination":
        identity.write_text(value.replace("target_user=testuser", "target_user=other"))
    elif corruption == "source_home":
        identity.write_text(value.replace("source_home=", "source_home=/unexpected"))
    else:
        saved = tmp_path / "saved-identity"
        identity.rename(saved)
        identity.symlink_to(saved)
    before = identity.read_bytes()
    actions = (tmp_path / "actions").read_bytes()
    assert run().returncode != 0
    assert identity.read_bytes() == before
    assert (tmp_path / "actions").read_bytes() == actions
    assert not (home / "pi").exists()
    assert (home / "testuser" / "klipper" / "sentinel").read_text() == "preserved"
    assert _state(tmp_path / "kace-firstrun.state")["status"] == "failed"


@_linux_shell
def test_legacy_partial_user_migration_without_identity_requires_recovery(tmp_path):
    home, run = _user_migration_lab(tmp_path)
    (home / "pi").rename(home / "testuser")
    accounts = tmp_path / "accounts"
    accounts.write_text(accounts.read_text().replace("pi:", "testuser:").replace("/pi:", "/testuser:"))
    (tmp_path / "kace-firstrun.state").write_text("status=failed\nattempts=1\n")
    assert run().returncode != 0
    assert "manual recovery" in (tmp_path / "kace-firstrun.log").read_text().lower()
    assert not (tmp_path / "actions").exists()
    assert not (home / "pi").exists()
