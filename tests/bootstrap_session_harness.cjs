// Execute all of app.js. Only browser/bridge IO is simulated, not UI handlers.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map(), timers = [], requests = {login: [], checkpoint: [], sftp: [], downloads: [], configurationDownloads: []};
function node() {
    const classes = new Set();
    return {style: {}, dataset: {}, children: [], value: '', disabled: false, textContent: '',
        classList: {add(...names) {names.forEach(n => classes.add(n));},
            remove(...names) {names.forEach(n => classes.delete(n));},
            toggle(name, force) {if (force) classes.add(name); else classes.delete(name);},
            contains(name) {return classes.has(name);}},
        appendChild(child) {this.children.push(child);}, replaceChildren() {this.children = [];},
        querySelector() {return node();}, addEventListener() {}, removeAttribute() {},
        setAttribute() {}, focus() {}, click() {}};
}
function deferred(kind, args) {
    let resolve;
    const promise = new Promise(ok => {resolve = ok;});
    requests[kind].push({args, resolve});
    return promise;
}
const context = {console, assert, requests, timers, deferred, scenarioName: process.argv[3],
    robinEvents: process.argv[4] ? JSON.parse(fs.readFileSync(process.argv[4], 'utf8')) : null,
    document: {documentElement: {}, addEventListener() {}, querySelectorAll() {return [];}, createElement: node,
        getElementById(id) {if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id);}},
    navigator: {language: 'en'}, addEventListener() {},
    setTimeout(fn, delay) {timers.push({fn, delay}); return timers.length;},
    clearTimeout() {}, setInterval() {return 1;}, clearInterval() {},
    fetch: (...args) => deferred('sftp', args),
    pywebview: {token: 'fixture', api: {
        select_power_target: () => Promise.resolve(true),
        connect_ssh: (...args) => deferred('login', args),
        disconnect_ssh: () => Promise.resolve(true), resize_ssh_pty() {},
        get_firmware_deployment_manifest: () => Promise.resolve({}),
        get_firmware_workflow_checkpoint: (...args) => deferred('checkpoint', args),
        get_remote_power_config: () => Promise.resolve({status: 'disabled'}),
        download_file: (...args) => {requests.downloads.push(args); return Promise.resolve(true);},
        download_kace_configuration: (...args) => deferred('configurationDownloads', args),
    }},
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const setup = `
const tick = async () => {for (let i = 0; i < 25; i++) await Promise.resolve();};
term = {cols: 80, rows: 24, write() {}, clear() {}};
currentDeviceIp = 'pi.local'; currentDeviceName = 'Pi'; activeTab = 'terminal-tab';
function event(sequence, state = 'ARTIFACT_READY') {
    return {schema: 2, workflow_kind: 'firmware_deployment', workflow_id: 'flow', sequence, state,
        data: {staged_path: '/home/kace/kace/firmware.bin', language: 'English'}};
}
function result(generation) {
    return {status: 'success', generation, power_config: {status: 'disabled', config: {enabled: false},
        power_context: {host: 'pi.local', selection: powerSelection, session: generation}}};
}
async function login(generation) {
    performSshLogin('fixture', 'unused'); await tick();
    requests.login.at(-1).resolve(result(generation)); await tick();
    assert.equal(firmwareGeneration, generation);
}
function begin() {
    window.updateDeviceState('BOOTSTRAPPING', 0, 'Starting bootstrap');
    window.updateBootstrapEvent({protocol: 'kace-bootstrap/v1', workflow_id: 'boot', sequence: 1, event: 'workflow_started'}, powerContext);
}
`;
const cases = {
    host_key_confirmation: `
        const output = [];
        window.Terminal = class {
            constructor() { this.cols=80; this.rows=24; }
            loadAddon() {} open() {} onResize() {} clear() {}
            onData(fn) { this.input=fn; }
            write(text) { output.push(text); }
        };
        window.FitAddon = {FitAddon: class {fit() {}}};
        initTerminal();
        pywebview.api.respond_ssh_host_key = (token, accept) => {
            assert.equal(token, 'challenge'); assert.equal(accept, true); return Promise.resolve(true);
        };
        for (const language of ['English', 'Español', 'Português']) {
            selectedStudioLanguage=language;
            performSshLogin('pi', 'first-password'); await tick();
            const selected = powerSelection;
            requests.login.at(-1).resolve({status:'host_key_confirmation_required', token:'challenge',
                host:'pi.local', algorithm:'ssh-ed25519', fingerprint:'SHA256:example'}); await tick();
            assert.equal(loginState, 'PROMPTING_HOST_KEY');
            assert.ok(output.join('').includes(studioText('hostKeyVerify')));
            assert.ok(output.join('').includes('SHA256:example'));
            term.input('y'); await tick();
            assert.equal(loginState, 'PROMPTING_USER');
            assert.equal(approvedHostSelection, selected);
            performSshLogin('pi', 'second-password'); await tick();
            assert.equal(powerSelection, selected);
            assert.equal(requests.login.at(-1).args[2], 'second-password');
            requests.login.at(-1).resolve({status:'failed'}); await tick();
        }
        performSshLogin('pi', 'password'); await tick();
        requests.login.at(-1).resolve({status:'host_key_confirmation_required', token:'challenge',
            host:'pi.local', algorithm:'ssh-ed25519', fingerprint:'SHA256:example'}); await tick();
        pywebview.api.respond_ssh_host_key = (token, accept) => {
            assert.equal(accept, false); return Promise.resolve(false);
        };
        term.input('n'); await tick();
        assert.equal(loginState, 'PROMPTING_USER');
        assert.equal(approvedHostSelection, null);
        performSshLogin('pi', 'password'); await tick();
        requests.login.at(-1).resolve({status:'host_key_confirmation_required', token:'challenge',
            host:'pi.local', algorithm:'ssh-ed25519', fingerprint:'SHA256:example'}); await tick();
        let resolveApproval;
        pywebview.api.respond_ssh_host_key = () => new Promise(resolve => {resolveApproval=resolve;});
        term.input('y');
        resetPowerTarget('other.local'); await tick();
        resolveApproval(true); await tick();
        assert.notEqual(approvedHostSelection, powerSelection);
    `,
    manifest_never_completes_installation: `
        await login(7);
        requests.checkpoint.at(-1).resolve(null); await tick();
        for (const language of ['English', 'Español', 'Português']) {
            selectedStudioLanguage=language;
            for (const [index,state] of ['COMPLETE','DONE',undefined,'READY_TO_DEPLOY'].entries()) {
                const id=language+'-'+index;
                const manifest={schema:1,workflow_kind:'firmware_deployment',workflow_id:id,sequence:1,
                    generation:7,state,deployment:{deployment_id:id,final_filename:'firmware.bin',
                        staged_path:'/home/kace/kace/deploy/'+id+'/firmware.bin'}};
                assert.equal(restoreKaceDeploymentManifest(manifest),false);
                assert.equal(kaceWorkflowViews.has(id),false);
                assert.equal(window.updateKaceWorkflowEvent({...event(1),state,workflow_id:id},7,false,'manifest'),false);
                assert.equal(document.getElementById('kace-workflow-tracker').classList.contains('success'),false);
            }
        }
        requests.checkpoint=[];
        selectedStudioLanguage='Español';
        const complete={...event(2,'COMPLETE'),data:{language:'Español'}};
        assert.equal(window.updateKaceWorkflowEvent(complete,7),true);
        assert.equal(document.getElementById('kace-workflow-status').textContent,'Completada');
        assert.equal(restoreKaceDeploymentManifest({schema:1,workflow_kind:'firmware_deployment',workflow_id:'bad',
            sequence:999,generation:7,state:'DONE',deployment:{}}),false);
        assert.equal(document.getElementById('kace-workflow-status').textContent,'Completada');
    `,
    manifest_positive_states_and_authority: `
        await login(7);
        for (const language of ['English','Español','Português']) {
            selectedStudioLanguage=language;
            for (const [index,state] of ['ARTIFACT_READY','MEDIA_PREPARED','ACTION_REQUIRED','FLASHED','CANCELLED','FAILED_FLASH'].entries()) {
                const id=language+'-'+state;
                assert.equal(restoreKaceDeploymentManifest({schema:1,workflow_kind:'firmware_deployment',
                    workflow_id:id,sequence:index+1,state,generation:7,deployment:{deployment_id:id,
                        final_filename:'Robin_nano.bin',staged_path:'/home/kace/kace/deploy/'+id+'/Robin_nano.bin',
                        instructions:[{id:'copy',text:'Copy the final file'}]}}),true);
                const view=kaceWorkflowViews.get(id);
                assert.equal(view.state,state);
                assert.equal(document.getElementById('kace-workflow-tracker').classList.contains('success'),false);
                assert.notEqual(document.getElementById('kace-workflow-detail').textContent,workflowText('completeDetail',view));
                const downloadable=['ARTIFACT_READY','MEDIA_PREPARED','ACTION_REQUIRED'].includes(state);
                assert.equal(document.getElementById('kace-firmware-download').style.display,downloadable?'inline-flex':'none');
                assert.equal(document.getElementById('kace-configuration-download').style.display,'none');
                if (downloadable) {
                    assert.equal(window.downloadKaceFirmwareArtifact(),true);
                    assert.deepEqual(Array.from(requests.downloads.at(-1)),[view.data.staged_path,7]);
                    await tick();
                }
            }
        }
        const checkpoint={...event(1,'COMPLETE'),workflow_id:'verified',data:{language:'English'}};
        window.updateKaceWorkflowEvent(checkpoint,7);
        assert.equal(document.getElementById('kace-workflow-tracker').classList.contains('success'),true);
        assert.equal(restoreKaceDeploymentManifest({schema:1,workflow_kind:'firmware_deployment',workflow_id:'late',
            sequence:999,generation:7,state:'FLASHED',deployment:{deployment_id:'late',
                final_filename:'firmware.bin',staged_path:'/home/kace/kace/firmware.bin'}}),false);
        // The supported installation event stream keeps its original DONE semantics.
        window.updateKaceWorkflowEvent({schema:1,workflow_id:'legacy',sequence:1,state:'DONE',data:{}},7);
        assert.equal(document.getElementById('kace-workflow-tracker').classList.contains('success'),true);
    `,
    manifest_discard_and_reconnect: `
        await login(7);
        const manifest={schema:1,workflow_kind:'firmware_deployment',workflow_id:'flow',sequence:99,
            generation:7,state:'ARTIFACT_READY',deployment:{deployment_id:'flow',
                final_filename:'firmware.bin',staged_path:'/home/kace/kace/firmware.bin'}};
        assert.equal(restoreKaceDeploymentManifest(manifest),true);
        requests.checkpoint.at(-1).resolve({generation:7,event:event(2,'DEPLOYING'),checkpoint:{state:'DEPLOYING'}});
        await tick();
        assert.equal(kaceWorkflowViews.get('flow').state,'DEPLOYING');
        assert.equal(window.updateKaceWorkflowEvent(event(3,'DISCARDED'),7),true);
        assert.equal(restoreKaceDeploymentManifest({...manifest,sequence:100}),false);
        assert.equal(document.getElementById('kace-workflow-tracker').style.display,'none');
        window.updateSshDisconnected('lost',false);
        assert.equal(restoreKaceDeploymentManifest({...manifest,workflow_id:'old'}),false);
        await login(8);
        assert.equal(restoreKaceDeploymentManifest({...manifest,workflow_id:'old'}),false);
        const fresh={...manifest,workflow_id:'new',generation:8,deployment:{...manifest.deployment,deployment_id:'new'}};
        assert.equal(restoreKaceDeploymentManifest(fresh),true);
        assert.equal(window.downloadKaceFirmwareArtifact(),true);
        assert.equal(requests.downloads.at(-1)[1],8);
    `,
    manifest_checkpoint_api_evidence: `
        requests.manifest=[];
        pywebview.api.get_firmware_deployment_manifest=()=>deferred('manifest',[]);
        await login(7);
        // Payloads came through the real Python checkpoint/manifest APIs.
        requests.checkpoint.at(-1).resolve(robinEvents.invalidCheckpoint); await tick();
        requests.manifest.at(-1).resolve(robinEvents.invalidManifest); await tick();
        assert.equal(kaceWorkflowViews.size,0);
        assert.equal(document.getElementById('kace-workflow-tracker').classList.contains('success'),false);
        for (const language of ['English','Español','Português']) {
            setStudioLanguage(language,false);
            const authoritative=robinEvents.complete;
            window.restoreSshAfterReconnect(result(7),powerSelection,authoritative);
            const view=kaceWorkflowViews.get(authoritative.event.workflow_id);
            assert.equal(document.getElementById('kace-workflow-status').textContent,workflowText('completed',view));
            assert.equal(document.getElementById('kace-workflow-tracker').classList.contains('success'),true);
            assert.equal(restoreKaceDeploymentManifest(robinEvents.invalidManifest),false);
            assert.equal(document.getElementById('kace-workflow-tracker').classList.contains('success'),true);
        }
    `,
    configuration_download_lifecycle: `
        await login(7);
        const button=document.getElementById('kace-configuration-download');
        for (const [index,language] of ['English','Español','Português'].entries()) {
            selectedStudioLanguage=language;
            const sequence=index+1;
            window.updateKaceWorkflowEvent({...event(sequence,'CONFIG_GENERATED'),data:{configuration_download_available:true,language}},7);
            assert.equal(button.style.display,'inline-flex');
            assert.equal(button.textContent,studioText('configurationDownload'));
            assert.equal(window.downloadKaceConfiguration(),true);
            assert.deepEqual(Array.from(requests.configurationDownloads.at(-1).args),['flow',sequence,7]);
            assert.equal(button.disabled,true);
            assert.equal(window.downloadKaceConfiguration(),false);
            requests.configurationDownloads.at(-1).resolve({status:'success'});await tick();
            assert.equal(button.disabled,false);
            assert.equal(document.getElementById('kace-configuration-download-status').textContent,studioText('configurationDownloadSuccess'));
        }
        window.updateKaceWorkflowEvent({...event(4,'DISCARDED'),data:{}},7);
        assert.equal(button.style.display,'none');
        assert.equal(window.downloadKaceConfiguration(),false);
        assert.equal(window.updateKaceWorkflowEvent({...event(5,'READY_TO_DEPLOY'),data:{configuration_download_available:true}},7),false);
        window.updateKaceWorkflowEvent({...event(1,'ARTIFACT_READY'),workflow_id:'new-flow'},7);
        assert.equal(button.style.display,'none');
        window.updateKaceWorkflowEvent({...event(2,'READY_TO_DEPLOY'),workflow_id:'new-flow',data:{configuration_download_available:true}},7);
        assert.equal(button.style.display,'inline-flex');
    `,
    configuration_download_disconnect: `
        await login(7);
        window.updateKaceWorkflowEvent({...event(1,'READY_TO_DEPLOY'),data:{configuration_download_available:true}},7);
        const button=document.getElementById('kace-configuration-download');
        window.downloadKaceConfiguration();
        const old=requests.configurationDownloads.at(-1);
        window.updateSshDisconnected('lost',false);
        assert.equal(button.disabled,true);
        assert.equal(window.downloadKaceConfiguration(),false);
        await login(8);
        old.resolve({status:'success'});await tick();
        assert.equal(document.getElementById('kace-configuration-download-status').hidden,true);
        window.updateKaceWorkflowEvent({...event(2,'READY_TO_DEPLOY'),data:{configuration_download_available:true}},8);
        assert.equal(button.disabled,false);
        assert.equal(window.downloadKaceConfiguration(),true);
        assert.deepEqual(Array.from(requests.configurationDownloads.at(-1).args),['flow',2,8]);
    `,
    configuration_download_cancel_failure: `
        await login(7);
        window.updateKaceWorkflowEvent({...event(1,'READY_TO_DEPLOY'),data:{configuration_download_available:true}},7);
        const button=document.getElementById('kace-configuration-download');
        window.downloadKaceConfiguration();
        requests.configurationDownloads.at(-1).resolve({status:'cancelled'});await tick();
        assert.equal(button.disabled,false);
        assert.equal(document.getElementById('kace-configuration-download-status').hidden,true);
        window.downloadKaceConfiguration();
        requests.configurationDownloads.at(-1).resolve({status:'error',code:'configurationDownloadChanged'});await tick();
        assert.equal(button.disabled,false);
        assert.equal(document.getElementById('kace-configuration-download-status').textContent,studioText('configurationDownloadChanged'));
        window.downloadKaceConfiguration();
        requests.configurationDownloads.at(-1).resolve({status:'error',code:'untrusted'});await tick();
        assert.equal(document.getElementById('kace-configuration-download-status').textContent,studioText('configurationDownloadFailed'));
    `,
    reconnected_terminal_never_calls_pending_installation_complete: `
        const output=[];term.write=text=>output.push(text);
        for (const [index,language] of ['English','Español','Português'].entries()) {
            selectedStudioLanguage=language;await login(7+index);
            const pending={generation:7+index,event:{...event(1,'DEPLOYING'),data:{language}},checkpoint:{state:'DEPLOYING'}};
            output.length=0;
            window.restoreSshAfterReconnect(result(7+index),powerSelection+1,pending);
            assert.equal(output.length,0);
            window.restoreSshAfterReconnect(result(7+index),powerSelection,pending);
            assert.ok(output.join('').includes(studioText('recoveryPending')));
            assert.ok(!output.join('').includes(studioText('recoveryComplete')));
            output.length=0;
            const complete={...pending,event:{...event(2,'COMPLETE'),data:{language}},checkpoint:{state:'COMPLETE'}};
            window.restoreSshAfterReconnect(result(7+index),powerSelection,complete);
            assert.ok(output.join('').includes(studioText('recoveryComplete')));
        }
    `,
    recovered_installation_waits_for_authoritative_verification: `
        const output=[];term.write=text=>output.push(text);
        for (const [index,language] of ['English','Español','Português'].entries()) {
            selectedStudioLanguage=language;await login(20+index);
            const generation=20+index;
            const pending={generation,event:{...event(1,'DEPLOYING'),workflow_id:'waiting-'+index,data:{language}},checkpoint:{state:'DEPLOYING'}};
            output.length=0;
            window.restoreSshAfterReconnect(result(generation),powerSelection,pending,true);
            let view=kaceWorkflowViews.get(pending.event.workflow_id);
            assert.equal(view.recoveryObserving,true);
            assert.equal(view.resumePending,false);
            assert.equal(document.getElementById('kace-workflow-status').textContent,workflowText('observing',view));
            assert.ok(output.join('').includes(studioText('recoveryWaiting')));
            assert.ok(!output.join('').includes(studioText('recoveryPending')));
            assert.equal(window.finishSshRecovery(generation-1,powerSelection,pending),false);
            assert.equal(window.finishSshRecovery(generation,powerSelection+1,pending),false);
            assert.equal(window.finishSshRecovery(generation,powerSelection,pending),true);
            assert.equal(view.recoveryObserving,false);
            assert.equal(view.resumePending,true);
            assert.ok(output.join('').includes(studioText('recoveryPending')));
            const complete={...pending,event:{...pending.event,sequence:2,state:'COMPLETE'},checkpoint:{state:'COMPLETE'}};
            assert.equal(window.finishSshRecovery(generation,powerSelection,complete),true);
            assert.equal(kaceWorkflowViews.get(pending.event.workflow_id).state,'COMPLETE');
            // An older timeout response cannot downgrade verified completion.
            assert.equal(window.finishSshRecovery(generation,powerSelection,pending),true);
            assert.equal(kaceWorkflowViews.get(pending.event.workflow_id).state,'COMPLETE');
            assert.ok(output.at(-1).includes(studioText('recoveryComplete')));
            window.restoreSshAfterReconnect(result(generation-1),powerSelection,pending,true);
            assert.equal(firmwareGeneration,generation,'older reconnect replies cannot replace the current session');
            window.updateKaceWorkflowEvent({...pending.event,workflow_id:'new-'+index,sequence:1,state:'HARDWARE_SELECTED'},generation);
            assert.equal(window.finishSshRecovery(generation,powerSelection,pending),false);
            assert.equal(document.getElementById('kace-workflow-tracker').dataset.workflowId,'new-'+index);
        }
    `,
    recovery_late_checkpoint_rebinds_preserved_workflow: `
        const output=[];term.write=text=>output.push(text);
        await login(7);
        window.updateKaceWorkflowEvent(event(4,'DEPLOYING'),7);
        window.updateSshDisconnected('transport lost',false);
        const before=kaceWorkflowViews.get('flow');
        assert.equal(before.connectionLost,true);
        window.restoreSshAfterReconnect(result(8),powerSelection,null);
        assert.equal(sshConnected,true);
        assert.equal(kaceWorkflowViews.get('flow'),before,'unavailable initial checkpoint preserves known history');
        assert.ok(output.join('').includes(studioText('recoveryUnavailable')));
        const poll=timers.filter(timer=>timer.delay===3000).at(-1).fn();await tick();
        requests.checkpoint.at(-1).resolve({generation:8,event:event(4,'DEPLOYING')});await poll;
        assert.equal(before.generation,8);
        assert.equal(before.connectionLost,false);
        assert.equal(before.resumePending,true);
        assert.equal(document.getElementById('kace-workflow-status').textContent,workflowText('recovery',before));
        // A watcher may finish while the backend observer still owns an older snapshot.
        window.updateKaceWorkflowEvent(event(5,'COMPLETE'),8);
        window.finishSshRecovery(8,powerSelection,{generation:8,event:event(4,'DEPLOYING')});
        assert.equal(kaceWorkflowViews.get('flow').state,'COMPLETE');
        assert.ok(output.at(-1).includes(studioText('recoveryComplete')));
        window.updateSshDisconnected('lost again',false);
        assert.equal(window.finishSshRecovery(8,powerSelection,{generation:8,event:event(4,'DEPLOYING')}),false);
    `,
    historical_complete_cannot_finish_newer_active_workflow_on_reconnect: `
        const output=[];term.write=text=>output.push(text);
        await login(7);
        const historical={...event(2,'COMPLETE'),workflow_id:'historical'};
        window.updateKaceWorkflowEvent(historical,7);
        window.updateKaceWorkflowEvent(event(4,'DEPLOYING'),7);
        window.updateSshDisconnected('transport lost',false);
        output.length=0;
        window.restoreSshAfterReconnect(result(8),powerSelection,{generation:8,event:historical,checkpoint:{state:'COMPLETE'}});
        assert.equal(sshConnected,true);
        assert.ok(output.join('').includes(studioText('recoveryUnavailable')));
        assert.ok(!output.join('').includes(studioText('recoveryComplete')));
        assert.equal(document.getElementById('kace-workflow-tracker').dataset.workflowId,'flow');
        assert.equal(kaceWorkflowViews.get('flow').state,'DEPLOYING');
        assert.equal(kaceWorkflowViews.get('historical').state,'COMPLETE','historical evidence is retained');
        const poll=timers.filter(timer=>timer.delay===3000).at(-1).fn();await tick();
        requests.checkpoint.at(-1).resolve({generation:8,event:event(4,'DEPLOYING')});await poll;
        assert.equal(kaceWorkflowViews.get('flow').generation,8);
        assert.equal(kaceWorkflowViews.get('flow').resumePending,true);
    `,
    stale_bootstrap_callbacks_are_rejected: `
        await login(7); begin();
        const old = {...powerContext};
        window.updateSshDisconnected('lost', false);
        await login(8);
        const state = currentDeviceState;
        const success = {protocol:'kace-bootstrap/v1', workflow_id:'boot', sequence:2, event:'workflow_succeeded'};
        for (const context of [old, {...powerContext, host:'other.local'}, {...powerContext, selection:0}, null]) {
            assert.equal(window.updateBootstrapEvent(success, context), false);
            assert.equal(window.writeTerminalData('Bootstrap complete! KACE wizard finished successfully.', context), false);
            assert.equal(window.updateBootstrapInterrupted('boot','old close',context),false);
            assert.equal(window.updateBootstrapDisconnected('boot','old close',false,context),false);
            assert.equal(currentDeviceState, state);
        }
        assert.equal(window.updateBootstrapEvent(success, powerContext), true);
        assert.equal(currentDeviceState, 'BOOTSTRAPPED');
    `,
    terminal_output_waits_for_login_identity: `
        const output=[];term.write=text=>output.push(text);
        loginState='CONNECTING';performSshLogin('fixture','unused');await tick();
        const context={host:'pi.local',selection:powerSelection,session:7};
        assert.equal(window.writeTerminalData('initial prompt',context),true);
        assert.equal(output.includes('initial prompt'),false);
        requests.login.at(-1).resolve(result(7));await tick();
        assert.equal(output.includes('initial prompt'),true);
        loginState='CONNECTING';performSshLogin('fixture','unused');await tick();
        const rejected={host:'pi.local',selection:powerSelection,session:8};
        window.writeTerminalData('rejected prompt',rejected);
        requests.login.at(-1).resolve({status:'failed'});await tick();
        await login(9);
        assert.equal(output.includes('rejected prompt'),false);
        assert.equal(pendingTerminalData.length,0);
    `,
    reconnect_preserves_early_terminal_output_and_close: `
        const output=[];term.write=text=>output.push(text);
        await login(7);
        window.updateSshDisconnected('lost',false);
        window.updateDeviceState('SSH_READY',100,'connected');
        const context={host:'pi.local',selection:powerSelection,session:8};
        assert.equal(window.writeTerminalData('new shell prompt',context),true);
        assert.equal(output.includes('new shell prompt'),false);
        window.updateTerminalClosed(8);
        window.restoreSshAfterReconnect(result(8),powerSelection,null);
        assert.equal(output.includes('new shell prompt'),true);
        assert.equal(sshConnected,true);
        assert.equal(terminalShellOpen,false);
    `,
    manual_login_preserves_early_terminal_close: `
        loginState='CONNECTING';performSshLogin('fixture','unused');await tick();
        window.updateDeviceState('SSH_READY',100,'connected');
        window.updateTerminalClosed(7);
        requests.login.at(-1).resolve(result(7));await tick();
        assert.equal(sshConnected,true);
        assert.equal(terminalShellOpen,false);
    `,
    bootstrap_start_reply_is_bound_to_origin: `
        requests.bootstrap=[];
        pywebview.api.start_bootstrap=(...args)=>deferred('bootstrap',args);
        await login(7);const original={...powerContext};
        startBootstrap();
        assert.equal(JSON.stringify(requests.bootstrap[0].args),JSON.stringify(['mainsail',original]));
        window.updateSshDisconnected('lost',false);await login(8);
        const state=currentDeviceState;
        requests.bootstrap[0].resolve({status:'failed',message:'old failure'});await tick();
        assert.equal(currentDeviceState,state);
        startBootstrap();
        requests.bootstrap[1].resolve({status:'failed',message:'current failure'});await tick();
        assert.equal(currentDeviceState,'BOOTSTRAP_FAILED');
    `,
    terminal_fragments_do_not_cross_sessions: `
        await login(7);
        window.writeTerminalData('Bootstrap complete! KACE wizard ', powerContext);
        const old = {...powerContext};
        window.updateSshDisconnected('lost', false);
        await login(8);
        const state = currentDeviceState;
        window.writeTerminalData('finished successfully.', powerContext);
        assert.equal(currentDeviceState, state);
        assert.equal(window.writeTerminalData('Bootstrap complete! KACE wizard finished successfully.', old), false);
        window.writeTerminalData('Bootstrap complete! KACE wizard finished successfully.', powerContext);
        assert.equal(currentDeviceState, 'BOOTSTRAPPED');
    `,
    checkpoint_precedes_late_manifest: `
        requests.manifest=[];
        pywebview.api.get_firmware_deployment_manifest=()=>deferred('manifest',[]);
        await login(7);
        const complete={generation:7,event:event(10,'COMPLETE'),checkpoint:{state:'COMPLETE'}};
        requests.checkpoint.at(-1).resolve(complete); await tick();
        requests.manifest.at(-1).resolve({workflow_kind:'firmware_deployment',workflow_id:'old-delivery',sequence:99,
            state:'ACTION_REQUIRED',generation:7,deployment:{deployment_id:'old-delivery',staged_path:'/old/firmware.bin'}});
        await tick();
        assert.equal(document.getElementById('kace-workflow-tracker').dataset.workflowId,'flow');
        assert.equal(document.getElementById('kace-workflow-status').textContent,'Completed');
        assert.equal(window.updateKaceWorkflowEvent(complete.event,7),false);
        assert.equal(document.getElementById('kace-workflow-status').textContent,'Completed');
    `,
    checkpoint_replaces_manifest_with_higher_sequence: `
        await login(7);
        assert.equal(restoreKaceDeploymentManifest({workflow_kind:'firmware_deployment',workflow_id:'flow',sequence:99,
            state:'ACTION_REQUIRED',generation:7,deployment:{staged_path:'/old/firmware.bin',instructions:['old']}}),true);
        requests.checkpoint.at(-1).resolve({generation:7,event:{...event(2,'DEPLOYING'),data:{language:'English'}},checkpoint:{state:'DEPLOYING'}});
        await tick();
        const view=kaceWorkflowViews.get('flow');
        assert.equal(view.state,'DEPLOYING');
        assert.equal(view.data.staged_path,undefined);
        assert.equal(view.data.instructions,undefined);
        assert.equal(view.resumePending,true);
        assert.equal(restoreKaceDeploymentManifest({workflow_kind:'firmware_deployment',workflow_id:'old',generation:7,
            deployment:{staged_path:'/old/firmware.bin'}}),false);
    `,
    manual_login_restores_pending_deployment: `
        for (const [index,language] of ['English','Español','Português'].entries()) {
            await login(7+index);
            const saved={...event(10,'DEPLOYING'),data:{language}};
            // A polling reply can precede the initial restoration reply.
            window.updateKaceWorkflowEvent(saved,7+index);
            requests.checkpoint.at(-1).resolve({generation:7+index,event:saved,checkpoint:{state:'DEPLOYING'}});
            await tick();
            const view=kaceWorkflowViews.get('flow');
            assert.equal(view.resumePending,true);
            assert.equal(document.getElementById('kace-workflow-status').textContent,workflowText('recovery',view));
            assert.equal(document.getElementById('kace-workflow-detail').textContent,workflowText('resumeDetail',view));
            assert.equal(window.updateKaceWorkflowEvent(event(11,'VERIFYING_CONFIG'),7+index),true);
            assert.equal(kaceWorkflowViews.get('flow').resumePending,false);
        }
    `,
    recoverable_disconnect_invalidates_session: `
        await login(7); begin();
        document.getElementById('bootstrap-progress-tracker').style.display = 'block';
        window.updateKaceWorkflowEvent(event(2, 'VERIFYING_CONFIG'), 7);
        const oldCheckpoint = requests.checkpoint.at(-1), oldSftp = requests.sftp.at(-1);
        const selection = powerSelection;
        if (scenarioName === 'post_bootstrap_disconnect_invalidates_session') {
            bootstrapActive = false;
            window.updateDeviceState('SSH_DISCONNECTED', 0, 'SSH lost; host status unknown');
        } else {
            window.updateBootstrapDisconnected('boot', 'SSH lost; host status unknown', true, powerContext);
        }
        assert.equal(sshConnected, false);
        assert.equal(document.getElementById('connection-title').textContent, studioText('sshDisconnected'));
        assert.equal(document.getElementById('bootstrap-btn').disabled, true);
        assert.equal(document.getElementById('printer-power-btn').disabled, true);
        assert.equal(document.getElementById('authorize-moonraker-btn').disabled, true);
        assert.equal(document.getElementById('kace-firmware-download').disabled, true);
        assert.equal(document.getElementById('sftp-browser-panel').style.display, 'none');
        assert.equal(document.getElementById('bootstrap-progress-tracker').style.display, 'block');
        assert.equal(kaceWorkflowViews.get('flow').state, 'VERIFYING_CONFIG');
        assert.equal(firmwareGeneration, null);
        assert.equal(sftpGeneration, null);
        assert.equal(powerContext, null);
        assert.equal(powerSelection, selection);
        assert.equal(window.downloadKaceFirmwareArtifact(), false);
        oldCheckpoint.resolve({generation: 7, event: event(99, 'COMPLETE')});
        oldSftp.resolve({ok:true,json:async()=>({path:'/old',items:[],generation:7})});
        await tick();
        assert.equal(kaceWorkflowViews.get('flow').state, 'VERIFYING_CONFIG');
        assert.equal(sftpGeneration, null);
        assert.equal(window.updateKaceWorkflowEvent(event(100, 'COMPLETE'), 7), false);
        // A failed automatic retry must retain the pending workflow too.
        window.updateDeviceState('CONNECTING', 0, 'Retrying');
        window.updateDeviceState('SSH_DISCONNECTED', 0, 'Host is still rebooting');
        assert.equal(sshConnected, false);
        assert.equal(kaceWorkflowViews.get('flow').state, 'VERIFYING_CONFIG');
        window.restoreSshAfterReconnect(result(8), selection, {
            generation: 8, event: event(3, 'DEPLOYING'), checkpoint: {state:'DEPLOYING'}
        });
        assert.equal(sshConnected, true);
        assert.equal(firmwareGeneration, 8);
        assert.equal(kaceWorkflowViews.get('flow').state, 'DEPLOYING');
        assert.equal(window.updateKaceWorkflowEvent(event(101, 'COMPLETE'), 7), false);
    `,
    sftp_persists_between_tabs: `
        await login(7);
        requests.sftp.at(-1).resolve({ok:true,json:async()=>({path:'/home/kace/configs',generation:7,items:[]})}); await tick();
        sftpSelectedFile='printer.cfg';
        const count=requests.sftp.length;
        for (const tab of ['imager-tab','credentials-tab','discovery-tab','terminal-tab']) {
            activeTab=tab; refreshSftpBrowser();
            assert.equal(document.getElementById('sftp-browser-panel').style.display,'flex');
            assert.equal(document.getElementById('sftp-browser-panel').classList.contains('is-visible'),true);
            assert.equal(sftpCurrentPath,'/home/kace/configs');
            assert.equal(sftpSelectedFile,'printer.cfg');
            assert.equal(sftpGeneration,7);
            assert.equal(requests.sftp.length,count);
        }
        updateConnectionStatus(false);
        assert.equal(document.getElementById('sftp-browser-panel').style.display,'none');
        assert.equal(document.getElementById('sftp-browser-panel').classList.contains('is-visible'),false);
        assert.equal(sftpGeneration,null);
        assert.equal(sftpSelectedFile,null);
    `,
    first_boot_stop_lifecycle: `
        let clock=0, scanTick, scans=0;
        Date.now=()=>clock;
        window.setInterval=fn=>{scanTick=fn;return 1;};
        triggerScan=()=>{scans++;};
        startFirstBootDiscovery();
        assert.equal(document.getElementById('stop-first-boot-scan').hidden,false);
        stopFirstBootDiscovery();
        assert.equal(document.getElementById('stop-first-boot-scan').hidden,true);
        startFirstBootDiscovery(); clock=600000; scanTick();
        assert.equal(document.getElementById('stop-first-boot-scan').hidden,true);
        assert.equal(firstBootDiscovery,null);
        assert.equal(scans,2); // Each start scans immediately; the timeout initiates no late scan.
    `,
    sftp_delayed_download_feedback: `
        await login(7);
        requests.sftp.at(-1).resolve({ok:true,json:async()=>({path:'/old',generation:7,items:[]})}); await tick();
        requests.sftpDownload=[];
        pywebview.api.download_file=(...args)=>deferred('sftpDownload',args);
        sftpSelectedFile='old.cfg';
        downloadSelectedSftpFile(); await tick();
        assert.deepEqual(requests.sftpDownload[0].args, ['/old/old.cfg',7]);
        assert.equal(document.getElementById('sftp-download-btn').disabled,true);
        loadSftpDirectory('/new');
        const pendingStatus=document.getElementById('sftp-status').textContent;
        requests.sftpDownload[0].resolve(true); await tick();
        assert.equal(document.getElementById('sftp-status').textContent,pendingStatus);
        assert.equal(document.getElementById('sftp-download-btn').disabled,true);
        requests.sftp.at(-1).resolve({ok:true,json:async()=>({path:'/new',generation:8,items:[]})}); await tick();
        sftpSelectedFile='new.cfg'; downloadSelectedSftpFile(); await tick();
        assert.deepEqual(requests.sftpDownload[1].args,['/new/new.cfg',8]);
        sshConnected=false; refreshSftpBrowser();
        requests.sftpDownload[1].resolve(true); await tick();
        assert.equal(document.getElementById('sftp-status').textContent,'');
        assert.equal(document.getElementById('sftp-download-btn').disabled,true);
    `,
    sftp_error_and_retry_feedback: `
        await login(7);
        requests.sftp.at(-1).resolve({ok:false,status:503}); await tick();
        assert.ok(document.getElementById('sftp-status').textContent.includes('Could not load directory'));
        assert.ok(document.getElementById('sftp-status').textContent.includes('/home/kace'));
        loadSftpDirectory('/home/kace');
        assert.ok(document.getElementById('sftp-status').textContent.includes('Loading directory'));
        requests.sftp.at(-1).resolve({ok:true,json:async()=>({path:'/home/kace',generation:7,items:[]})}); await tick();
        assert.equal(document.getElementById('sftp-status').textContent,'/home/kace: 0 items');
    `,
    disconnect_diagnostic_is_not_host_diagnosis: `
        for (const expected of [true, false]) {
            await login(expected ? 7 : 8); begin();
            window.updateBootstrapDisconnected('boot', '', expected, powerContext);
            const message = document.getElementById('bootstrap-stage-label').textContent;
            assert.ok(message.includes('Host status is unknown'));
            assert.equal(currentDeviceState, 'BOOTSTRAP_RECOVERABLE');
            assert.equal(bootstrapActive, false);
        }
        await login(9); begin();
        window.updateBootstrapDisconnected('boot', 'SSH channel closed in VERIFYING_CONFIG', true, powerContext);
        assert.equal(document.getElementById('bootstrap-stage-label').textContent, studioText('sshLost'));
        assert.equal(document.getElementById('connection-subtitle').title, 'SSH channel closed in VERIFYING_CONFIG');
        assert.equal(printerPowerStatus, 'unknown');
    `,
    robin_final_download: `
        await login(7);
        const rows = robinEvents || ['Robin_nano.bin', 'Robin_nano35.bin', 'Robin_nano43.bin'].map((name, i) => ({
            ...event(i + 1), data: {staged_path: '/home/kace/kace/deploy/run/' + name,
                final_filename: name, download_available: true, language: 'English'}
        }));
        for (const [index, source] of rows.entries()) {
            const row = {...source, workflow_id: 'robin-download-fixture', sequence: index + 1};
            assert.equal(window.updateKaceWorkflowEvent(row, 7), true);
            const button = document.getElementById('kace-firmware-download');
            assert.equal(button.dataset.remotePath, row.data.staged_path);
            assert.equal(window.downloadKaceFirmwareArtifact(), true); await tick();
            assert.equal(requests.downloads.at(-1)[0], row.data.staged_path);
            assert.equal(requests.downloads.at(-1)[1], 7);
            assert.notEqual(row.data.final_filename, 'klipper.bin');
        }
        updateConnectionStatus(false);
        const before = requests.downloads.length;
        assert.equal(window.downloadKaceFirmwareArtifact(), false);
        assert.equal(requests.downloads.length, before);
    `,
    progress_and_download: `
        await login(7);
        assert.equal(window.updateKaceWorkflowEvent(event(1), 7), true);
        begin();
        assert.equal(firmwareGeneration, 7);
        assert.equal(kaceWorkflowViews.get('flow').sequence, 1);
        assert.equal(window.updateKaceWorkflowEvent(event(2, 'AWAITING_FLASH'), 7), true);
        assert.equal(document.getElementById('kace-firmware-download').disabled, false);
        assert.equal(window.downloadKaceFirmwareArtifact(), true); await tick();
        assert.equal(JSON.stringify(requests.downloads), JSON.stringify([['/home/kace/kace/firmware.bin', 7]]));
        window.updateDeviceState('SSH_READY', 100, 'Duplicate connected notification');
        assert.equal(firmwareGeneration, 7);
        assert.equal(window.updateKaceWorkflowEvent(event(1), 7), false);
        assert.equal(window.updateKaceWorkflowEvent(event(3), 6), false);
    `,
    checkpoint_survives_bootstrap: `
        await login(7); begin();
        const watch = timers.find(t => t.delay === 3000);
        assert.ok(watch);
        const count = requests.checkpoint.length;
        const pending = watch.fn(); await tick();
        assert.equal(requests.checkpoint.length, count + 1);
        requests.checkpoint.at(-1).resolve({generation: 7, event: event(3, 'VERIFYING_MCU')});
        await pending;
        assert.equal(kaceWorkflowViews.get('flow').state, 'VERIFYING_MCU');
        assert.ok(timers.filter(t => t.delay === 3000).length >= 2);
    `,
    disconnect_reconnect_rejects_delayed_results: `
        await login(7); begin();
        window.updateKaceWorkflowEvent(event(1), 7);
        const oldCheckpoint = requests.checkpoint.at(-1), oldSftp = requests.sftp.at(-1);
        updateConnectionStatus(false);
        assert.equal(firmwareGeneration, null);
        assert.equal(window.downloadKaceFirmwareArtifact(), false);
        assert.equal(window.updateKaceWorkflowEvent(event(2), 7), false);
        await login(8);
        oldCheckpoint.resolve({generation: 7, event: event(99, 'COMPLETE')});
        oldSftp.resolve({ok: true, json: async () => ({path: '/old', items: [], generation: 7})});
        await tick();
        assert.equal(kaceWorkflowViews.size, 0);
        assert.equal(sftpGeneration, null);
        assert.equal(window.updateKaceWorkflowEvent(event(100), 7), false);
        assert.equal(window.updateKaceWorkflowEvent(event(2), 8), true);
    `,
    double_login_and_session_replacement: `
        performSshLogin('one', 'unused'); await tick(); const one = requests.login.at(-1);
        performSshLogin('two', 'unused'); await tick(); const two = requests.login.at(-1);
        two.resolve(result(9)); await tick(); one.resolve(result(8)); await tick();
        assert.equal(firmwareGeneration, 9);
        window.updateKaceWorkflowEvent(event(10), 9);
        await login(10);
        assert.equal(kaceWorkflowViews.size, 0);
        assert.equal(window.updateKaceWorkflowEvent(event(1), 10), true);
        assert.equal(window.updateKaceWorkflowEvent(event(11), 9), false);
    `,
    failed_bootstrap_is_not_completion: `
        await login(7); begin(); window.updateKaceWorkflowEvent(event(2, 'VERIFYING_MCU'), 7);
        window.updateBootstrapEvent({protocol: 'kace-bootstrap/v1', workflow_id: 'boot', sequence: 2, event: 'workflow_failed', code: 'KACE_INSTALL'}, powerContext);
        assert.equal(firmwareGeneration, 7);
        assert.equal(kaceWorkflowViews.get('flow').bootstrapFailed, true);
        assert.notEqual(kaceWorkflowViews.get('flow').state, 'COMPLETE');
        assert.equal(currentDeviceState, 'BOOTSTRAP_FAILED');
        assert.equal(bootstrapActive, false);
        assert.equal(document.getElementById('kace-workflow-tracker').classList.contains('success'), false);
    `,
    reconnect_restores_remote_checkpoint: `
        await login(7); begin(); updateConnectionStatus(false);
        const checkpoint = {generation: 8, event: event(4, 'COMPLETE'), checkpoint: {state: 'COMPLETE'}};
        window.restoreSshAfterReconnect(result(8), powerSelection, checkpoint);
        assert.equal(firmwareGeneration, 8);
        assert.equal(kaceWorkflowViews.get('flow').state, 'COMPLETE');
        window.updateDeviceState('SSH_READY', 100, 'Connected');
        assert.equal(kaceWorkflowViews.get('flow').state, 'COMPLETE');
        assert.equal(window.updateKaceWorkflowEvent(event(5), 7), false);
    `,
    sftp_pending_list_survives_status: `
        await login(7);
        const listing = requests.sftp.at(-1), count = requests.sftp.length;
        begin();
        assert.equal(requests.sftp.length, count);
        listing.resolve({ok: true, json: async () => ({path: '/home/kace', items: [], generation: 7})});
        await tick(); assert.equal(sftpGeneration, 7);
    `,
};
cases.post_bootstrap_disconnect_invalidates_session = cases.recoverable_disconnect_invalidates_session;
const scenario = cases[process.argv[3]];
assert.ok(scenario, 'Unknown scenario');
vm.runInContext(setup + '\n(async () => {' + scenario + '})()', context)
    .then(() => console.log('completed'))
    .catch(error => {console.error(error); process.exitCode = 1;});
