"""Launch one new configuration using the installed, pinned KACE runtime."""
import json
import re
import shlex

_INTERMEDIATE = frozenset({'PREPARING', 'BACKUP', 'APPLYING_CONFIG', 'VERIFYING_UPLOAD',
                          'WAITING_MOONRAKER', 'FIRMWARE_RESTART', 'VERIFYING_CONFIG', 'DONE', 'CONFIG_ERROR'})
_TERMINAL_EXITS = {'COMPLETE': {0}, 'CANCELLED': {2}, 'PENDING_ACTIVATION': {41}, 'FAILED': {10, 20, 30, 40}}

def parse_configuration_result(raw, operation_id, checkpoint_sha):
    """Read operation evidence only; never infer it from installation COMPLETE."""
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get('schema') != 'kace-configuration-operation/v1'
                or value.get('operation_id') != operation_id
                or value.get('source_checkpoint_sha256') != checkpoint_sha
                or type(value.get('sequence')) is not int or value['sequence'] < 1
                or not isinstance(value.get('state'), str) or not value['state']
                or (value.get('exit_code') is not None and type(value['exit_code']) is not int)):
            return None
        state, exit_code = value['state'], value.get('exit_code')
        if state in _INTERMEDIATE:
            if exit_code is not None:
                return None
        elif state not in _TERMINAL_EXITS or exit_code not in _TERMINAL_EXITS[state]:
            return None
        return value
    except (TypeError, ValueError):
        return None


def configuration_command(env_path, runtime_commit, checkpoint_sha, workflow_id, operation_id, result_sha=None):
    for value, pattern in ((runtime_commit, r'[0-9a-f]{40}'),
                           (checkpoint_sha, r'[0-9a-f]{64}'),
                           (workflow_id, r'bootstrap-[0-9a-f]{32}'),
                           (operation_id, r'[0-9a-f]{32}'),
                           (env_path, r'/tmp/kace-studio-preflight-[A-Za-z0-9_-]+\.env')):
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError('Invalid configuration launch identity')
    if result_sha is not None and (not isinstance(result_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', result_sha)):
        raise ValueError('Invalid configuration result identity')

    def marker(event, code, sequence, exit_code):
        payload = dict(protocol='kace-bootstrap/v1', workflow_id=workflow_id,
                       event=event, stage='KACE', code=code, sequence=sequence,
                       exit_code=exit_code, operation_id=operation_id)
        return "printf '%s\\n' " + shlex.quote(
            '=== KACE_BOOTSTRAP_EVENT: ' + json.dumps(payload, separators=(',', ':')) + ' ===')

    result_path = f'configuration-operations/{operation_id}/result.json'
    operation_gate = (f"printf '%s  %s\\n' '{result_sha}' '{result_path}' | sha256sum --status --check"
                      if result_sha else f'[ ! -e configuration-operations/{operation_id} ]')
    capability = 'from core.configuration_operation import SCHEMA; assert SCHEMA=="kace-configuration-operation/v1"'
    complete = (
        'import hashlib,json,pathlib; '
        f'r=json.loads(pathlib.Path({result_path!r}).read_text()); '
        f'assert r["schema"]=="kace-configuration-operation/v1" and r["operation_id"]=={operation_id!r}; '
        f'assert r["source_checkpoint_sha256"]=={checkpoint_sha!r}; '
        'assert r["state"]=="COMPLETE" and type(r["exit_code"]) is int and r["exit_code"]==0; '
        'assert type(r["sequence"]) is int and r["sequence"]>0; '
        f'assert hashlib.sha256(pathlib.Path("firmware-workflow.json").read_bytes()).hexdigest()=={checkpoint_sha!r}'
    )
    return (
        'KACE_STUDIO_LAUNCH=1; ( set +x; '
        f'if . {shlex.quote(env_path)} && rm -f -- {shlex.quote(env_path)}; then '
        'if cd "$HOME/kace" && '
        f'[ "$(cat .kace-runtime-commit)" = {shlex.quote(runtime_commit)} ] && '
        f'[ "$(git rev-parse HEAD)" = {shlex.quote(runtime_commit)} ] && '
        'git diff --quiet HEAD -- && '
        f'{operation_gate} && ./venv/bin/python -c {shlex.quote(capability)} && '
        f"printf '%s  %s\\n' '{checkpoint_sha}' firmware-workflow.json | sha256sum --status --check; then "
        + marker('workflow_started', 'CONFIGURATION_STARTED', 1, 0) + '; '
        'KACE_MACHINE_OUTPUT=1 KACE_FIRMWARE_WORKFLOW_PATH="$HOME/kace/firmware-workflow.json" '
        f'./venv/bin/python ./kace.py {"--resume-configuration" if result_sha else "--new-configuration"} {operation_id} '
        f'--configuration-checkpoint-sha256 {checkpoint_sha} '
        + (f'--configuration-result-sha256 {result_sha} ' if result_sha else '') + '; kace_status=$?; '
        'if [ "$kace_status" -eq 0 ] && ./venv/bin/python -c ' + shlex.quote(complete) + '; then '
        + marker('workflow_succeeded', 'CONFIGURATION_COMPLETE', 2, 0) + '; else '
        + marker('workflow_cancelled', 'CONFIGURATION_PENDING', 2, 1) + '; fi; else '
        + marker('workflow_failed', 'CONFIGURATION_CONTRACT_MISMATCH', 1, 1)
        + '; fi; else '
        + marker('workflow_failed', 'PREFLIGHT_CONTEXT_UNAVAILABLE', 1, 1)
        + '; fi; )\n'
    )
