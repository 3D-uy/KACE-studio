"""Generated configuration exports remain bound to a current flow and SSH session."""
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.firmware_workflow import ALLOWED_STATES, checkpoint_event
from main import Api
from test_firmware_checkpoint import checkpoint, resigned


@pytest.fixture
def export(tmp_path):
    api=Api()
    api._ssh_gen=7
    value=checkpoint('READY_TO_DEPLOY')
    value['wizard_data']['macros_generated']=True
    resigned(value)
    texts={'kace/printer.cfg':'# Generated\r\n[mcu]\r\nserial: example\r\n',
           'kace/macros.cfg':'# Macros: configuración\n[gcode_macro TEST]\ngcode:\n M118 test\n'}
    session=Mock()
    session.is_connected.return_value=True
    session.read_text_file.side_effect=lambda name: json.dumps(value)
    session.read_text_file_result.side_effect=lambda name,**kw: ('ok',texts[name]) if name in texts else ('absent',None)
    api._ssh=session
    api._window=Mock()
    dest=tmp_path/'configuration.zip'
    api._window.create_file_dialog.return_value=(str(dest),)
    return api,value,texts,dest


@pytest.mark.parametrize('macros',[True,False,None])
def test_export_zip_has_exact_generated_files_and_ignores_stale_unselected_macros(export,macros):
    api,value,texts,dest=export
    value['wizard_data']['macros_generated']=macros
    resigned(value)
    original=json.dumps(value)
    assert api.download_kace_configuration(value['workflow_id'],value['sequence'],7)=={'status':'success'}
    expected=['printer.cfg','macros.cfg'] if macros is True else ['printer.cfg']
    with zipfile.ZipFile(dest) as archive:
        assert archive.namelist()==expected
        for name in expected: assert archive.read(name)==texts['kace/'+name].encode()
    assert json.dumps(value)==original
    assert all(call.args[0] in ['kace/'+n for n in expected] for call in api._ssh.read_text_file_result.call_args_list)


@pytest.mark.parametrize('mutation',['generation','session','checkpoint','files','disconnect'])
def test_dialog_cannot_publish_after_session_or_generated_files_change(export,mutation):
    api,value,texts,dest=export
    dest.write_bytes(b'existing-backup')
    def choose(*a,**kw):
        if mutation=='generation': api._ssh_gen+=1
        if mutation=='session': api._ssh=Mock()
        if mutation=='checkpoint':
            value['sequence']+=1
            resigned(value)
        if mutation=='files': texts['kace/printer.cfg']='different generated files'
        if mutation=='disconnect': api._ssh.is_connected.return_value=False
        return str(dest)
    api._window.create_file_dialog.side_effect=choose
    sequence=value['sequence']
    result=api.download_kace_configuration(value['workflow_id'],sequence,7)
    assert result=={'status':'error','code':'configurationDownloadChanged'}
    assert dest.read_bytes()==b'existing-backup'
    assert not list(dest.parent.glob('.kace-config-*'))


@pytest.mark.parametrize('missing',['kace/printer.cfg','kace/macros.cfg'])
def test_missing_selected_file_fails_without_partial_zip_or_save_dialog(export,missing):
    api,value,texts,dest=export
    texts.pop(missing)
    assert api.download_kace_configuration(value['workflow_id'],value['sequence'],7)=={'status':'error','code':'configurationDownloadFailed'}
    api._window.create_file_dialog.assert_not_called()
    assert not dest.exists()


def test_cancelled_save_has_no_output(export):
    api,value,texts,dest=export
    api._window.create_file_dialog.return_value=None
    assert api.download_kace_configuration(value['workflow_id'],value['sequence'],7)=={'status':'cancelled'}
    assert not dest.exists()


@pytest.mark.parametrize('state',sorted(ALLOWED_STATES))
def test_configuration_button_projection_requires_configuration_generated_stage(state):
    value=checkpoint(state)
    assert checkpoint_event(value)['data']['configuration_download_available'] == (state in {'CONFIG_GENERATED','READY_TO_DEPLOY','DEPLOYING','COMPLETE'})


@pytest.mark.parametrize('change',['old_generation','wrong_flow','wrong_sequence','early_state','invalid_checkpoint','bool_generation'])
def test_stale_or_unvalidated_requests_cannot_export(export,change):
    api,value,texts,dest=export
    workflow,sequence,generation=value['workflow_id'],value['sequence'],7
    if change=='old_generation': generation=6
    if change=='bool_generation': generation=True
    if change=='wrong_flow': workflow='other-flow'
    if change=='wrong_sequence': sequence+=1
    if change=='early_state':
        value['state']='ARTIFACT_READY'
        resigned(value)
    if change=='invalid_checkpoint': value['integrity_sha256']='0'*64
    assert api.download_kace_configuration(workflow,sequence,generation)['status']=='error'
    api._window.create_file_dialog.assert_not_called()
    api._ssh.read_text_file_result.assert_not_called()
    assert not dest.exists()


def test_failed_zip_publication_preserves_existing_download_and_cleans_temporary(export,monkeypatch):
    api,value,texts,dest=export
    dest.write_bytes(b'previous')
    monkeypatch.setattr('main.os.replace',Mock(side_effect=OSError('synthetic private exception')))
    assert api.download_kace_configuration(value['workflow_id'],value['sequence'],7)=={'status':'error','code':'configurationDownloadFailed'}
    assert dest.read_bytes()==b'previous'
    assert not list(dest.parent.glob('.kace-config-*'))


@pytest.mark.parametrize('case',['configuration_download_lifecycle','configuration_download_disconnect','configuration_download_cancel_failure'])
def test_configuration_download_production_frontend(case):
    root=Path(__file__).resolve().parents[1]
    node=shutil.which('node')
    assert node, 'Node.js is required for this download regression'
    result=subprocess.run([node,str(root/'tests/bootstrap_session_harness.cjs'),str(root/'web/app.js'),case],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.strip().endswith('completed')
