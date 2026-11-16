"""Guarded installed-runtime launch; KACE still owns recovery and completion."""
import json
import re
import shlex


def runtime_read_guard():
    """Keep the installer's shared lock through runtime checks, CLI and outcome."""
    return (
        'exec 8>>"$HOME/.kace-install.lock" && flock -s -n 8 && '
        '[ ! -e "$HOME/.kace-runtime-transaction.json" ] && '
        '[ ! -L "$HOME/.kace-runtime-transaction.json" ] && '
    )


def resume_command(env_path, runtime_commit, checkpoint_sha, workflow_id):
    for value, pattern in ((runtime_commit, r'[0-9a-f]{40}'),
                           (checkpoint_sha, r'[0-9a-f]{64}'),
                           (workflow_id, r'bootstrap-[0-9a-f]{32}'),
                           (env_path, r'/tmp/kace-studio-preflight-[A-Za-z0-9_-]+\.env')):
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError('Invalid recovery launch identity')

    def marker(event, code, sequence, exit_code):
        payload = dict(protocol='kace-bootstrap/v1', workflow_id=workflow_id,
                       event=event, stage='KACE', code=code,
                       sequence=sequence, exit_code=exit_code)
        return "printf '%s\\n' " + shlex.quote(
            '=== KACE_BOOTSTRAP_EVENT: ' + json.dumps(payload, separators=(',', ':')) + ' ===')

    complete = ('from core.firmware_workflow import load_checkpoint; '
                'c=load_checkpoint("firmware-workflow.json", verify_artifact=True); '
                'raise SystemExit(0 if c and c["state"]=="COMPLETE" else 1)')
    # No auto answers, no installer and no persistent shell environment. The
    # checkpoint hash binds this explicit action; KACE then validates artifacts.
    return (
        'KACE_STUDIO_LAUNCH=1; ( set +x; '
        f'if . {shlex.quote(env_path)} && rm -f -- {shlex.quote(env_path)}; then '
        'if ' + runtime_read_guard() + 'cd "$HOME/kace" && '
        f'[ "$(cat .kace-runtime-commit)" = {shlex.quote(runtime_commit)} ] && '
        f'[ "$(git rev-parse HEAD)" = {shlex.quote(runtime_commit)} ] && '
        'git diff --quiet HEAD -- && '
        f"printf '%s  %s\\n' '{checkpoint_sha}' firmware-workflow.json | sha256sum --status --check; then "
        + marker('workflow_started', '', 1, 0) + '; '
        'KACE_MACHINE_OUTPUT=1 KACE_FIRMWARE_WORKFLOW_PATH="$HOME/kace/firmware-workflow.json" '
        './venv/bin/python ./kace.py; kace_status=$?; '
        'if [ "$kace_status" -eq 0 ] && ./venv/bin/python -c ' + shlex.quote(complete) + '; then '
        + marker('workflow_succeeded', 'SUCCESS', 2, 0) + '; else '
        + marker('workflow_cancelled', 'RECOVERY_PENDING', 2, 1) + '; fi; else '
        + marker('workflow_failed', 'RECOVERY_CONTRACT_MISMATCH', 1, 1)
        + '; fi; else '
        + marker('workflow_failed', 'PREFLIGHT_CONTEXT_UNAVAILABLE', 1, 1)
        + '; fi; )\n'
    )
