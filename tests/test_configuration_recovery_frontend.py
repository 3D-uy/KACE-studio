"""Reopening Studio discovers receipts only on request; recovery requires selection."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('language', ['English', 'Español', 'Português'])
@pytest.mark.parametrize('scenario', ['select', 'late', 'failure'])
def test_saved_configuration_selection_is_explicit(language, scenario):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required')
    program = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),calls=[];
function element(){return {dataset:{},style:{},children:[],textContent:'',value:'',disabled:false,hidden:false,
classList:{add(){},remove(){},toggle(){}},setAttribute(){},removeAttribute(){},replaceChildren(){this.children=[]},appendChild(x){this.children.push(x)},querySelector(){return this}};}
let finish;
const response=new Promise(resolve=>finish=resolve);
const c={console,nodes,calls,assert,finish,language:process.argv[3],scenario:process.argv[4],navigator:{language:'en'},
localStorage:{getItem(){return null},setItem(){}},addEventListener(){},setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},
document:{documentElement:{},addEventListener(){},querySelectorAll(){return []},createElement:element,getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)}}};
c.window=c;c.pywebview={api:{
async inspect_kace_configurations(...a){calls.push(['inspect',...a]);return response;},
async resume_kace_configuration(...a){calls.push(['resume',...a]);return {status:'started',operation_id:'c'.repeat(32)}},
async start_kace_configuration(){throw Error('must not generate a new operation')}}};
vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),c);
(async()=>{await vm.runInContext(`(async()=>{
selectedStudioLanguage=language;term={write(){}};
sshConnected=true;terminalShellOpen=true;firmwareGeneration=7;powerSelection=1;currentDeviceIp='pi.local';
powerContext={host:'pi.local',selection:1,session:7};
const context={...powerContext};
const original={schema:2,workflow_kind:'firmware_deployment',workflow_id:'original',sequence:10,state:'COMPLETE',data:{firmware_authority:'durable_checkpoint',language}};
updateKaceWorkflowEvent(original,7,true);
assert.equal(configurationOperation,null,'a restarted app has no local operation ID');
assert.equal(calls.length,0,'reading a checkpoint never starts discovery or recovery');
const discover=nodes.get('kace-discover-configurations');
assert.equal(discover.disabled,false);
const pending=discoverKaceConfigurations();
assert.equal(calls.length,1);assert.equal(calls[0][0],'inspect');
await discoverKaceConfigurations();assert.equal(calls.length,1,'duplicate discovery blocked');
if(scenario==='late') window.invalidatePowerSession(context);
finish(scenario==='failure'?{status:'failed',message:'receipt read failed'}:{status:'ok',generation:7,power_context:context,truncated:true,rejected:2,operations:[
{operation_id:'b'.repeat(32),state:'FAILED',sequence:3,recoverable:true,result_sha256:'b'.repeat(64),reason:''},
{operation_id:'c'.repeat(32),state:'PENDING_ACTIVATION',sequence:5,recoverable:true,result_sha256:'d'.repeat(64),reason:''},
{operation_id:'e'.repeat(32),state:'COMPLETE',sequence:9,recoverable:false,result_sha256:'f'.repeat(64),reason:'complete'},
{operation_id:'f'.repeat(32),state:'PREPARING',sequence:1,recoverable:false,result_sha256:'a'.repeat(64),reason:'manifest_unavailable'}]});
await pending;
assert.equal(calls.length,1,'discovery never resumes');
if(scenario!=='select'){
 assert.equal(configurationOperation,null);
 assert.equal(nodes.get('kace-configuration-recovery-choose').disabled,true);
 if(scenario==='late') assert.equal(nodes.get('kace-configuration-recovery').hidden,true);
 return;
}
const select=nodes.get('kace-configuration-recovery-select');
assert.equal(select.value,'','no operation selected automatically, regardless of order');
assert.equal(select.children.length,5);
assert.equal(select.children[3].disabled,true);assert.equal(select.children[4].disabled,true);
assert.ok(nodes.get('kace-configuration-recovery-status').textContent.includes(studioText('configurationTruncated')));
assert.ok(nodes.get('kace-configuration-recovery-status').textContent.includes('pi.local'));
chooseConfigurationRecovery();assert.equal(configurationOperation,null);
select.value='e'.repeat(32);updateConfigurationRecoverySelection();
assert.equal(nodes.get('kace-configuration-recovery-choose').disabled,true);
chooseConfigurationRecovery();assert.equal(configurationOperation,null,'completed receipt is read-only');
select.value='c'.repeat(32);updateConfigurationRecoverySelection();
assert.equal(nodes.get('kace-configuration-recovery-choose').disabled,false);
chooseConfigurationRecovery();
assert.equal(configurationOperation.operationId,'c'.repeat(32));
assert.equal(configurationOperation.resultSha256,'d'.repeat(64));
assert.equal(calls.length,1,'selection itself does not execute');
assert.equal(configurationOperation.view.state,'PENDING_ACTIVATION');
assert.equal(configurationOperation.source.workflowId,'original');
assert.ok(configurationOperation.view.detail.includes('c'.repeat(32)));
await startKaceConfiguration();
assert.equal(calls.length,2);assert.equal(calls[1][0],'resume');
assert.equal(calls[1][1],'c'.repeat(32));assert.equal(calls[1][2],'original');
assert.equal(calls[1][6],'d'.repeat(64),'selected receipt hash is bound to dispatch');
assert.equal(configurationOperation.resultSha256,null,'future receipts are read again after this launch');
})()`,c);console.log('completed')})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([node, '-', str(Path(__file__).resolve().parents[1]/'web/app.js'), language, scenario],
                            input=program, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'completed'
