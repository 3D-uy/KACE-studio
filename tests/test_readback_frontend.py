"""Execute the real completion handler/modal and locale switch with fake DOM."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_readback_modal_uses_current_backend_evidence_in_all_languages():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend execution")
    app = Path(__file__).resolve().parents[1] / "web/app.js"
    program = r'''
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const nodes=new Map();
function element(id) {
    return {id,dataset:{},style:{},textContent:'',value:'3',open:false,checked:false,
        classList:{add(){},remove(){}},setAttribute(){},removeAttribute(){},
        showModal(){this.open=true;},close(){this.open=false;},focus(){document.activeElement=this;}};
}
for(const id of ['success-modal','success-done-btn','flash-action-btn','flash-status-container',
    'btn-progress-fill','eject-btn','cancel-flash-btn','flash-readback-result','flash-readback-scope',
    'flash-next-steps','drive-select','skip-write-verification']) nodes.set(id,element(id));
const document={documentElement:{},activeElement:null,addEventListener(){},
    getElementById:id=>nodes.get(id)||null,
    querySelectorAll:selector=>selector==='[data-ui-text]'?[...nodes.values()].filter(n=>n.dataset.uiText):[]};
const context={console,assert,document,nodes,navigator:{language:'en-US'},addEventListener(){},
    localStorage:{getItem(){return null;},setItem(){}},Event:class{}};
context.window=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
vm.runInContext(`
updateTrackerBar=()=>{};updateProgress=()=>{};startFirstBootDiscovery=()=>{};
playFlashCompletionSound=()=>{};showFlashNotification=()=>{};
const identity={number:3,serial_number:'SD-A',path:'disk-a',size_bytes:4096,unique_id:'image-a'};
const operation='a'.repeat(32);
const payload={schema:'kace-studio-readback/v1',operation_id:operation,disk_identity:identity,status:'verified'};
function begin(id=operation) {
    window.activeFlashOperation={operation_id:id,disk_identity:{...identity}};
    driveIdentitySnapshots.set('3',{...identity});
    nodes.get('drive-select').value='3';
    nodes.get('success-modal').close();
}
for(const language of ['English','Español','Português']) {
    setStudioLanguage(language,false);
    for(const status of ['verified','skipped']) {
        begin(); nodes.get('skip-write-verification').checked=status==='verified';
        window.updateDeviceState('FLASHED',100,'complete',{...payload,status});
        assert.equal(nodes.get('success-modal').open,true);
        assert.equal(nodes.get('flash-readback-result').textContent,studioText(status==='verified'?'readbackVerified':'readbackSkipped'));
        assert.ok(nodes.get('flash-readback-result').textContent.length>15);
        assert.equal(nodes.get('flash-readback-scope').textContent,studioText('readbackScope'));
        assert.equal(nodes.get('flash-next-steps').textContent,studioText('flashNextSteps'));
        const next=language==='English'?'Português':'English';
        setStudioLanguage(next,false);
        assert.equal(nodes.get('flash-readback-result').textContent,studioText(status==='verified'?'readbackVerified':'readbackSkipped'));
        setStudioLanguage(language,false);
    }
    for(const result of [null,{...payload,status:'unknown'},{...payload,operation_id:'b'.repeat(32)},
        {...payload,disk_identity:{...identity,serial_number:'SD-B'}}]) {
        begin();window.updateDeviceState('FLASHED',100,'complete',result);
        assert.equal(nodes.get('success-modal').open,false,'missing/invalid/stale evidence');
    }
    begin();nodes.get('drive-select').value='4';
    window.updateDeviceState('FLASHED',100,'complete',payload);
    assert.equal(nodes.get('success-modal').open,false,'changed selected disk');
    begin();window.updateDeviceState('ERROR',95,'injection failed',{operation_id:operation,disk_identity:identity});
    window.updateDeviceState('FLASHED',100,'late success',payload);
    assert.equal(nodes.get('success-modal').open,false,'error must invalidate late completion');
    begin();term={write(){}};updateConnectionStatus=()=>{};showTroubleshootingPrompt=()=>{};
    activeTab='terminal-tab';
    window.updateDeviceState('CONNECTING',0,'connecting');
    window.updateDeviceState('ERROR',0,'SSH connection failed');
    assert.equal(window.activeFlashOperation.operation_id,operation,'SSH failure must preserve flash identity');
    window.updateDeviceState('FLASHED',100,'complete',payload);
    assert.equal(nodes.get('success-modal').open,true,'valid flash completion survives unrelated SSH failure');
}
`,context);
'''
    result = subprocess.run([node, "-", str(app)], input=program, text=True,
                            encoding="utf-8", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
