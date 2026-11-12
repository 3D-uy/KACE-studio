"""A late checkpoint on reconnect enables explicit recovery even at the same sequence."""
from pathlib import Path
import shutil
import subprocess

def test_same_sequence_checkpoint_after_reconnect():
    node = shutil.which("node")
    assert node, "Node is required for the frontend session contract"
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const callbacks=[];
const c={console,navigator:{language:'en'},localStorage:{getItem(){return null}},setTimeout(f){callbacks.push(f)},
clearTimeout(){},setInterval(){},clearInterval(){},addEventListener(){},
document:{documentElement:{},addEventListener(){},querySelectorAll(){return []},getElementById(){return null}}};
c.window=c;
c.pywebview={api:{async get_firmware_workflow_checkpoint(){return {generation:7,event:{schema:2,workflow_kind:'firmware_deployment',workflow_id:'flow',sequence:10,state:'DEPLOYING',data:{firmware_authority:'durable_checkpoint'}}}}}};
vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),c);
(async()=>{vm.runInContext("sshConnected=true; firmwareGeneration=7; renderKaceWorkflow=()=>{}; kaceWorkflowViews.set('flow',{workflowId:'flow',sequence:10,state:'DEPLOYING',generation:6,kind:'firmware_deployment',data:{firmware_authority:'durable_checkpoint'}});startFirmwareCheckpointWatch(true)",c);
await callbacks.shift()();assert.equal(vm.runInContext("kaceWorkflowViews.get('flow').resumeAvailable",c),true);
assert.equal(vm.runInContext("kaceWorkflowViews.get('flow').generation",c),7);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result=subprocess.run([node,"-",str(Path(__file__).resolve().parents[1]/"web/app.js")],input=script,capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stderr
