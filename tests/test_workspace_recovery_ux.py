"""Exercise connection, pending verification and compact tracker in production JS."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("language", ["English", "Español", "Português"])
def test_workspace_recovery_keeps_authority_and_pending_work_distinct(language):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend execution")
    app = Path(__file__).resolve().parents[1] / "web/app.js"
    program = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(), output=[], calls=[];
function element(){const classes=new Set();return {dataset:{},style:{},children:[],textContent:'',value:'',disabled:false,
 classList:{add(c){classes.add(c)},remove(c){classes.delete(c)},toggle(c,on){if(on)classes.add(c);else classes.delete(c)},contains(c){return classes.has(c)}},setAttribute(){},removeAttribute(){},
 replaceChildren(){this.children=[]},appendChild(item){this.children.push(item)},querySelector(){return this}};}
const context={console,assert,nodes,output,calls,language:process.argv[3],navigator:{language:'en'},
 localStorage:{getItem(){return null},setItem(){}},addEventListener(){},setTimeout(){},clearTimeout(){},
 setInterval(){return 1},clearInterval(){},
 document:{documentElement:{},activeElement:{id:'terminal-focus'},addEventListener(){},
 querySelectorAll(){return []},createElement:element,
 getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)}}};
context.window=context;
context.pywebview={api:{power_on(){calls.push('on')},power_off(){calls.push('off')},send_ssh_input(){calls.push('input')}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
vm.runInContext(`
selectedStudioLanguage=language;
refreshSftpBrowser=()=>{};clearSftpSelection=()=>{};
term={write(text){output.push(text)}};
currentDeviceIp='pi.local';currentDeviceName='Pi';powerSelection=3;
updateConnectionStatus(true,{newSession:true});firmwareGeneration=10;
const authority={host:'pi.local',selection:3,session:10};powerContext=authority;
remotePowerAuthority={status:'configured'};
const event={schema:2,workflow_kind:'firmware_deployment',workflow_id:'install',sequence:9,
 state:'DEPLOYING',data:{language}};
updateKaceWorkflowEvent(event,10);
const history=nodes.get('kace-workflow-history-label');
assert.equal(history.textContent,workflowText('history',kaceWorkflowViews.get('install')));
assert.equal(nodes.get('kace-workflow-detail').hidden,true);
const historyPanel=nodes.get('kace-workflow-history');historyPanel.open=true;
updateKaceWorkflowEvent({...event,sequence:10},10);
assert.equal(historyPanel.open,true,'same-flow updates retain the history disclosure');
const focus=document.activeElement;
// Only KACE's authoritative archive event hides a pending previous flow.
const retired={...event,workflow_id:'retired',sequence:1,state:'ARTIFACT_READY'};
updateKaceWorkflowEvent(retired,10);
assert.equal(nodes.get('kace-workflow-tracker').style.display,'block');
assert.equal(updateKaceWorkflowEvent({...retired,sequence:2,state:'DISCARDED'},9),false);
assert.equal(nodes.get('kace-workflow-tracker').style.display,'block');
assert.equal(updateKaceWorkflowEvent({...retired,sequence:2,state:'DISCARDED'},10),true);
assert.equal(nodes.get('kace-workflow-tracker').style.display,'none');
assert.equal(nodes.get('kace-firmware-download').dataset.remotePath,'');
assert.equal(kaceWorkflowViews.get('retired').state,'DISCARDED');
assert.equal(updateKaceWorkflowEvent({...retired,sequence:1},10,true),false);
assert.equal(updateKaceWorkflowEvent({...retired,sequence:99},10),false);
updateKaceWorkflowEvent({...event,sequence:11},10);
assert.equal(nodes.get('kace-workflow-tracker').style.display,'block');
assert.equal(updateKaceWorkflowEvent({...retired,sequence:3,state:'DISCARDED'},10),false);
assert.equal(nodes.get('kace-workflow-tracker').style.display,'block');
assert.equal(updateKaceWorkflowEvent({...retired,workflow_id:'unshown-archive',sequence:2,state:'DISCARDED'},10),true);
assert.equal(nodes.get('kace-workflow-tracker').dataset.workflowId,'install');
assert.equal(nodes.get('kace-workflow-tracker').style.display,'block','an unrelated archived ID does not hide the active flow');
updateTerminalClosed(9);assert.equal(terminalShellOpen,true);
updateTerminalClosed(10);assert.equal(terminalShellOpen,false);
assert.equal(sshConnected,true);assert.equal(powerContext,authority);
assert.ok(output.at(-1).includes(studioText('terminalClosed')));
updateSshDisconnected('raw diagnostic VERIFYING_CONFIG',true);
assert.equal(sshConnected,false);assert.equal(powerContext,null);
assert.equal(printerPowerStatus,'unknown');
assert.equal(nodes.get('printer-power-state').textContent,studioText('powerState_unknown'));
assert.equal(nodes.get('printer-power-btn').ariaLabel,studioText('powerLabel')+': '+studioText('powerState_unknown'));
assert.equal(nodes.get('printer-power-btn').disabled,true);
assert.equal(nodes.get('connection-subtitle').textContent,studioText('sshLost'));
assert.equal(nodes.get('kace-workflow-status').textContent,workflowText('disconnected',kaceWorkflowViews.get('install')));
restoreSshAfterReconnect({status:'success',generation:11},3,{generation:11,event,checkpoint:{state:'DEPLOYING'}});
assert.equal(kaceWorkflowViews.get('install').sequence,11,'a stale initial checkpoint cannot replace observed progress');
restoreSshAfterReconnect({status:'success',generation:11},3,{generation:11,event:{...event,sequence:12},checkpoint:{state:'DEPLOYING'}});
assert.equal(sshConnected,true);assert.equal(terminalShellOpen,true);
const pending=kaceWorkflowViews.get('install');assert.equal(pending.resumePending,true);
assert.equal(pending.state,'DEPLOYING');
assert.equal(nodes.get('kace-workflow-detail').textContent,workflowText('resumeDetail',pending));
assert.equal(nodes.get('kace-workflow-status').textContent,workflowText('recovery',pending));
assert.equal(nodes.get('kace-workflow-tracker').classList.contains('error'),false);
assert.equal(nodes.get('kace-workflow-tracker').classList.contains('action-required'),true);
assert.equal(nodes.get('kace-workflow-current').textContent,workflowStep('Deployment verification',pending));
assert.equal(document.activeElement,focus);
updateKaceWorkflowEvent({...event,sequence:13,state:'READY_TO_DEPLOY'},11);
assert.equal(kaceWorkflowViews.get('install').resumePending,false);
updateKaceWorkflowEvent({...event,sequence:14,state:'READY_TO_DEPLOY',data:{language,last_error:'simulated verification failure'}},11);
assert.equal(nodes.get('kace-workflow-tracker').classList.contains('error'),true);
assert.equal(nodes.get('kace-workflow-detail').hidden,false);
updateKaceWorkflowEvent({...event,sequence:15,state:'COMPLETE'},10);
assert.equal(kaceWorkflowViews.get('install').state,'READY_TO_DEPLOY');
updateKaceWorkflowEvent({...event,sequence:15,state:'COMPLETE',data:{language,last_error:''}},11);
assert.equal(nodes.get('kace-workflow-status').textContent,workflowText('completed',pending));
historyPanel.open=true;
updateKaceWorkflowEvent({...event,workflow_id:'new-install',sequence:1,state:'HARDWARE_SELECTED',data:{language}},11);
assert.equal(historyPanel.open,false,'new KACE workflow starts compact');
assert.equal(nodes.get('kace-workflow-tracker').dataset.workflowId,'new-install');
assert.equal(nodes.get('kace-workflow-tracker').classList.contains('error'),false);
assert.equal(nodes.get('kace-workflow-detail').hidden,true);
assert.equal(kaceWorkflowViews.get('install').state,'COMPLETE','prior evidence is retained');
assert.equal(calls.length,0);
`,context);
'''
    result = subprocess.run([node, "-", str(app), language], input=program, text=True,
                            encoding="utf-8", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
