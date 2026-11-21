"""Execute the generated Bash launcher with a real Python result verifier offline."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid

import pytest

from backend.kace_configuration import configuration_command
from test_bootstrap_contract import _find_bash


@pytest.mark.parametrize('fault,expected,launched', [
    ('', 'CONFIGURATION_COMPLETE', True),
    ('pending', 'CONFIGURATION_PENDING', True),
    ('wrong-result', 'CONFIGURATION_PENDING', True),
    ('wrong-schema', 'CONFIGURATION_PENDING', True),
    ('wrong-checkpoint', 'CONFIGURATION_PENDING', True),
    ('stale-sequence', 'CONFIGURATION_PENDING', True),
    ('invalid-exit', 'CONFIGURATION_PENDING', True),
    ('changed-original', 'CONFIGURATION_PENDING', True),
    ('missing-result', 'CONFIGURATION_PENDING', True),
    ('missing-capability', 'CONFIGURATION_CONTRACT_MISMATCH', False),
    ('existing-operation', 'CONFIGURATION_CONTRACT_MISMATCH', False),
    ('dirty', 'CONFIGURATION_CONTRACT_MISMATCH', False),
    ('wrong-marker', 'CONFIGURATION_CONTRACT_MISMATCH', False),
    ('missing-env', 'PREFLIGHT_CONTEXT_UNAVAILABLE', False),
    ('resume', 'CONFIGURATION_COMPLETE', True),
    ('resume-changed', 'CONFIGURATION_CONTRACT_MISMATCH', False),
])
@pytest.mark.parametrize("optimize", [0, 1])
def test_new_operation_requires_its_own_result_and_preserves_original(tmp_path, fault, expected, launched, optimize):
    bash = _find_bash()
    if not bash:
        pytest.skip('Bash required for offline launch integration')
    runtime = tmp_path/'kace'
    (runtime/'venv/bin').mkdir(parents=True)
    (runtime/'core').mkdir()
    (runtime/'core/__init__.py').write_text('')
    (runtime/'core/configuration_operation.py').write_text(
        'SCHEMA="missing"' if fault == 'missing-capability' else 'SCHEMA="kace-configuration-operation/v1"')
    (tmp_path/'bin').mkdir()
    (tmp_path/'bin/git').write_text('#!/bin/sh\nif [ "$1" = rev-parse ]; then printf "%s\\n" "$TEST_COMMIT"; else [ "$TEST_FAULT" != dirty ]; fi\n', newline='\n')
    if os.name == 'nt':
        # Git Bash lacks flock; real locking is checked in isolated Linux.
        (tmp_path/'bin/flock').write_text(
            '#!/bin/sh\n[ "$*" = "-s -n 8" ] || exit 2\nprintf locked > "$HOME/lock-requested"\n',
            newline='\n')
    interpreter = Path(sys.executable).as_posix()
    (runtime/'venv/bin/python').write_text('#!/bin/sh\nexec '+shlex.quote(interpreter)+' "$@"\n', newline='\n')
    if os.name != 'nt':
        (tmp_path/'bin/git').chmod(0o755)
        (runtime/'venv/bin/python').chmod(0o755)
    original = b'{"state":"COMPLETE","installation":"historical"}'
    digest = hashlib.sha256(original).hexdigest()
    (runtime/'firmware-workflow.json').write_bytes(original)
    (runtime/'.kace-runtime-commit').write_text(('b' if fault == 'wrong-marker' else 'a')*40+'\n')
    operation = 'c'*32
    result_sha = None
    if fault.startswith('resume'):
        result_path = runtime/f'configuration-operations/{operation}/result.json'
        result_path.parent.mkdir(parents=True)
        result_path.write_text(json.dumps(dict(schema='kace-configuration-operation/v1', operation_id=operation,
            source_checkpoint_sha256=digest, sequence=4, state='FAILED', exit_code=10)))
        result_sha = hashlib.sha256(result_path.read_bytes()).hexdigest()
        if fault == 'resume-changed': result_path.write_text('{}')
    if fault == 'existing-operation': (runtime/f'configuration-operations/{operation}').mkdir(parents=True)
    (runtime/'kace.py').write_text('''import json,os,pathlib,sys
args=sys.argv
assert args[1] in ('--new-configuration','--resume-configuration')
if args[1]=='--resume-configuration':
    import hashlib
    assert args[5]=='--configuration-result-sha256'
    assert hashlib.sha256((pathlib.Path('configuration-operations')/args[2]/'result.json').read_bytes()).hexdigest()==args[6]
assert pathlib.Path(os.environ['KACE_FIRMWARE_WORKFLOW_PATH']).resolve()==pathlib.Path('firmware-workflow.json').resolve()
pathlib.Path('launched').write_text('yes')
fault=os.environ['TEST_FAULT']
if fault=='changed-original': pathlib.Path('firmware-workflow.json').write_text('changed')
if fault=='missing-result': raise SystemExit(0)
result=dict(schema='kace-configuration-operation/v1',operation_id=args[2],source_checkpoint_sha256=args[4],sequence=5,state='COMPLETE',exit_code=0)
if fault=='pending': result.update(state='FAILED',exit_code=1)
if fault=='wrong-result': result['operation_id']='d'*32
if fault=='wrong-schema': result['schema']='unknown'
if fault=='wrong-checkpoint': result['source_checkpoint_sha256']='d'*64
if fault=='stale-sequence': result['sequence']=False
if fault=='invalid-exit': result['exit_code']=False
p=pathlib.Path('configuration-operations')/args[2]/'result.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(result))
''', newline='\n')
    env_path='/tmp/kace-studio-preflight-'+uuid.uuid4().hex+'.env'
    command=configuration_command(env_path,'a'*40,digest,'bootstrap-'+'e'*32,operation,result_sha)
    setup=('export HOME="$(pwd)"; export PATH="$HOME/bin:$PATH"; '
           f'export TEST_COMMIT={"a"*40}; export TEST_FAULT={shlex.quote(fault)}; '
           f'trap "rm -f -- {env_path}" EXIT; ')
    if fault != 'missing-env': setup+=f"printf '%s\\n' 'export KACE_STUDIO_PREFLIGHT_TOKEN=fixture-secret' > {env_path}; "
    completed=subprocess.run([bash,'-c',setup+command],cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',
                             env={**os.environ, 'PYTHONOPTIMIZE': str(optimize)},timeout=20)
    events=[json.loads(line.split('=== KACE_BOOTSTRAP_EVENT: ',1)[1].rsplit(' ===',1)[0]) for line in completed.stdout.splitlines() if line.startswith('=== KACE_BOOTSTRAP_EVENT: ')]
    assert events and events[-1]['code']==expected, completed.stdout+completed.stderr
    assert (runtime/'launched').exists()==launched
    if launched and os.name == 'nt':
        assert (tmp_path/'lock-requested').read_text() == 'locked'
    assert 'fixture-secret' not in completed.stdout+completed.stderr
    if fault != 'changed-original': assert (runtime/'firmware-workflow.json').read_bytes()==original
    if expected != 'CONFIGURATION_COMPLETE': assert not any(e['event']=='workflow_succeeded' for e in events)
