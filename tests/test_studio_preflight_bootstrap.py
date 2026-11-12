"""Exercise bootstrap's stdin context with a synthetic sudo/login boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_bootstrap_contract import _find_bash


ROOT = Path(__file__).resolve().parents[1]
KACE = ROOT.parent / "KACE"
KEYS = ("KACE_STUDIO_PREFLIGHT_PORT", "KACE_STUDIO_PREFLIGHT_TOKEN", "KACE_STUDIO_PREFLIGHT_SESSION")
PROBE = r'''
import hashlib, importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location('studio_client', sys.argv[1])
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
keys = ('KACE_STUDIO_PREFLIGHT_PORT','KACE_STUDIO_PREFLIGHT_TOKEN','KACE_STUDIO_PREFLIGHT_SESSION')
context = {key:os.environ[key] for key in keys if key in os.environ}
result = {'context':context, 'configured':client.configured()}
if not context:
    result['stdin'] = sys.stdin.read()
if context and not all(context.values()):
    def forbidden(*args, **kwargs):
        raise AssertionError('Partial context must not open a connection')
    client.socket.create_connection = forbidden
    result['validation'] = client.validate_files({}, {}, '')['status']
print(json.dumps(result))
'''


def run_context(tmp_path, context):
    script = (KACE / "scripts/bootstrap.sh").read_text(encoding="utf-8")
    block = "STUDIO_PREFLIGHT_CONTEXT=0" + script.split("STUDIO_PREFLIGHT_CONTEXT=0", 1)[1].split('\nif [ "$(id -un)"', 1)[0]
    assert "if run_kace_installer_as_user; then" in script
    # Preserve the real array, stdin framing and target reads. Replace only the
    # unrelated installer body so this regression cannot download or install.
    before, tail = block.split('        tmp_script="/tmp/kace-install.sh"', 1)
    after = tail.split("\n        '\n    )", 1)[1]
    block = before + '        "$TEST_PYTHON" "$TEST_PROBE" "$TEST_CLIENT"\n' + "        '\n    )" + after
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    argv = tmp_path / "argv.txt"
    environment = {key: value for key, value in os.environ.items() if key not in KEYS}
    environment.update(context)
    environment.update({"TEST_PYTHON": Path(sys.executable).as_posix(), "TEST_PROBE": probe.as_posix(),
                        "TEST_CLIENT": (KACE / "core/studio_preflight.py").as_posix(), "TEST_ARGV": argv.as_posix(),
                        "PRINTER_USER": "fixture", "KACE_INSTALL_URL": "fixture", "KACE_INSTALL_SHA256": "fixture",
                        "KACE_INSTALL_REF": "fixture"})
    command = 'log_err() { echo "$1" >&2; }; emit_bootstrap_terminal() { :; };\n' + block
    command += '''
sudo() {
    builtin printf '%s\\n' "$@" >"$TEST_ARGV"
    shift 3
    env -i PATH="$PATH" TEST_PYTHON="$TEST_PYTHON" TEST_PROBE="$TEST_PROBE" TEST_CLIENT="$TEST_CLIENT" "$@"
}
run_kace_installer_as_user
'''
    run = subprocess.run([_find_bash(), "-c", command], env=environment, input="original stdin\n",
                         capture_output=True, text=True, timeout=15)
    return run, argv


@pytest.mark.skipif(_find_bash() is None or not KACE.is_dir(), reason="requires Bash and sibling KACE source")
@pytest.mark.parametrize("context", [
    {}, dict(zip(KEYS, ("49001", "a" * 64, "b" * 32))),
    {KEYS[0]: "49001"}, {KEYS[1]: ""},
])
def test_sudo_boundary_preserves_selection_and_partial_context_fails_closed(context, tmp_path):
    run, argv = run_context(tmp_path, context)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["configured"] is bool(context)
    assert result["context"] == ({key: context.get(key, "") for key in KEYS} if context else {})
    if context and len(context) != 3:
        assert result["validation"] == "unverified"
    if not context:
        assert result["stdin"] == "original stdin\n"
    command = argv.read_text(encoding="utf-8")
    for key in KEYS:
        if context.get(key):
            assert context[key] not in command


@pytest.mark.skipif(_find_bash() is None or not KACE.is_dir(), reason="requires Bash and sibling KACE source")
@pytest.mark.parametrize("context", [{KEYS[0]: "1\n2"}, {KEYS[1]: "a" * 63}, {KEYS[2]: "bad;echo injection"}])
def test_malformed_context_stops_before_installer_and_sudo(tmp_path, context):
    run, argv = run_context(tmp_path, context)
    assert run.returncode != 0
    assert "Invalid Studio preflight context" in run.stderr
    assert not argv.exists()


@pytest.mark.skipif(_find_bash() is None or not KACE.is_dir(), reason="requires Bash and sibling KACE source")
def test_truncated_stdin_context_cannot_start_installer():
    script = (KACE / "scripts/bootstrap.sh").read_text(encoding="utf-8")
    child = script.split('KACE_STUDIO_PREFLIGHT_CONTEXT="$STUDIO_PREFLIGHT_CONTEXT"\n        sh -c \'', 1)[1]
    child = child.split('        tmp_script="/tmp/kace-install.sh"', 1)[0]
    environment = dict(os.environ, KACE_STUDIO_PREFLIGHT_CONTEXT="1")
    result = subprocess.run([_find_bash(), "-c", child + "echo INSTALLER_REACHED"], env=environment,
                            input="49001\n" + "a" * 64 + "\n", capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "INSTALLER_REACHED" not in result.stdout
    assert "incomplete Studio preflight context" in result.stderr
