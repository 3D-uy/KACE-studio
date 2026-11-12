"""A pending installation resumes through the PC validator, never bare kace."""
import hashlib
import json
from unittest.mock import Mock

import pytest

import main
from test_firmware_checkpoint import checkpoint
from test_studio_preflight_launch import Session, PATH


@pytest.fixture
def resume(monkeypatch):
    value = checkpoint('DEPLOYING')
    raw = json.dumps(value)
    session = Session()
    session.resume_terminal_available = Mock(return_value=True)
    session.send_resume_command = session.send_input
    session.read_text_file = Mock(return_value=raw)
    api = main.Api()
    api._ssh = session
    api._ssh_gen = 7
    api.set_device_state = Mock()
    api._require_power_context_locked = Mock()
    monkeypatch.setattr(main, 'require_release_ready', lambda: None)
    monkeypatch.setattr(main, 'load_release_contract', lambda: {'kace': {'candidate_ref': 'a'*40, 'bootstrap_sha256': 'b'*64}})
    engine = Mock()
    monkeypatch.setattr(main, 'DockerPreflight', Mock(return_value=engine))
    return api, session, value, engine


def invoke(resume, **changes):
    api, _, value, _ = resume
    args = dict(workflow_id=value['workflow_id'], sequence=value['sequence'], generation=7,
                power_context={'host':'pi.local','selection':1,'session':7})
    args.update(changes)
    return api.resume_kace_installation(**args)


def test_resume_restores_pc_context_without_reinstall_or_bootstrap(resume):
    api, session, value, engine = resume
    assert invoke(resume)['status'] == 'started'
    engine.prepare.assert_called_once()
    command = session.commands[0]
    assert PATH in command and 'KACE_STUDIO_PREFLIGHT_TOKEN' not in command
    assert 'bootstrap.sh' not in command and 'install.sh' not in command
    assert 'git rev-parse HEAD' in command and 'git diff --quiet HEAD' in command
    assert 'venv/bin/python' in command and 'kace.py' in command
    assert hashlib.sha256(json.dumps(value).encode()).hexdigest() in command
    assert 'workflow_succeeded' in command and 'COMPLETE' in command
    assert api._bootstrap_active


@pytest.mark.parametrize('change', [{'generation':6}, {'sequence':-1}, {'sequence':True}, {'workflow_id':'other'}, {'power_context':None}])
def test_resume_rejects_stale_or_missing_authority_without_launch(resume, change):
    assert invoke(resume, **change)['status'] == 'failed'
    assert not resume[1].commands
    resume[3].prepare.assert_not_called()


@pytest.mark.parametrize('raw', [None, '{}', 'bad', json.dumps(checkpoint('COMPLETE'))])
def test_resume_requires_valid_pending_checkpoint(resume, raw):
    resume[1].read_text_file.return_value = raw
    assert invoke(resume)['status'] == 'failed'
    assert not resume[1].commands


def test_resume_changed_checkpoint_during_prepare_revokes_its_bridge(resume):
    api, session, _, engine = resume
    engine.prepare.side_effect = lambda: setattr(session.read_text_file, 'return_value', '{}')
    assert invoke(resume)['status'] == 'failed'
    assert not session.commands
    assert not api._bootstrap_active


def test_resume_changed_session_during_prepare_cannot_launch(resume):
    api, session, _, engine = resume
    engine.prepare.side_effect = lambda: setattr(api, '_ssh_gen', 8)
    assert invoke(resume)['status'] == 'failed'
    assert not session.commands


def test_resume_docker_unavailable_does_not_fallback(resume):
    resume[3].prepare.side_effect = RuntimeError('Docker unavailable')
    assert invoke(resume)['status'] == 'failed'
    assert not resume[1].commands


def test_resume_busy_preserves_current_bridge(resume):
    resume[0]._bootstrap_active = True
    resume[0]._bootstrap_workflow_id = 'existing'
    assert invoke(resume)['status'] == 'busy'
    assert not resume[1].commands and not resume[1].stops


def test_resume_checkpoint_read_failure_never_launches(resume):
    resume[1].read_text_file.side_effect = OSError('read unavailable')
    assert invoke(resume)['status'] == 'failed'
    assert not resume[1].commands


def test_resume_used_terminal_requires_reconnect_before_preparation(resume):
    resume[1].resume_terminal_available.return_value = False
    result = invoke(resume)
    assert result['code'] == 'RESUME_RECONNECT_REQUIRED'
    assert not resume[1].commands
    resume[3].prepare.assert_not_called()


def test_resume_keyboard_input_during_prepare_cannot_receive_shell_command(resume):
    resume[1].send_resume_command = Mock(return_value=False)
    assert invoke(resume)['status'] == 'failed'
    assert not resume[1].commands
    assert not resume[0]._bootstrap_active


@pytest.mark.parametrize('frozen', [False, True])
def test_source_and_packaged_resume_use_installed_runtime_not_bootstrap(resume, monkeypatch, frozen):
    monkeypatch.setattr(main.sys, 'frozen', frozen, raising=False)
    assert invoke(resume)['status'] == 'started'
    assert './venv/bin/python ./kace.py' in resume[1].commands[0]
    assert '/boot/' not in resume[1].commands[0]
