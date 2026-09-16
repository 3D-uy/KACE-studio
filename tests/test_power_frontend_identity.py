"""Execute the production power/login JavaScript with a simulated bridge and DOM."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web/app.js").read_text(encoding="utf-8")
POWER_JS = APP[APP.index("function renderPrinterPower"):APP.index("function startBootstrap")]
LOGIN_JS = APP[APP.index("function performSshLogin"):APP.index("// Terminal Workspace (Stage C)")]
BOOTSTRAP_JS = (
    APP[APP.index("function completeBootstrapSuccess"):APP.index("function completeBootstrapTerminal")]
    + APP[APP.index("window.updateBootstrapEvent ="):APP.index("window.updateBootstrapInterrupted =")]
    + APP[APP.index("function parseBootstrapProgress"):APP.index("function updateConnectionStatus")]
)
GLOBALS_JS = APP[:APP.index("let userPreferences")]

HARNESS = r"""
const assert = require('node:assert/strict');
const nodes = new Map();
const document = {getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {
        value: 'local_suggestion', disabled: false, style: {},
        classList: {add() {}, remove() {}}, click() {},
    });
    return nodes.get(id);
}};
function deferred() {
    let resolve, reject;
    const promise = new Promise((ok, fail) => {resolve = ok; reject = fail;});
    return {promise, resolve, reject};
}
const requests = {status: [], command: [], login: [], authority: [], selection: []};
function queue(kind, args) {
    const request = {...deferred(), args};
    requests[kind].push(request);
    return request.promise;
}
const api = {
    select_power_target: (...args) => {requests.selection.push(args); return Promise.resolve(true);},
    get_power_status: (...args) => queue('status', args),
    power_on: (...args) => queue('command', args),
    power_off: (...args) => queue('command', args),
    get_remote_power_config: (...args) => queue('authority', args),
    connect_ssh: (...args) => queue('login', args),
    disconnect_ssh: () => Promise.resolve(true),
    resize_ssh_pty() {},
    get_firmware_deployment_manifest: () => Promise.resolve({}),
    get_firmware_workflow_checkpoint: () => Promise.resolve({}),
};
const window = {pywebview: {api}, setInterval() {return 1;}, clearInterval() {}};
window.updateDeviceState = () => {};
const setTimeout = () => 0;
const BOOTSTRAP_STAGES = [];
const bootstrapEventCursors = new Map();
let bootstrapActive = false, bootstrapAuthoritativeSeen = false, bootstrapFailureHandled = false;
let bootstrapBuffer = '';
function updateConnectionStatus(connected) {sshConnected = connected;}
function promptTerminalLogin() {}
function restoreKaceDeploymentManifest() {}
const tick = async () => {for (let i = 0; i < 8; i++) await Promise.resolve();};
function authority(context, device = 'shared_relay') {
    return {status: 'configured', config: {enabled: true, device}, power_context: context};
}
function result(context, status) {
    return {ok: true, available: true, device: 'shared_relay', status, power_context: context};
}
function select(host) {connectToDevice(host, host);}
function establish(session) {
    const context = {host: currentDeviceIp.toLowerCase(), selection: powerSelection, session};
    applyRemotePowerConfig(authority(context), powerSelection);
    return context;
}
"""


CASES = {
    "on_off_waits_for_observed_state_and_blocks_duplicate_commands": r"""
        const actions = [];
        for (const action of ['power_on', 'power_off']) {
            api[action] = (...args) => {actions.push(action); return queue('command', args);};
        }
        select('A.local');
        const a = establish(1);
        const button = document.getElementById('printer-power-btn');
        const label = document.getElementById('printer-power-state');
        assert.equal(button.disabled, true);
        await togglePrinterPower();
        assert.equal(requests.command.length, 0);
        requests.status.at(-1).resolve(result(a, 'off'));
        await tick();

        for (const [action, observed] of [['power_on', 'on'], ['power_off', 'off']]) {
            assert.equal(button.disabled, false);
            const previous = printerPowerStatus;
            const polls = requests.status.length;
            const commands = requests.command.length;
            const toggle = togglePrinterPower();
            assert.equal(actions.at(-1), action);
            assert.deepEqual(requests.command.at(-1).args, ['a.local', 'shared_relay', a]);
            assert.equal(button.disabled, true);
            assert.equal(label.textContent, 'init');
            await togglePrinterPower();
            await refreshPrinterPower();
            assert.equal(requests.command.length, commands + 1);
            assert.equal(requests.status.length, polls);

            // An acknowledgement is not the observed relay state.
            requests.command.at(-1).resolve(result(a, previous));
            await tick();
            assert.equal(printerPowerRequestActive, false);
            assert.equal(requests.status.length, polls + 1);
            assert.deepEqual(requests.status.at(-1).args, ['a.local', 'shared_relay', a]);
            assert.equal(label.textContent, 'init');
            assert.equal(button.disabled, true);
            await togglePrinterPower();
            assert.equal(requests.command.length, commands + 1);

            requests.status.at(-1).resolve(result(a, observed));
            await toggle;
            assert.equal(printerPowerStatus, observed);
            assert.equal(label.textContent, observed);
            assert.equal(button.disabled, false);
        }
        assert.deepEqual(actions, ['power_on', 'power_off']);
    """,
    "power_errors_disable_actions_and_allow_polling_recovery": r"""
        select('A.local');
        const a = establish(1);
        const button = document.getElementById('printer-power-btn');
        const label = document.getElementById('printer-power-state');
        requests.status.at(-1).reject(new Error('Moonraker unreachable'));
        await tick();
        assert.equal(label.textContent, 'error');
        assert.equal(button.disabled, true);
        assert.equal(powerStatusRequest, null);
        await togglePrinterPower();
        assert.equal(requests.command.length, 0);

        const recovered = refreshPrinterPower();
        requests.status.at(-1).resolve(result(a, 'off'));
        await recovered;
        assert.equal(button.disabled, false);
        const toggle = togglePrinterPower();
        const polls = requests.status.length;
        requests.command.at(-1).reject(new Error('Command failed'));
        await tick();
        assert.equal(printerPowerRequestActive, false);
        assert.equal(label.textContent, 'error');
        assert.equal(button.disabled, true);
        assert.equal(requests.status.length, polls + 1);
        // A reachable device reporting an error must also remain disabled.
        requests.status.at(-1).resolve(result(a, 'error'));
        await toggle;
        assert.equal(printerPowerAvailable, true);
        assert.equal(button.disabled, true);
        assert.equal(powerStatusRequest, null);
        await togglePrinterPower();
        assert.equal(requests.command.length, 1);

        const retry = refreshPrinterPower();
        requests.status.at(-1).resolve(result(a, 'on'));
        await retry;
        assert.equal(label.textContent, 'on');
        assert.equal(button.disabled, false);
        assert.equal(printerPowerRequestActive, false);
        assert.deepEqual(powerContext, a);
    """,
    "selection_and_late_status": r"""
        select('A.local');
        assert.equal(printerPowerAvailable, false);
        await togglePrinterPower();
        assert.equal(requests.command.length, 0);
        const a = establish(1);
        const pending = requests.status.at(-1);
        assert.deepEqual(pending.args, ['a.local', 'shared_relay', a]);
        select('B.local');
        assert.equal(powerContext, null);
        assert.equal(remotePowerAuthority, null);
        assert.equal(printerPowerAvailable, false);
        await togglePrinterPower();
        assert.equal(requests.command.length, 0);
        const b = establish(2);
        requests.status.at(-1).resolve(result(b, 'off'));
        await tick();
        pending.resolve(result(a, 'on'));
        await tick();
        assert.equal(printerPowerStatus, 'off');
        assert.deepEqual(powerContext, b);
        const toggle = togglePrinterPower();
        assert.deepEqual(requests.command.at(-1).args, ['b.local', 'shared_relay', b]);
        requests.command.at(-1).resolve(result(b, 'on'));
        await tick();
        requests.status.at(-1).resolve(result(b, 'on'));
        await toggle;
        assert.equal(printerPowerStatus, 'on');
    """,
    "pending_command_cannot_clear_new_request": r"""
        select('A.local');
        const a = establish(1);
        requests.status.at(-1).resolve(result(a, 'off'));
        await tick();
        const oldToggle = togglePrinterPower();
        const oldRequest = requests.command.at(-1);
        select('B.local');
        const b = establish(2);
        requests.status.at(-1).resolve(result(b, 'off'));
        await tick();
        const newToggle = togglePrinterPower();
        const newRequest = requests.command.at(-1);
        const count = requests.status.length;
        oldRequest.reject(new Error('A disconnected'));
        await oldToggle;
        assert.equal(printerPowerRequestActive, true);
        assert.equal(requests.status.length, count);
        assert.equal(printerPowerStatus, 'init');
        newRequest.resolve(result(b, 'on'));
        await tick();
        requests.status.at(-1).resolve(result(b, 'on'));
        await newToggle;
        assert.equal(printerPowerRequestActive, false);
    """,
    "rapid_a_b_a_authority_and_status_out_of_order": r"""
        select('A.local');
        const a1 = establish(1);
        const oldStatus = requests.status.at(-1);
        const refreshing = refreshRemotePowerConfig(a1);
        const oldAuthority = requests.authority.at(-1);
        select('B.local');
        const b = establish(2);
        const bStatus = requests.status.at(-1);
        select('A.local');
        const a2 = establish(3);
        requests.status.at(-1).resolve(result(a2, 'off'));
        await tick();
        oldAuthority.resolve(authority(a1, 'old_relay'));
        bStatus.resolve(result(b, 'on'));
        oldStatus.resolve(result(a1, 'on'));
        await refreshing;
        await tick();
        applyRemotePowerConfig(authority(a1), a1.selection);
        window.invalidatePowerSession(a1);
        assert.deepEqual(powerContext, a2);
        assert.equal(remotePowerAuthority.config.device, 'shared_relay');
        assert.equal(printerPowerStatus, 'off');
        const count = requests.authority.length;
        // A late bootstrap completion must not even start a refresh for new A.
        await refreshRemotePowerConfig(a1);
        await refreshRemotePowerConfig(b);
        assert.equal(requests.authority.length, count);
        assert.equal(printerPowerStatus, 'off');
    """,
    "disconnect_reconnect_late_login": r"""
        select('A.local');
        const a = establish(1);
        disconnectSSH();
        assert.equal(powerContext, null);
        assert.equal(printerPowerAvailable, false);
        await tick();
        performSshLogin('kace', 'simulated');
        await tick();
        const oldLogin = requests.login.at(-1);
        const oldContext = {host: 'a.local', selection: oldLogin.args[5], session: 2};
        assert.equal(printerPowerAvailable, false);
        select('B.local');
        performSshLogin('kace', 'simulated');
        await tick();
        const newLogin = requests.login.at(-1);
        const b = {host: 'b.local', selection: newLogin.args[5], session: 3};
        newLogin.resolve({status: 'success', power_config: authority(b)});
        await tick();
        requests.status.at(-1).resolve(result(b, 'off'));
        oldLogin.resolve({status: 'success', power_config: authority(oldContext)});
        await tick();
        assert.deepEqual(powerContext, b);
        assert.equal(printerPowerStatus, 'off');
        window.invalidatePowerSession(b);
        assert.equal(powerContext, null);
        assert.equal(printerPowerAvailable, false);
        await togglePrinterPower();
        assert.equal(requests.command.length, 0);
    """,
    "mismatched_or_unidentified_response_is_never_applied": r"""
        select('A.local');
        applyRemotePowerConfig({status: 'configured', config: {enabled: true, device: 'shared_relay'}}, powerSelection);
        assert.equal(powerContext, null);
        assert.equal(printerPowerAvailable, false);
        const a = establish(1);
        requests.status.at(-1).resolve(result({...a, host: 'b.local'}, 'on'));
        await tick();
        assert.equal(printerPowerStatus, 'init');
        assert.equal(document.getElementById('printer-power-btn').disabled, true);
    """,
    "slow_status_poll_is_not_superseded_by_timer_ticks": r"""
        select('A.local');
        const a = establish(1);
        await refreshPrinterPower();
        await refreshPrinterPower();
        assert.equal(requests.status.length, 1);
        requests.status[0].resolve(result(a, 'off'));
        await tick();
        assert.equal(printerPowerStatus, 'off');
        assert.equal(powerStatusRequest, null);
    """,
    "bootstrap_refresh_uses_originating_session_including_legacy_output": r"""
        select('A.local');
        const a = establish(1);
        select('B.local');
        const b = establish(2);
        requests.status.at(-1).resolve(result(b, 'off'));
        await tick();
        window.writeTerminalData('Bootstrap complete! KACE wizard finished successfully.', a);
        const success = {protocol: 'kace-bootstrap/v1', workflow_id: 'A', sequence: 1, event: 'workflow_succeeded'};
        window.updateBootstrapEvent(success, a);
        await tick();
        assert.equal(requests.authority.length, 0);
        assert.deepEqual(powerContext, b);
        assert.equal(printerPowerStatus, 'off');
        window.updateBootstrapEvent({...success, workflow_id: 'B'}, b);
        assert.equal(requests.authority.length, 1);
        assert.deepEqual(requests.authority[0].args, [b]);
        requests.authority[0].resolve(authority(b, 'B_relay'));
        await tick();
        assert.equal(remotePowerAuthority.config.device, 'B_relay');
    """,
}


@pytest.mark.parametrize("case", CASES)
def test_frontend_power_identity(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the frontend regression")
    script = HARNESS + GLOBALS_JS + LOGIN_JS + BOOTSTRAP_JS + POWER_JS + "\n" + r"""
term = {cols: 80, rows: 24, write() {}, clear() {}};
(async () => {
""" + CASES[case] + r"""
})().then(() => console.log('completed')).catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "completed", "The asynchronous scenario did not complete"
