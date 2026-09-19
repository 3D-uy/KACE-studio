import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from backend.firmware_workflow import ALLOWED_STATES, checkpoint_event


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'web/app.js').read_text(encoding='utf-8')


@pytest.mark.parametrize('state', sorted(ALLOWED_STATES))
def test_checkpoint_projects_error_and_valid_actions(state):
    event = checkpoint_event({'hardware': {'board': 'SKR', 'mcu': 'lpc1769'},
        'state': state, 'workflow_id': 'flow', 'sequence': 2,
        'last_error': 'concurrent edit', 'artifact': {'path': '/firmware.bin'},
        'wizard_data': {'language': 'Español'}})
    assert event['data']['last_error'] == 'concurrent edit'
    assert event['data']['language'] == 'Español'
    assert event['data']['download_available'] == (state in {'ARTIFACT_READY', 'AWAITING_FLASH', 'VERIFYING_MCU'})


def run_js(program):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for production frontend regression tests')
    catalog = APP[APP.index('const STUDIO_INSTALLATION_TEXT'):APP.index("document.addEventListener('DOMContentLoaded', () => {", APP.index('const STUDIO_INSTALLATION_TEXT'))]
    program = program.replace("const assert = require('node:assert/strict');", catalog + "\nconst assert = require('node:assert/strict');")
    result = subprocess.run([node, '-'], input=program, text=True, encoding='utf-8', capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


HARNESS = r'''
const assert = require('node:assert/strict');
const timers = [];
function element() { return {style: {}, dataset: {}, textContent: '', children: [],
 classList: {toggle(){}, add(){}, remove(){}},
 replaceChildren(){this.children=[]}, appendChild(item){this.children.push(item)},
 querySelector(){return this}, innerHTML: ''}; }
const nodes = new Map();
const document = {getElementById(id){if(!nodes.has(id)) nodes.set(id, element());return nodes.get(id)}, createElement: element};
const window = {setTimeout(fn){timers.push(fn)}, setInterval(fn){timers.push(fn);return 1}, clearInterval(){}};
const setTimeout = window.setTimeout;
let fitAddon = null, sshConnected = true;
'''


def test_live_checkpoint_panel_and_download_visibility_without_reconnect():
    workflow = APP[APP.index('const KACE_INSTALLATION_STEPS'):APP.index('function restoreKaceDeploymentManifest')]
    observer = APP[APP.index('let checkpointWatchToken'):APP.index('let firstBootDiscovery')]
    run_js(HARNESS + workflow + observer + r'''
firmwareGeneration = 7;
function event(sequence, state, error='') {return {schema:2, workflow_kind:'firmware_deployment', workflow_id:'one', sequence, state,
 data:{language:'Español', last_error:error, staged_path:'/internal/firmware.bin'}};}
window.updateKaceWorkflowEvent(event(1,'AWAITING_FLASH'),7);
assert.equal(nodes.get('kace-firmware-download').style.display,'inline-flex');
for (const [i,state] of ['MCU_VERIFIED','CONFIG_GENERATED','READY_TO_DEPLOY','DEPLOYING'].entries()) {
 window.updateKaceWorkflowEvent(event(i+2,state),7);
 assert.equal(nodes.get('kace-firmware-download').style.display,'none');
}
window.updateKaceWorkflowEvent(event(6,'READY_TO_DEPLOY','conflict'),7);
assert.equal(nodes.get('kace-workflow-status').textContent,'Recuperación necesaria');
assert(!nodes.get('kace-workflow-detail').textContent.includes('/internal'));
window.pywebview = {api:{async get_firmware_workflow_checkpoint(){return {generation:7,event:event(7,'COMPLETE')};}}};
timers.length=0;
startFirmwareCheckpointWatch();
(async()=>{
 await timers.shift()();
 assert.equal(nodes.get('kace-workflow-status').textContent,'Completada');
 assert(!nodes.get('kace-workflow-advanced').textContent.includes('conflict'));
 assert.equal(nodes.get('kace-firmware-download').style.display,'none');
 for(const language of Object.keys(WORKFLOW_TEXT)) assert.deepEqual(Object.keys(WORKFLOW_TEXT[language]),Object.keys(WORKFLOW_TEXT.English));
})().catch(error=>{console.error(error);process.exitCode=1});
''')


def test_first_boot_discovery_is_bounded_stoppable_and_serial():
    discovery = APP[APP.index('let firstBootDiscovery'):APP.index('const STUDIO_INSTALLATION_TEXT')]
    run_js(HARNESS + r'''
let now=100000, scans=0, discoveryScanInFlight=false;
Date.now=()=>now;
function triggerScan(){scans++;discoveryScanInFlight=true;}
''' + discovery + r'''
startFirstBootDiscovery();
assert.equal(scans,1);
now+=20000;timers[0]();assert.equal(scans,1);
discoveryScanInFlight=false;timers[0]();assert.equal(scans,2);
now+=600000;timers[0]();assert.equal(firstBootDiscovery,null);
startFirstBootDiscovery();stopFirstBootDiscovery();assert.equal(firstBootDiscovery,null);
assert.equal(nodes.get('stop-first-boot-scan').hidden,true);
''')


def test_power_before_bootstrap_reports_unconfigured_endpoint_as_pending(monkeypatch):
    from main import Api
    api = Api()
    api._remote_power_authority = {'status': 'absent', 'config': None}
    monkeypatch.setattr(api, '_require_power_context_locked', lambda *_: None)
    monkeypatch.setattr(api, '_resolve_power_device', lambda _: 'printer')
    from backend.power_controller import PowerControllerError
    controller = Mock()
    controller.return_value.get_status.side_effect = PowerControllerError("POWER_DEVICE 'printer' is not configured in Moonraker")
    monkeypatch.setattr('main.MoonrakerPowerController', controller)
    result = api.get_power_status('pi.local', 'printer', {'host': 'pi.local', 'selection': 1, 'session': 1})
    assert result['status'] == 'pending'
    assert result['available'] is False
    controller.return_value.power_on.assert_not_called()


def test_imaging_verification_title_matches_detail():
    code = APP[APP.index('function updateProgress'):APP.index('// Network Discovery')]
    run_js(HARNESS + code + r'''
for(const percent of [0,50,100]) {
 updateProgress(percent,'Verifying pre-baked image identity','VERIFYING_IMAGE');
 assert(nodes.get('btn-text-content').textContent.startsWith(studioText('verifying')));
 assert(!nodes.get('btn-text-content').textContent.includes('Writing'));
}
''')
