"""Behavioral tests for the reproducible external dependency contract."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="The bootstrap dependency harness runs on POSIX CI",
)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def dependency_harness(tmp_path: Path) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_log = tmp_path / "git.log"

    _write_executable(
        bin_dir / "git",
        """#!/usr/bin/env bash
set -euo pipefail

if [[ "$1" == "init" ]]; then
    mkdir -p "$2/.git"
    exit 0
fi

if [[ "$1" != "-C" ]]; then
    exit 64
fi

directory="$2"
shift 2
command="$1"
shift
case "$command" in
    remote)
        exit 0
        ;;
    fetch)
        printf 'fetch\\n' >> "$FAKE_GIT_LOG"
        if [[ "${FAKE_GIT_MODE:-ok}" == "network-fail" ]]; then
            exit 69
        fi
        expected_ref="${!#}"
        if [[ "${FAKE_GIT_MODE:-ok}" == "wrong-ref" ]]; then
            printf '%s\\n' '0000000000000000000000000000000000000000' > "$directory/.git/kace-head"
        else
            printf '%s\\n' "$expected_ref" > "$directory/.git/kace-head"
        fi
        ;;
    checkout)
        exit 0
        ;;
    rev-parse)
        cat "$directory/.git/kace-head"
        ;;
    *)
        exit 64
        ;;
esac
""",
    )
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env bash
set -euo pipefail

output=""
while [[ "$#" -gt 0 ]]; do
    if [[ "$1" == "-o" ]]; then
        output="$2"
        shift 2
    else
        shift
    fi
done

if [[ "${FAKE_CURL_MODE:-ok}" == "network-fail" ]]; then
    exit 7
fi
if [[ "${FAKE_CURL_MODE:-ok}" == "incomplete" ]]; then
    printf 'partial' > "$output"
else
    printf '%s' "${FAKE_CURL_CONTENT:-verified-content}" > "$output"
fi
""",
    )
    _write_executable(bin_dir / "chown", "#!/usr/bin/env bash\nexit 0\n")
    return {"bin_dir": bin_dir, "git_log": git_log}


def _run_bootstrap_function(
    harness: dict[str, Path], command: str, *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    shell = """
set -euo pipefail
export KACE_BOOTSTRAP_LIB_ONLY=1
source "$1"
SUDO=""
PRINTER_USER="$(id -un)"
PRINTER_GROUP="$PRINTER_USER"
PATH="$2:$PATH"
""" + command
    env = os.environ.copy()
    env["FAKE_GIT_LOG"] = str(harness["git_log"])
    if environment:
        env.update(environment)
    return subprocess.run(
        [BASH, "-c", shell, "bootstrap-test", str(BOOTSTRAP), str(harness["bin_dir"]), *arguments],
        capture_output=True,
        text=True,
        env=env,
    )


def test_forbids_moving_critical_dependency_references():
    script = BOOTSTRAP.read_text(encoding="utf-8")
    assert "# BEGIN KACE_DEPENDENCY_PINS" in script
    assert "git pull" not in script
    assert "releases/latest" not in script
    assert "/master/client.cfg" not in script
    assert "/main/client.cfg" not in script


def test_pinned_git_checkout_verifies_expected_commit_and_is_idempotent(
    dependency_harness: dict[str, Path], tmp_path: Path
):
    expected_ref = "1" * 40
    target = tmp_path / "klipper"
    result = _run_bootstrap_function(
        dependency_harness,
        'ensure_pinned_git_checkout "Klipper" "https://example.invalid/klipper.git" "$3" "$4"\n'
        'ensure_pinned_git_checkout "Klipper" "https://example.invalid/klipper.git" "$3" "$4"\n'
        'test "$(cat "$4/.git/kace-head")" = "$3"\n',
        expected_ref,
        str(target),
    )

    assert result.returncode == 0, result.stderr
    assert dependency_harness["git_log"].read_text(encoding="utf-8") == "fetch\n"


def test_pinned_git_checkout_rejects_wrong_commit(
    dependency_harness: dict[str, Path], tmp_path: Path
):
    target = tmp_path / "moonraker"
    result = _run_bootstrap_function(
        dependency_harness,
        'ensure_pinned_git_checkout "Moonraker" "https://example.invalid/moonraker.git" "$3" "$4"\n',
        "2" * 40,
        str(target),
        environment={"FAKE_GIT_MODE": "wrong-ref"},
    )

    assert result.returncode != 0
    assert not target.exists()
    assert not list(tmp_path.glob("moonraker.kace-staging.*"))


def test_pinned_git_checkout_fails_safely_on_network_error(
    dependency_harness: dict[str, Path], tmp_path: Path
):
    target = tmp_path / "crowsnest"
    result = _run_bootstrap_function(
        dependency_harness,
        'ensure_pinned_git_checkout "Crowsnest" "https://example.invalid/crowsnest.git" "$3" "$4"\n',
        "3" * 40,
        str(target),
        environment={"FAKE_GIT_MODE": "network-fail"},
    )

    assert result.returncode != 0
    assert not target.exists()
    assert not list(tmp_path.glob("crowsnest.kace-staging.*"))


def test_verified_download_publishes_only_after_matching_hash(
    dependency_harness: dict[str, Path], tmp_path: Path
):
    content = "verified release"
    destination = tmp_path / "release.zip"
    expected_hash = hashlib.sha256(content.encode()).hexdigest()
    result = _run_bootstrap_function(
        dependency_harness,
        'download_verified_file "Release" "https://example.invalid/release.zip" "$3" "$4"\n',
        str(destination),
        expected_hash,
        environment={"FAKE_CURL_CONTENT": content},
    )

    assert result.returncode == 0, result.stderr
    assert destination.read_text(encoding="utf-8") == content
    assert not list(tmp_path.glob("release.zip.kace-download.*"))


def test_verified_download_rejects_wrong_hash_without_replacing_destination(
    dependency_harness: dict[str, Path], tmp_path: Path
):
    destination = tmp_path / "release.zip"
    destination.write_text("previous", encoding="utf-8")
    result = _run_bootstrap_function(
        dependency_harness,
        'if download_verified_file "Release" "https://example.invalid/release.zip" "$3" "$4"; then exit 99; fi\n'
        'test "$(cat "$3")" = "previous"\n',
        str(destination),
        hashlib.sha256(b"expected").hexdigest(),
        environment={"FAKE_CURL_CONTENT": "tampered"},
    )

    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.glob("release.zip.kace-download.*"))


@pytest.mark.parametrize("mode", ["incomplete", "network-fail"])
def test_verified_download_rejects_incomplete_or_failed_network_requests(
    dependency_harness: dict[str, Path], tmp_path: Path, mode: str
):
    destination = tmp_path / "release.zip"
    destination.write_text("previous", encoding="utf-8")
    result = _run_bootstrap_function(
        dependency_harness,
        'if download_verified_file "Release" "https://example.invalid/release.zip" "$3" "$4"; then exit 99; fi\n'
        'test "$(cat "$3")" = "previous"\n',
        str(destination),
        hashlib.sha256(b"verified release").hexdigest(),
        environment={"FAKE_CURL_MODE": mode},
    )

    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.glob("release.zip.kace-download.*"))


def test_partial_dashboard_installation_is_not_overwritten(
    dependency_harness: dict[str, Path], tmp_path: Path
):
    target = tmp_path / "mainsail"
    target.mkdir()
    (target / "partial-file").write_text("partial", encoding="utf-8")
    result = _run_bootstrap_function(
        dependency_harness,
        'if install_verified_dashboard "Mainsail" "https://example.invalid/mainsail.zip" "$3" "$4" "$5"; then exit 99; fi\n'
        'test "$(cat "$5/partial-file")" = "partial"\n',
        "4" * 64,
        str(tmp_path / "mainsail.zip"),
        str(target),
    )

    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.glob(".mainsail.kace-staging.*"))
