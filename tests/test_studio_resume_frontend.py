"""Exercise the explicit recovery action and truthful firmware label in app.js."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('language', ['English', 'Español', 'Português'])
def test_resume_is_explicit_session_bound_and_never_claims_completion(language):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    program = r'''
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const nodes=new Map(), calls=[], output=[];
function element(){return {dataset:{},style:{},children:[],textContent:'',value:'',disabled:false,
classList:{add(){},remove(){},toggle(){}},setAttribute(){},removeAttribute(){},replaceChildren(){this.children=[]},appendChild(x){this.children.push(x)},querySelector(){return this}};}
const c={console,nodes,calls,output,assert,language:process.argv[3],navigator:{language:'en'},
localStorage:{getItem(){return null},setItem(){}},addEventListener(){},setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},
document:{documentElement:{},addEventListener(){},querySelectorAll(){return []},createElement:element,getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)}}};
c.window=c;
c.pywebview={api:{async resume_kace_installation(...a){calls.push(a);return {status:'started'};}}};
vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),c);
(async()=>{await vm.runInContext(`(async()=>{
selectedStudioLanguage=language;term={write(t){output.push(t)}};
sshConnected=true;terminalShellOpen=true;firmwareGeneration=7;powerSelection=1;currentDeviceIp='pi.local';
powerContext={host:'pi.local',selection:1,session:7};
const event={schema:2,workflow_kind:'firmware_deployment',workflow_id:'pending',sequence:10,state:'DEPLOYING',
data:{firmware_authority:'durable_checkpoint',language}};
updateKaceWorkflowEvent(event,7,true);
const button=nodes.get('kace-resume-installation');
assert.ok(button,'explicit resume action'); assert.equal(button.disabled,false);assert.equal(calls.length,0);
await resumeKaceInstallation();
assert.equal(calls.length,1);assert.equal(JSON.stringify(calls[0]),JSON.stringify(['pending',10,7,powerContext]));
assert.equal(kaceWorkflowViews.get('pending').state,'DEPLOYING');assert.equal(button.disabled,true);
assert.equal(nodes.get('bootstrap-btn').disabled,true);
await resumeKaceInstallation();assert.equal(calls.length,1,'double click blocked');
bootstrapActive=false;sshConnected=false;renderKaceWorkflow(kaceWorkflowViews.get('pending'));
assert.equal(button.disabled,true);await resumeKaceInstallation();assert.equal(calls.length,1);
sshConnected=true;firmwareGeneration=8;renderKaceWorkflow(kaceWorkflowViews.get('pending'));
assert.equal(button.disabled,true);await resumeKaceInstallation();assert.equal(calls.length,1);
firmwareGeneration=7;updateKaceWorkflowEvent({...event,sequence:11,state:'COMPLETE'},7,true);
assert.equal(button.style.display,'none');
updateKaceWorkflowEvent({...event,workflow_id:'flash',sequence:1,state:'AWAITING_FLASH'},7,true);
assert.equal(nodes.get('kace-workflow-current').textContent,workflowText('phase_AWAITING_FLASH',kaceWorkflowViews.get('flash')));
assert.ok(!/instalado|installed|instalado/i.test(nodes.get('kace-workflow-current').textContent));
const flash=kaceWorkflowViews.get('flash');
flash.recoveryObserving=true;renderKaceWorkflow(flash);assert.equal(button.disabled,true);
flash.recoveryObserving=false;terminalShellOpen=false;renderKaceWorkflow(flash);assert.equal(button.disabled,true);
terminalShellOpen=true;renderKaceWorkflow(flash);assert.equal(button.disabled,false);
pywebview.api.resume_kace_installation=async()=>({status:'failed',message:'runtime mismatch'});
await resumeKaceInstallation();
assert.equal(bootstrapActive,false);assert.equal(button.disabled,false);
assert.ok(nodes.get('kace-resume-status').textContent.includes('runtime mismatch'));
assert.equal(flash.state,'AWAITING_FLASH');
pywebview.api.resume_kace_installation=async()=>({status:'failed',code:'RESUME_RECONNECT_REQUIRED'});
await resumeKaceInstallation();
assert.ok(nodes.get('kace-resume-status').textContent.includes(workflowText('resumeReconnect',flash)));
updateBootstrapEvent({protocol:'kace-bootstrap/v1',workflow_id:'resume-fixture',sequence:1,
event:'workflow_cancelled',code:'RECOVERY_PENDING'},powerContext);
assert.equal(nodes.get('bootstrap-stage-label').textContent,'⚠ '+workflowText('resumeIncomplete',{}));
updateKaceWorkflowEvent({...event,workflow_id:'flash',sequence:2,state:'DISCARDED'},7);
assert.equal(button.style.display,'none');assert.equal(button.disabled,true);
updateKaceWorkflowEvent({...event,workflow_id:'live',sequence:1,state:'AWAITING_FLASH'},7,false);
assert.equal(button.style.display,'none','no resume while live CLI progresses');
bootstrapActive=true;
completeBootstrapTerminal('BOOTSTRAP_CANCELLED','cancelled');
assert.equal(button.style.display,'inline-flex');assert.equal(button.disabled,false);
updateKaceWorkflowEvent({...event,workflow_id:'untrusted',sequence:1,data:{}},7,true);
assert.equal(button.style.display,'none','event without durable authority cannot resume');
})()`,c)})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([node, '-', str(Path(__file__).resolve().parents[1]/'web/app.js'), language],
                            input=program, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout+result.stderr
