"""Run the actual resume shell offline; all runtime/transport peers are fixtures."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

import pytest

from backend.kace_resume import resume_command
from test_bootstrap_contract import _find_bash


@pytest.mark.parametrize('fault,expected,launched', [
    ('', 'SUCCESS', True),
    ('pending', 'RECOVERY_PENDING', True),
    ('cli-failed', 'RECOVERY_PENDING', True),
    ('wrong-commit', 'RECOVERY_CONTRACT_MISMATCH', False),
    ('wrong-marker', 'RECOVERY_CONTRACT_MISMATCH', False),
    ('dirty', 'RECOVERY_CONTRACT_MISMATCH', False),
    ('checkpoint-changed', 'RECOVERY_CONTRACT_MISMATCH', False),
    ('missing-env', 'PREFLIGHT_CONTEXT_UNAVAILABLE', False),
])
def test_resume_shell_gates_before_cli_and_requires_complete(tmp_path, fault, expected, launched):
    bash = _find_bash()
    if not bash:
        pytest.skip('Bash required for offline shell integration')
    runtime = tmp_path / 'kace'
    (runtime / 'venv/bin').mkdir(parents=True)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    commit = 'a' * 40
    (runtime / '.kace-runtime-commit').write_text(('b'*40 if fault == 'wrong-marker' else commit)+'\n')
    original = b'{"state":"DEPLOYING"}'
    (runtime / 'firmware-workflow.json').write_bytes(b'changed' if fault == 'checkpoint-changed' else original)
    (binaries / 'git').write_text('''#!/bin/sh
if [ "$1" = rev-parse ]; then
    if [ "$TEST_FAULT" = wrong-commit ]; then printf '%040d\\n' 0; else printf '%s\\n' "$TEST_COMMIT"; fi
else
    [ "$TEST_FAULT" != dirty ]
fi
''', newline='\n')
    (runtime / 'venv/bin/python').write_text('''#!/bin/sh
if [ "$1" = ./kace.py ]; then
    printf '%s|%s|%s\\n' "$KACE_MACHINE_OUTPUT" "$KACE_STUDIO_PREFLIGHT_TOKEN" "$KACE_FIRMWARE_WORKFLOW_PATH" > "$HOME/launched"
    [ "$TEST_FAULT" != cli-failed ]
else
    printf '%s\\n' "$2" > "$HOME/completion-check"
    [ "$TEST_FAULT" != pending ]
fi
''', newline='\n')
    env_path = '/tmp/kace-studio-preflight-' + uuid.uuid4().hex + '.env'
    command = resume_command(env_path, commit, hashlib.sha256(original).hexdigest(), 'bootstrap-'+'c'*32)
    setup = (
        'export HOME="$(pwd)"; export PATH="$HOME/bin:$PATH"; '
        f'export TEST_COMMIT={commit}; export TEST_FAULT={shlex.quote(fault)}; '
        f'trap "rm -f -- {env_path}" EXIT; '
    )
    if fault != 'missing-env':
        setup += f"printf '%s\\n' 'export KACE_STUDIO_PREFLIGHT_TOKEN=private-fixture' > {env_path}; "
    result = subprocess.run([bash, '-c', setup+command], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line.split('=== KACE_BOOTSTRAP_EVENT: ', 1)[1].rsplit(' ===', 1)[0])
              for line in result.stdout.splitlines() if line.startswith('=== KACE_BOOTSTRAP_EVENT: ')]
    assert events[-1]['code'] == expected, result.stdout+result.stderr
    assert (tmp_path / 'launched').exists() == launched
    assert 'private-fixture' not in result.stdout+result.stderr
    if launched:
        assert (tmp_path / 'launched').read_text().startswith('1|private-fixture|')
    if expected == 'SUCCESS':
        check = (tmp_path / 'completion-check').read_text()
        assert 'verify_artifact=True' in check and 'COMPLETE' in check
    else:
        assert not any(event['event'] == 'workflow_succeeded' for event in events)


@pytest.mark.parametrize('index,value', [(0, '/tmp/x; echo unsafe'), (1, 'main'), (2, ''), (3, 'x\ncmd')])
def test_resume_command_rejects_untrusted_shell_identifiers(index, value):
    args = ['/tmp/kace-studio-preflight-test.env', 'a'*40, 'b'*64, 'bootstrap-'+'c'*32]
    args[index] = value
    with pytest.raises(ValueError):
        resume_command(*args)
