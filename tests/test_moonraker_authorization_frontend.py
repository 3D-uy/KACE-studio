"""Run the enrollment UI against the existing simulated bridge and DOM."""

import shutil
import subprocess

import pytest

from tests.test_power_frontend_identity import (
    HARNESS, GLOBALS_JS, LOGIN_JS, BOOTSTRAP_JS, POWER_JS,
)


@pytest.mark.parametrize("scenario", ["approve", "cancel", "late_inspect", "late_authorize"])
def test_explicit_enrollment_and_stale_responses(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required")
    script = HARNESS + GLOBALS_JS + LOGIN_JS + BOOTSTRAP_JS + POWER_JS + r'''
term = {cols: 80, rows: 24, write() {}, clear() {}};
(async () => {
    const inspection = deferred(), enrollment = deferred();
    const confirmations = [], notices = [], calls = [];
    api.inspect_moonraker_client = context => {calls.push(['inspect', context]); return inspection.promise;};
    api.authorize_moonraker_client = (ip, context) => {calls.push(['authorize', ip, context]); return enrollment.promise;};
    window.confirm = message => {confirmations.push(message); return SCENARIO !== 'cancel';};
    window.alert = message => notices.push(message);
    select('A.local');
    sshConnected = true;
    const a = establish(1);
    assert.equal(calls.length, 0);
    const clicked = authorizeMoonrakerAccess();
    assert.deepEqual(calls, [['inspect', a]]);
    if (SCENARIO === 'late_inspect') select('B.local');
    inspection.resolve({ok: true, ip: '192.168.1.23', power_context: a});
    await tick();
    if (SCENARIO === 'cancel' || SCENARIO === 'late_inspect') {
        await clicked;
        assert.equal(calls.length, 1);
        assert.equal(notices.length, 0);
        assert.equal(confirmations.length, SCENARIO === 'cancel' ? 1 : 0);
        return;
    }
    assert.match(confirmations[0], /192\.168\.1\.23/);
    assert.match(confirmations[0], /a\.local/);
    assert.deepEqual(calls[1], ['authorize', '192.168.1.23', a]);
    if (SCENARIO === 'late_authorize') select('B.local');
    enrollment.resolve({ok: true, changed: true, power_context: a});
    await clicked;
    assert.equal(notices.length, SCENARIO === 'approve' ? 1 : 0);
    if (SCENARIO === 'late_authorize') {
        assert.equal(powerContext, null);
        assert.equal(document.getElementById('authorize-moonraker-btn').disabled, true);
    }
})().then(() => console.log('completed')).catch(error => {console.error(error); process.exitCode = 1;});
'''
    script = "const SCENARIO = " + repr(scenario) + ";\n" + script
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "completed"
