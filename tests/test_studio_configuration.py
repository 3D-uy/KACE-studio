"""New configuration uses installed runtime, independent evidence and PC preflight."""
import hashlib
import json
from unittest.mock import Mock

import pytest

from backend.kace_configuration import configuration_command, parse_configuration_result
from test_firmware_checkpoint import checkpoint
from test_studio_resume import resume


def prepare(resume):
    api, session, _, engine = resume
    value = checkpoint('COMPLETE')
    session.read_text_file.return_value = json.dumps(value)
    return api, session, value, engine


def launch(items, **changes):
    api, _, value, _ = items
    args = dict(workflow_id=value['workflow_id'], sequence=value['sequence'], generation=7,
                power_context={'host': 'pi.local', 'selection': 1, 'session': 7})
    args.update(changes)
    return api.start_kace_configuration(**args)


def test_new_configuration_guards_original_complete_and_retains_approval(resume):
    items = prepare(resume)
    result = launch(items)
    assert result['status'] == 'started'
    command = items[1].commands[0]
    assert '--new-configuration ' + result['operation_id'] in command
    assert '--configuration-checkpoint-sha256 ' + hashlib.sha256(json.dumps(items[2]).encode()).hexdigest() in command
    assert 'from core.configuration_operation import SCHEMA' in command
    assert 'KACE_FIRMWARE_WORKFLOW_PATH="$HOME/kace/firmware-workflow.json"' in command
    assert 'bootstrap.sh' not in command and 'install.sh' not in command
    assert 'load_checkpoint' not in command
    assert 'configuration-operations/' + result['operation_id'] + '/result.json' in command
    assert 'git diff --quiet HEAD' in command
    assert '--auto' not in command
    items[3].prepare.assert_called_once()


@pytest.mark.parametrize('change', [{'generation': 8}, {'workflow_id': 'other'}, {'sequence': True}, {'power_context': None}])
def test_new_configuration_rejects_stale_context(resume, change):
    items = prepare(resume)
    assert launch(items, **change)['status'] == 'failed'
    assert not items[1].commands
    items[3].prepare.assert_not_called()


@pytest.mark.parametrize('state', ['DEPLOYING', 'READY_TO_DEPLOY', 'AWAITING_FLASH'])
def test_pending_installation_cannot_start_independent_configuration(resume, state):
    items = prepare(resume)
    items[1].read_text_file.return_value = json.dumps(checkpoint(state))
    assert launch(items)['status'] == 'failed'
    assert not items[1].commands


@pytest.mark.parametrize('fault', ['docker', 'checkpoint', 'session', 'used-terminal', 'send-race', 'busy'])
def test_configuration_gates_before_send(resume, fault):
    api, session, value, engine = items = prepare(resume)
    if fault == 'docker': engine.prepare.side_effect = RuntimeError('unavailable')
    if fault == 'checkpoint': engine.prepare.side_effect = lambda: setattr(session.read_text_file, 'return_value', '{}')
    if fault == 'session': engine.prepare.side_effect = lambda: setattr(api, '_ssh_gen', 8)
    if fault == 'used-terminal': session.resume_terminal_available.return_value = False
    if fault == 'send-race': session.send_resume_command = Mock(return_value=False)
    if fault == 'busy': api._bootstrap_active = True
    assert launch(items)['status'] in ('failed', 'busy')
    assert not session.commands


def test_resume_configuration_binds_own_receipt_not_old_complete(resume):
    api, session, value, engine = prepare(resume)
    raw = json.dumps(value)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    result = json.dumps(dict(schema='kace-configuration-operation/v1', operation_id='c'*32,
                            source_checkpoint_sha256=digest, sequence=5, state='FAILED', exit_code=10))
    session.read_text_file.side_effect = lambda path: result if path.endswith('/result.json') else raw
    reply = api.resume_kace_configuration('c'*32, value['workflow_id'], value['sequence'], 7,
                                         {'host':'pi.local','selection':1,'session':7})
    assert reply['status'] == 'started'
    command = session.commands[0]
    assert '--resume-configuration ' + 'c'*32 in command
    assert '--configuration-result-sha256 ' + hashlib.sha256(result.encode()).hexdigest() in command
    assert '--new-configuration' not in command


@pytest.mark.parametrize('field,bad', [('schema','old'), ('operation_id','wrong'), ('source_checkpoint_sha256','bad'),
                                     ('state','COMPLETE'), ('state','unknown'), ('exit_code',0), ('sequence',True)])
def test_result_parser_rejects_wrong_evidence(field, bad):
    value = dict(schema='kace-configuration-operation/v1', operation_id='c'*32,
                 source_checkpoint_sha256='d'*64, sequence=5, state='FAILED', exit_code=10)
    value[field] = bad
    assert parse_configuration_result(json.dumps(value),'c'*32,'d'*64) is None


def test_configuration_command_rejects_injected_operation():
    with pytest.raises(ValueError):
        configuration_command('/tmp/kace-studio-preflight-test.env','a'*40,'b'*64,'bootstrap-'+'c'*32,'../other')
