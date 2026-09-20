"""Exercise the reconnect worker without starting PyWebView or real SSH."""
import ast
import json
from pathlib import Path
import threading
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def worker_api():
    source = ast.parse((Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8"))
    api = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "Api")
    method = next(node for node in api.body if isinstance(node, ast.FunctionDef) and node.name == "_recover_ssh")
    namespace = {"time": SimpleNamespace(sleep=Mock()), "json": json}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "main.py", "exec"), namespace)
    fake = SimpleNamespace(_ssh_lock=threading.RLock(), _ssh_attempt_gen=3,
        _window=Mock(), set_device_state=Mock(),
        get_firmware_workflow_checkpoint=Mock(return_value={"generation": 4, "checkpoint": {"state": "DEPLOYING"}}))
    return namespace["_recover_ssh"], fake, namespace["time"].sleep


@pytest.mark.parametrize("status,count", [("success", 1), ("failed", 3), ("host_key_mismatch", 1)])
def test_reconnect_limits_and_reports_authoritative_checkpoint(status, count):
    worker, api, sleep = worker_api()
    def connect(*args, **kwargs):
        assert kwargs["_reconnect_attempt"] == api._ssh_attempt_gen
        api._ssh_attempt_gen += 1
        return {"status": status, "generation": 4}
    api.connect_ssh = Mock(side_effect=connect)
    worker(api, "pi", "kace", "secret", 80, 24, 1, 3)
    assert api.connect_ssh.call_count == count
    assert sleep.call_count == count
    if status == "success":
        script = api._window.evaluate_js.call_args.args[0]
        assert "restoreSshAfterReconnect" in script
        assert "DEPLOYING" in script
        assert "secret" not in script
    else:
        api.get_firmware_workflow_checkpoint.assert_not_called()


def test_manual_disconnect_or_new_selection_cancels_recovery():
    worker, api, sleep = worker_api()
    api.connect_ssh = Mock()
    sleep.side_effect = lambda _: setattr(api, "_ssh_attempt_gen", 4)
    worker(api, "pi", "kace", "secret", 80, 24, 1, 3)
    api.connect_ssh.assert_not_called()
    api._window.evaluate_js.assert_not_called()


def test_reconnected_terminal_never_calls_pending_installation_complete():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend execution")
    app = (Path(__file__).parents[1] / "web/app.js").read_text(encoding="utf-8")
    handler = app[app.index("window.restoreSshAfterReconnect ="):app.index("function performSshLogin")]
    script = """
const assert = require('node:assert/strict');
const window = {updateKaceWorkflowEvent(){}};
let powerSelection=1, firmwareGeneration=null, loginState='', output='';
const term={write(text){output+=text}};
function updateConnectionStatus(){}
function applyRemotePowerConfig(){}
function startFirmwareCheckpointWatch(){}
""" + handler + """
const result={status:'success',generation:4};
const pending={generation:4,event:{},checkpoint:{state:'DEPLOYING'}};
window.restoreSshAfterReconnect(result,2,pending);
assert.equal(output,'');
window.restoreSshAfterReconnect(result,1,pending);
assert.ok(output.includes('falta confirmar'));
assert.ok(!output.includes('instalados y verificados'));
output=''; pending.checkpoint.state='COMPLETE';
window.restoreSshAfterReconnect(result,1,pending);
assert.ok(output.includes('instalados y verificados'));
"""
    completed = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8", capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
