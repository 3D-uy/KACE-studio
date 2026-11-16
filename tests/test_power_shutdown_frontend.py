"""Run production UI actions with a simulated DOM/bridge; no physical E2E."""
import shutil
import subprocess

import pytest

from tests.test_power_frontend_identity import HARNESS, CATALOG_JS, GLOBALS_JS, POWER_JS, LOGIN_JS


CASES = {
    "emergency_reconnect_does_not_inherit_busy_or_late_acknowledgement": r"""
        const first = deferred(), second = deferred(), alerts = [];
        let calls = 0;
        api.emergency_stop = () => (++calls === 1 ? first : second).promise;
        window.alert = message => alerts.push(message);
        const old = requestLogicalEmergencyStop();
        window.invalidatePowerSession(a);
        const renewed = establish(2);
        assert.equal(emergencyStopRequestActive, false);
        const current = requestLogicalEmergencyStop();
        assert.equal(calls, 2);
        first.resolve({ok:true, status:'requested', power_context:a});
        await old;
        assert.equal(emergencyStopRequestActive, true);
        assert.equal(alerts.length, 0);
        second.resolve({ok:true, status:'requested', power_context:a});
        await current;
        assert.deepEqual(alerts, [studioText('emergencyUnknown')]);
        assert.equal(emergencyStopRequestActive, false);
        assert.deepEqual(powerContext, renewed);
    """,
    "cancel_review": r"""
        const confirmations = [];
        window.confirm = message => {confirmations.push(message); return false;};
        const action = togglePrinterPower();
        await tick();
        assert.equal(requests.command.length, 0);
        assert.equal(confirmations.length, 1);
        assert.match(confirmations[0], /a.local/);
        assert.match(confirmations[0], /shared_relay/);
        assert.match(confirmations[0], /25/);
        assert.match(confirmations[0], /standby/);
        assert.ok(confirmations[0].includes(studioText('powerOffConfirm').split('\n')[0]));
        requests.status.at(-1).resolve(result(a, 'on'));
        await action;
    """,
    "selection_changes_inside_confirmation": r"""
        window.confirm = () => {select('B.local'); return true;};
        await togglePrinterPower();
        assert.equal(requests.command.length, 0);
        assert.equal(powerContext, null);
    """,
    "blocked_review_and_no_retry": r"""
        let reviews = 0, confirmations = 0;
        const alerts = [];
        api.inspect_power_off = async () => {reviews++; return {ok:false, code:'powerOffJob', power_context:a};};
        window.confirm = () => {confirmations++; return true;};
        window.alert = message => alerts.push(message);
        const action = togglePrinterPower();
        await tick();
        assert.equal(reviews, 1);
        assert.equal(confirmations, 0);
        assert.equal(requests.command.length, 0);
        assert.deepEqual(alerts, [studioText('powerOffJob')]);
        requests.status.at(-1).resolve(result(a, 'on'));
        await action;
        assert.equal(reviews, 1);
    """,
    "double_click_during_review_and_send": r"""
        const review = deferred();
        let reviews = 0;
        api.inspect_power_off = () => {reviews++; return review.promise;};
        const action = togglePrinterPower();
        await togglePrinterPower();
        assert.equal(reviews, 1);
        assert.equal(requests.command.length, 0);
        review.resolve({ok:true, device:'shared_relay', power_context:a, approval_token:'single',
            review:{state:'standby', heaters:{extruder:{temperature:29,target:0}}}});
        await tick();
        await togglePrinterPower();
        assert.equal(requests.command.length, 1);
        assert.deepEqual(requests.command[0].args, ['a.local','shared_relay',a,'single']);
        requests.command[0].reject(new Error('lost response'));
        await tick();
        requests.status.at(-1).resolve(result(a, 'on'));
        await action;
        assert.equal(requests.command.length, 1);
    """,
    "emergency_independent_unknown_state_and_normal_busy": r"""
        printerPowerStatus = 'unknown';
        printerPowerRequestActive = true;
        const emergency = deferred(), calls = [], alerts = [];
        api.emergency_stop = (...args) => {calls.push(args); return emergency.promise;};
        window.alert = message => alerts.push(message);
        renderPrinterPower({status:'unknown', available:false});
        assert.equal(document.getElementById('printer-emergency-btn').disabled, false);
        const action = requestLogicalEmergencyStop();
        await requestLogicalEmergencyStop();
        assert.deepEqual(calls, [['a.local', a]]);
        assert.equal(document.getElementById('printer-emergency-btn').disabled, true);
        emergency.reject(new Error('response lost'));
        await action;
        assert.deepEqual(alerts, [studioText('emergencyUnknown')]);
        assert.equal(calls.length, 1);
        assert.equal(requests.command.length, 0);
        assert.equal(printerPowerStatus, 'unknown');
    """,
    "emergency_cancel_stale_and_no_false_cutoff": r"""
        const calls = [], alerts = [];
        api.emergency_stop = async (...args) => {calls.push(args); return {ok:true, status:'requested', power_context:a};};
        window.alert = message => alerts.push(message);
        window.confirm = () => false;
        await requestLogicalEmergencyStop();
        assert.equal(calls.length, 0);
        window.confirm = () => true;
        await requestLogicalEmergencyStop();
        assert.deepEqual(alerts, [studioText('emergencyRequested')]);
        assert.equal(printerPowerStatus, 'on');
        assert.equal(requests.command.length, 0);
        window.confirm = () => {select('B.local'); return true;};
        await requestLogicalEmergencyStop();
        assert.equal(calls.length, 1);
    """,
}


@pytest.mark.parametrize("language", ["English", "Español", "Português"])
@pytest.mark.parametrize("case", CASES)
def test_shutdown_ui(case, language):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the frontend regression")
    script = HARNESS + CATALOG_JS + GLOBALS_JS + LOGIN_JS + POWER_JS + "\n" + r"""
    term = {cols: 80, rows: 24, write() {}, clear() {}};
    (async () => {
        selectedStudioLanguage = LANGUAGE;
        select('A.local');
        const a = establish(1);
        requests.status.at(-1).resolve(result(a, 'on'));
        await tick();
    """.replace("LANGUAGE", repr(language)) + CASES[case] + r"""
    })().then(() => console.log('completed')).catch(error => {console.error(error); process.exitCode = 1;});
    """
    result = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "completed", "The asynchronous scenario did not complete"
