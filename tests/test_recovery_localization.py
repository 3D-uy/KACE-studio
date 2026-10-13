"""Exercise critical recovery/eject messages in production JavaScript."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("language", ["English", "Español", "Português"])
def test_recovery_and_eject_use_current_language_and_preserve_state(language):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend execution")
    app = Path(__file__).resolve().parents[1] / "web/app.js"
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(), alerts=[], messages=[], events=[], requests=[];
function element(){return {dataset:{},style:{},attributes:{},disabled:false,textContent:'',value:'preserve',
 setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},
 classList:{add(){},remove(){},contains(){return false;}},
 set innerHTML(value){this.html=value;this.spans=[...value.matchAll(/data-ui-text="([^"]+)">([^<]*)/g)].map(m=>({dataset:{uiText:m[1]},textContent:m[2]}));},
 get innerHTML(){return this.html||'';}};}
nodes.set('eject-btn',element());
const context={assert,console,process,nodes,alerts,messages,events,requests,language:process.argv[3],
 navigator:{language:'en'},addEventListener(){},localStorage:{getItem(){return null;},setItem(){}},
 alert:message=>alerts.push(message),document:{documentElement:{},activeElement:{id:'preserved-focus'},
 addEventListener(){},getElementById:id=>nodes.get(id)||null,
 querySelectorAll(selector){if(selector==='[data-ui-text]')return nodes.get('eject-btn').spans||[];
 if(selector==='[data-ui-label]')return [nodes.get('eject-btn')].filter(n=>n.dataset.uiLabel);return [];}}
};context.window=context;vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
vm.runInContext(`
(async()=>{
 setStudioLanguage(language,false);
 updateConnectionStatus=()=>{};applyRemotePowerConfig=()=>{};startFirmwareCheckpointWatch=()=>{};
 updateKaceWorkflowEvent=event=>events.push(event);refreshDrives=()=>{};
 term={write:message=>messages.push(message)};powerSelection=4;
 const result={status:'success',generation:7};
 for(const state of ['COMPLETE','READY_TO_DEPLOY',null]){
   messages.length=0;
   restoreSshAfterReconnect(result,4,state?{generation:7,event:{state},checkpoint:{state}}:null);
   const key=state==='COMPLETE'?'recoveryComplete':state?'recoveryPending':'recoveryUnavailable';
   assert.equal(typeof studioText(key),'string');
   assert.ok(messages.join('').includes(studioText('recoveryConnected')));
   assert.ok(messages.join('').includes(studioText(key)));
   if(language!=='Español')assert.ok(!messages.join('').includes('Conexión con la Pi recuperada'));
 }
 const before=messages.length;restoreSshAfterReconnect(result,3,null);assert.equal(messages.length,before);
 messages.length=0;restoreSshAfterReconnect(result,4,{generation:6,event:{state:'COMPLETE'},checkpoint:{state:'COMPLETE'}});
 assert.ok(messages.join('').includes(studioText('recoveryUnavailable')));
 lastFlashedDriveId=null;ejectFlashedDrive();assert.equal(alerts.pop(),studioText('ejectNoDrive'));
 lastFlashedDriveId=99;
 window.pywebview={api:{eject_drive:()=>new Promise((resolve,reject)=>requests.push({resolve,reject}))}};
 const button=nodes.get('eject-btn');
 ejectFlashedDrive();assert.equal(button.disabled,true);
 assert.ok(button.innerHTML.includes(studioText('ejecting')));
 const next=language==='English'?'Português':'English';
 sshConnected=true;activeTab='credentials-tab';
 setStudioLanguage(next,false);
 assert.equal(button.spans[0].textContent,studioText('ejecting'));
 assert.equal(document.activeElement.id,'preserved-focus');assert.equal(sshConnected,true);
 assert.equal(activeTab,'credentials-tab');assert.equal(button.value,'preserve');
 requests.pop().resolve({success:true,message:'untranslated backend message'});
 await Promise.resolve();await Promise.resolve();
 assert.ok(button.innerHTML.includes(studioText('ejectSafe')));
 assert.equal(button.title,studioText('ejectSafeHint'));
 assert.equal(button.disabled,true);
 setStudioLanguage(language,false);
 assert.equal(button.spans[0].textContent,studioText('ejectSafe'));
 assert.equal(button.title,studioText('ejectSafeHint'));
 for(const rejected of [false,true]){
   renderEjectState('idle');ejectFlashedDrive();const request=requests.pop();
   if(rejected)request.reject(new Error('backend-only detail'));else request.resolve({success:false,error:'backend-only detail'});
   await Promise.resolve();await Promise.resolve();await Promise.resolve();
   assert.equal(alerts.pop(),studioText('ejectFailed'));
   assert.equal(button.disabled,false);assert.ok(button.innerHTML.includes(studioText('eject')));
 }
 for(const success of [true,false]){
   ejectFlashedDrive();const old=requests.pop();
   // A new flash of the same disk invalidates the old eject response.
   ++ejectRequestGeneration;renderEjectState('idle');ejectFlashedDrive();
   const active=requests.pop();const beforeAlerts=alerts.length;
   old.resolve({success});await Promise.resolve();await Promise.resolve();
   assert.equal(alerts.length,beforeAlerts);assert.equal(button.disabled,true);
   assert.ok(button.innerHTML.includes(studioText('ejecting')));
   active.resolve({success:false});await Promise.resolve();await Promise.resolve();alerts.pop();
 }
 window.pywebview=null;ejectFlashedDrive();assert.equal(alerts.pop(),studioText('ejectDesktopOnly'));
 assert.equal(button.disabled,false);
 console.log('completed');
})().catch(error=>{console.error(error);process.exitCode=1;});
`,context);
'''
    result = subprocess.run([node, "-", str(app), language], input=script, text=True,
                            encoding="utf-8", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("completed")
