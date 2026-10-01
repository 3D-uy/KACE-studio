// Execute all of app.js. Only browser/bridge IO is simulated, not UI handlers.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map(), timers = [], requests = {login: [], checkpoint: [], sftp: [], downloads: []};
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
const context = {console, assert, requests, timers, deferred,
    robinEvents: process.argv[4] ? JSON.parse(fs.readFileSync(process.argv[4], 'utf8')) : null,
    document: {addEventListener() {}, querySelectorAll() {return [];}, createElement: node,
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
    window.updateBootstrapEvent({protocol: 'kace-bootstrap/v1', workflow_id: 'boot', sequence: 1, event: 'workflow_started'});
}
`;
const cases = {
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
        assert.equal(scans,0); // The timeout does not initiate a late scan.
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
        await login(7); begin();
        for (const expected of [true, false]) {
            window.updateBootstrapDisconnected('boot', '', expected);
            const message = document.getElementById('bootstrap-stage-label').textContent;
            assert.ok(message.includes('Host status is unknown'));
            assert.equal(currentDeviceState, 'BOOTSTRAP_RECOVERABLE');
            assert.equal(bootstrapActive, false);
        }
        window.updateBootstrapDisconnected('boot', 'SSH channel closed in VERIFYING_CONFIG', true);
        assert.ok(document.getElementById('bootstrap-stage-label').textContent.includes('SSH channel closed in VERIFYING_CONFIG'));
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
        window.updateBootstrapEvent({protocol: 'kace-bootstrap/v1', workflow_id: 'boot', sequence: 2, event: 'workflow_failed', code: 'KACE_INSTALL'});
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
const scenario = cases[process.argv[3]];
assert.ok(scenario, 'Unknown scenario');
vm.runInContext(setup + '\n(async () => {' + scenario + '})()', context)
    .then(() => console.log('completed'))
    .catch(error => {console.error(error); process.exitCode = 1;});
