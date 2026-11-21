"""Launch one new configuration using the installed, pinned KACE runtime."""
import json
import re
import shlex
import hashlib
import posixpath
import stat
import threading
import time

from backend.kace_resume import runtime_read_guard

_INTERMEDIATE = frozenset({'PREPARING', 'BACKUP', 'APPLYING_CONFIG', 'VERIFYING_UPLOAD',
                          'WAITING_MOONRAKER', 'FIRMWARE_RESTART', 'VERIFYING_CONFIG', 'DONE', 'CONFIG_ERROR'})
_TERMINAL_EXITS = {'COMPLETE': {0}, 'CANCELLED': {2}, 'PENDING_ACTIVATION': {41}, 'FAILED': {10, 20, 30, 40}}
DISCOVERY_TIMEOUT_SECONDS = 10

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
    except (TypeError, ValueError, RecursionError):
        return None


def discover_configuration_operations(session, checkpoint_sha, *, authorize=None):
    """Read a bounded receipt inventory; never execute, repair or choose an operation."""
    if not isinstance(checkpoint_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', checkpoint_sha):
        raise ValueError('Invalid checkpoint digest')
    if authorize:
        authorize()
    sftp = session.get_sftp()
    if sftp is None:
        raise OSError('SFTP is unavailable')
    deadline = time.monotonic() + DISCOVERY_TIMEOUT_SECONDS
    channel = None
    timer = None
    result = {'operations': [], 'rejected': 0, 'truncated': False}

    def guard():
        if authorize:
            authorize()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Configuration discovery timed out; no recovery was started')
        channel.settimeout(remaining)

    def read_file(path):
        guard()
        attrs = sftp.lstat(path)
        if (not stat.S_ISREG(attrs.st_mode) or attrs.st_size < 0 or attrs.st_size > 32 * 1024
                or sftp.normalize(path) != path):
            raise ValueError('Receipt or manifest is not a bounded regular file')
        with sftp.open(path, 'rb') as source:
            payload = source.read(32 * 1024 + 1)
        guard()
        if len(payload) > 32 * 1024:
            raise ValueError('Receipt or manifest is oversized')
        return payload

    try:
        channel = sftp.get_channel()
        timer = threading.Timer(DISCOVERY_TIMEOUT_SECONDS, channel.close)
        timer.daemon = True
        timer.start()
        guard()
        home = sftp.normalize('.')
        root = posixpath.join(home, 'kace', 'configuration-operations')
        try:
            attrs = sftp.lstat(root)
        except FileNotFoundError:
            return result
        if not stat.S_ISDIR(attrs.st_mode) or sftp.normalize(root) != root:
            raise ValueError('Configuration operation directory is not a regular directory')
        for index, entry in enumerate(sftp.listdir_iter(root, read_aheads=1)):
            guard()
            if index >= 128 or len(result['operations']) >= 64:
                result['truncated'] = True
                break
            identity = entry.filename
            if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
                result['rejected'] += 1
                continue
            directory = posixpath.join(root, identity)
            try:
                if not stat.S_ISDIR(sftp.lstat(directory).st_mode) or sftp.normalize(directory) != directory:
                    raise ValueError('Operation path is not a regular directory')
                payload = read_file(posixpath.join(directory, 'result.json'))
                receipt = parse_configuration_result(payload.decode('utf-8'), identity, checkpoint_sha)
                if receipt is None:
                    raise ValueError('Receipt does not match this installation')
            except (OSError, UnicodeError, ValueError):
                result['rejected'] += 1
                continue
            reason = 'complete' if receipt['state'] == 'COMPLETE' else 'manifest_unavailable'
            if reason != 'complete':
                digest = receipt.get('generated_manifest_sha256')
                if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest):
                    try:
                        manifest_bytes = read_file(posixpath.join(directory, 'generated.json'))
                        manifest = json.loads(manifest_bytes)
                        required = {'printer.cfg', 'printer.cfg.provenance.json'}
                        if (hashlib.sha256(manifest_bytes).hexdigest() == digest
                                and isinstance(manifest, dict) and required <= set(manifest)
                                and set(manifest) <= required | {'macros.cfg'}
                                and all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                                        for value in manifest.values())):
                            reason = ''
                    except (OSError, ValueError, UnicodeError, RecursionError):
                        pass
            result['operations'].append({'operation_id': identity, 'state': receipt['state'],
                'sequence': receipt['sequence'], 'recoverable': not reason, 'reason': reason,
                'result_sha256': hashlib.sha256(payload).hexdigest()})
        guard()
        result['operations'].sort(key=lambda item: item['operation_id'])
        return result
    finally:
        if timer is not None:
            timer.cancel()
        sftp.close()


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
    capability = ('from core.configuration_operation import SCHEMA; '
                  'raise SystemExit(SCHEMA != "kace-configuration-operation/v1")')
    complete = (
        'import hashlib,json,pathlib; '
        f'r=json.loads(pathlib.Path({result_path!r}).read_text()); '
        'valid = (r["schema"]=="kace-configuration-operation/v1" '
        f'and r["operation_id"]=={operation_id!r} and r["source_checkpoint_sha256"]=={checkpoint_sha!r} '
        'and r["state"]=="COMPLETE" and type(r["exit_code"]) is int and r["exit_code"]==0 '
        'and type(r["sequence"]) is int and r["sequence"]>0 '
        f'and hashlib.sha256(pathlib.Path("firmware-workflow.json").read_bytes()).hexdigest()=={checkpoint_sha!r}); '
        'raise SystemExit(not valid)'
    )
    return (
        'KACE_STUDIO_LAUNCH=1; ( set +x; '
        f'if . {shlex.quote(env_path)} && rm -f -- {shlex.quote(env_path)}; then '
        'if ' + runtime_read_guard() + 'cd "$HOME/kace" && '
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
