"""Run production JS: new configuration cannot borrow an old COMPLETE."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('language', ['English', 'Español', 'Português'])
def test_configuration_keeps_its_identity_and_resumes_explicitly(language):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    program = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),calls=[];
function element(){return {dataset:{},style:{},children:[],textContent:'',value:'',disabled:false,
classList:{add(){},remove(){},toggle(){}},setAttribute(){},removeAttribute(){},replaceChildren(){this.children=[]},appendChild(x){this.children.push(x)},querySelector(){return this}};}
const c={console,nodes,calls,assert,language:process.argv[3],navigator:{language:'en'},
localStorage:{getItem(){return null},setItem(){}},addEventListener(){},setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},
document:{documentElement:{},addEventListener(){},querySelectorAll(){return []},createElement:element,getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)}}};
c.window=c;c.pywebview={api:{async start_kace_configuration(...a){calls.push(['new',...a]);return {status:'started',operation_id:'c'.repeat(32)}},async resume_kace_configuration(...a){calls.push(['resume',...a]);return {status:'started',operation_id:'c'.repeat(32)}}}};
vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),c);
(async()=>{await vm.runInContext(`(async()=>{
selectedStudioLanguage=language;term={write(){}};
sshConnected=true;terminalShellOpen=true;firmwareGeneration=7;powerSelection=1;currentDeviceIp='pi.local';
powerContext={host:'pi.local',selection:1,session:7};
const original={schema:2,workflow_kind:'firmware_deployment',workflow_id:'original',sequence:10,state:'COMPLETE',data:{firmware_authority:'durable_checkpoint',language}};
updateKaceWorkflowEvent(original,7,true);
const button=nodes.get('kace-new-configuration');
assert.equal(button.disabled,false);assert.equal(button.textContent,workflowText('configurationAction',{}));
await startKaceConfiguration();assert.equal(calls.length,1);assert.equal(calls[0][0],'new');
assert.equal(kaceWorkflowViews.get('original').state,'COMPLETE');
await startKaceConfiguration();assert.equal(calls.length,1,'double-click blocked');
const pending={schema:1,workflow_kind:'configuration',workflow_id:'configuration-'+ 'c'.repeat(32),sequence:2,state:'VERIFYING_CONFIG'};
assert.equal(updateKaceWorkflowEvent(pending,7),true);
assert.equal(updateKaceWorkflowEvent({...pending,workflow_id:'configuration-'+'d'.repeat(32),state:'COMPLETE'},7),false);
updateKaceWorkflowEvent({...original,sequence:11},7,true);
assert.equal(nodes.get('kace-workflow-tracker').dataset.workflowId,pending.workflow_id,'old COMPLETE cannot replace new operation');
bootstrapActive=false;firmwareGeneration=8;powerSelection=2;powerContext={host:'pi.local',selection:2,session:8};
kaceWorkflowViews.clear();assert.equal(restoreConfigurationProjection(),true);
assert.equal(kaceWorkflowViews.get(pending.workflow_id).state,'VERIFYING_CONFIG');
assert.equal(button.disabled,false);assert.equal(button.textContent,workflowText('configurationResume',{}));
await startKaceConfiguration();assert.equal(calls.length,2);assert.equal(calls[1][0],'resume');assert.equal(calls[1][1],'c'.repeat(32));
assert.equal(calls[1][2],'original');assert.equal(calls[1][4],8);
bootstrapActive=false;
pywebview.api.resume_kace_configuration=async()=>({status:'complete',operation_id:'c'.repeat(32),result:{sequence:9,detail:'verified own operation'}});
renderKaceWorkflow(configurationOperation.view);await startKaceConfiguration();
assert.equal(configurationOperation.view.state,'COMPLETE');assert.equal(configurationOperation.view.sequence,9);
assert.equal(nodes.get('kace-new-configuration').style.display,'none');
assert.equal(configurationOperation.source.sequence,10,'saved original authority is unchanged');
})()`,c)})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([node, '-', str(Path(__file__).resolve().parents[1]/'web/app.js'), language],
                            input=program, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
