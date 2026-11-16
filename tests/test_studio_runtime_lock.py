"""Real Linux flock around generated launchers; all runtime programs are fixtures."""
import hashlib
import os
import shutil
import subprocess
import sys
import uuid

import pytest

from backend.kace_configuration import configuration_command
from backend.kace_resume import resume_command


pytestmark = pytest.mark.skipif(os.name != "posix" or not shutil.which("flock"),
                                reason="real POSIX flock; verified in isolated Linux")


def launcher(tmp_path, kind):
    runtime = tmp_path / "kace"
    (runtime / "venv/bin").mkdir(parents=True)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    checkpoint = b'{"state":"COMPLETE"}'
    (runtime / "firmware-workflow.json").write_bytes(checkpoint)
    (runtime / ".kace-runtime-commit").write_text("a" * 40 + "\n")
    interpreter = runtime / "venv/bin/python"
    interpreter.write_text("#!" + sys.executable + "\n" + '''
import fcntl,os,pathlib,sys
home=pathlib.Path(os.environ['HOME'])
phase='cli' if sys.argv[1]=='./kace.py' else ('capability' if 'SCHEMA' in sys.argv[2] else 'verification')
with (home/'.kace-install.lock').open('a') as lock:
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: pass
    else: raise SystemExit('runtime was invoked without its shared lock')
with (home/'observed').open('a') as out: out.write(phase+'\\n')
''', encoding="utf-8")
    interpreter.chmod(0o755)
    git = binaries / "git"
    git.write_text("#!/bin/sh\nprintf called >> \"$HOME/git-called\"\nif [ \"$1\" = rev-parse ]; then printf '%s\\n' '" + "a"*40 + "'; fi\n")
    git.chmod(0o755)
    env_path = "/tmp/kace-studio-preflight-" + uuid.uuid4().hex + ".env"
    args = (env_path, "a"*40, hashlib.sha256(checkpoint).hexdigest(), "bootstrap-"+"b"*32)
    command = resume_command(*args) if kind == "resume" else configuration_command(*args, "c"*32)
    script = f"trap 'rm -f -- {env_path}' EXIT; printf 'true\\n' > {env_path}; " + command
    environment = {**os.environ, "HOME": str(tmp_path), "PATH": str(binaries)+os.pathsep+os.environ["PATH"]}
    return script, environment


@pytest.mark.parametrize("kind", ["resume", "configuration"])
@pytest.mark.parametrize("obstacle", ["lock", "journal", "dangling-journal", "flock-failure"])
def test_runtime_is_not_imported_or_started_with_unfinished_install(tmp_path, kind, obstacle):
    import fcntl
    command, environment = launcher(tmp_path, kind)
    journal = tmp_path / ".kace-runtime-transaction.json"
    if obstacle == "journal":
        journal.write_text("unfinished")
    elif obstacle == "dangling-journal":
        journal.symlink_to(tmp_path / "absent")
    elif obstacle == "flock-failure":
        flock = tmp_path / "bin/flock"
        flock.write_text("#!/bin/sh\nexit 1\n")
        flock.chmod(0o755)
    with (tmp_path / ".kace-install.lock").open("a") as lock:
        if obstacle == "lock":
            fcntl.flock(lock, fcntl.LOCK_EX)
        result = subprocess.run(["bash", "-c", command], env=environment,
                                capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert "workflow_failed" in result.stdout and "CONTRACT_MISMATCH" in result.stdout
    assert "workflow_started" not in result.stdout
    assert not (tmp_path / "observed").exists()
    assert not (tmp_path / "git-called").exists()
    if obstacle == "journal":
        assert journal.read_text() == "unfinished"
    elif obstacle == "dangling-journal":
        assert journal.is_symlink()


@pytest.mark.parametrize("kind", ["resume", "configuration"])
def test_lock_is_retained_through_runtime_and_final_verification(tmp_path, kind):
    import fcntl
    command, environment = launcher(tmp_path, kind)
    result = subprocess.run(["bash", "-c", command], env=environment,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert "workflow_succeeded" in result.stdout, result.stdout + result.stderr
    expected = ["cli", "verification"] if kind == "resume" else ["capability", "cli", "verification"]
    assert (tmp_path / "observed").read_text().splitlines() == expected
    with (tmp_path / ".kace-install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # Released after the shell exits.
