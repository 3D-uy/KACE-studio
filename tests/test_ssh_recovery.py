"""Exercise the reconnect worker without starting PyWebView or real SSH."""
import ast
import json
from pathlib import Path
import threading
import shutil
import subprocess
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
import main


def worker_api():
    source = ast.parse((Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8"))
    api = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "Api")
    methods = [node for node in api.body if isinstance(node, ast.FunctionDef) and node.name in {
        "_recover_ssh", "_wait_ssh_recovery", "_observe_recovered_installation"}]
    namespace = {"time": SimpleNamespace(sleep=Mock()), "json": json}
    namespace["time"].monotonic = lambda: sum(call.args[0] for call in namespace["time"].sleep.call_args_list)
    exec(compile(ast.Module(body=methods, type_ignores=[]), "main.py", "exec"), namespace)
    fake = SimpleNamespace(_ssh_lock=threading.RLock(), _ssh_attempt_gen=3,
        _window=Mock(), set_device_state=Mock(),
        get_firmware_workflow_checkpoint=Mock(return_value={"generation": 4, "checkpoint": {"state": "DEPLOYING"}}))
    fake._wait_ssh_recovery = MethodType(namespace["_wait_ssh_recovery"], fake)
    fake._observe_recovered_installation = MethodType(namespace["_observe_recovered_installation"], fake)
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
    assert sum(call.args[0] for call in sleep.call_args_list) == (47 if status == "success" else 32 if count == 3 else 2)
    if status == "success":
        script = api._window.evaluate_js.call_args_list[0].args[0]
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


def test_host_available_after_old_retry_window_can_connect_on_third_attempt():
    worker, api, sleep = worker_api()
    connection_times = []
    def connect(*args, **kwargs):
        elapsed = sum(call.args[0] for call in sleep.call_args_list)
        connection_times.append(elapsed)
        api._ssh_attempt_gen += 1
        return {"status": "success" if elapsed >= 25 else "failed", "generation": 4}
    api.connect_ssh = Mock(side_effect=connect)
    api.get_firmware_workflow_checkpoint.return_value = None
    worker(api, "pi", "kace", "secret", 80, 24, 1, 3)
    assert connection_times == [2, 12, 32]
    assert "restoreSshAfterReconnect" in api._window.evaluate_js.call_args.args[0]


def projection(state="DEPLOYING", sequence=1, workflow="install", generation=4, **extra):
    return {"generation": generation, "checkpoint": {
        "workflow_id": workflow, "sequence": sequence, "state": state, **extra}}


@pytest.mark.parametrize("final_state", ["COMPLETE", "READY_TO_DEPLOY"])
def test_recovery_observes_late_authoritative_verification_without_restarting(final_state):
    _, api, sleep = worker_api()
    initial = projection()
    final = projection(final_state, 2)
    api.get_firmware_workflow_checkpoint.side_effect = [initial] * 4 + [final]
    api._observe_recovered_installation({"generation": 4}, 1, 3, initial)
    assert sum(call.args[0] for call in sleep.call_args_list) == 15
    script = api._window.evaluate_js.call_args.args[0]
    assert "finishSshRecovery" in script and final_state in script
    assert api.get_firmware_workflow_checkpoint.call_count == 5


def test_recovery_observation_expires_without_manufacturing_complete():
    _, api, sleep = worker_api()
    initial = projection()
    api.get_firmware_workflow_checkpoint.return_value = None
    api._observe_recovered_installation({"generation": 4}, 1, 3, initial)
    assert sum(call.args[0] for call in sleep.call_args_list) == 45
    script = api._window.evaluate_js.call_args.args[0]
    assert "DEPLOYING" in script and "COMPLETE" not in script


def test_slow_reads_consume_the_polling_window_instead_of_restarting_its_budget():
    _, api, sleep = worker_api()
    read_seconds = [0]
    clock = api._wait_ssh_recovery.__func__.__globals__["time"]
    clock.monotonic = lambda: read_seconds[0] + sum(call.args[0] for call in sleep.call_args_list)
    def delayed_read():
        read_seconds[0] += 8
        return None
    api.get_firmware_workflow_checkpoint.side_effect = delayed_read
    api._observe_recovered_installation({"generation": 4}, 1, 3, projection())
    assert api.get_firmware_workflow_checkpoint.call_count == 4
    assert clock.monotonic() == 45


@pytest.mark.parametrize("observed", [projection("COMPLETE", 2, workflow="new"), projection("COMPLETE", 2, generation=3)])
def test_observation_never_accepts_another_workflow_or_session(observed):
    _, api, _ = worker_api()
    api.get_firmware_workflow_checkpoint.return_value = observed
    api._observe_recovered_installation({"generation": 4}, 1, 3, projection())
    scripts = [call.args[0] for call in api._window.evaluate_js.call_args_list]
    assert not any('"state": "COMPLETE"' in script for script in scripts)


def test_observation_cancels_during_wait_without_reading_checkpoint():
    _, api, sleep = worker_api()
    sleep.side_effect = lambda _: setattr(api, "_ssh_attempt_gen", 4)
    api._observe_recovered_installation({"generation": 4}, 1, 3, projection())
    api.get_firmware_workflow_checkpoint.assert_not_called()
    api._window.evaluate_js.assert_not_called()


def test_recorded_failure_does_not_delay_manual_recovery():
    _, api, sleep = worker_api()
    api._observe_recovered_installation({"generation": 4}, 1, 3, projection(last_error="configuration mismatch"))
    sleep.assert_not_called()
    api.get_firmware_workflow_checkpoint.assert_not_called()


@pytest.mark.parametrize("initial", [None, {"generation": 4, "checkpoint": {}}])
def test_unavailable_initial_projection_is_not_treated_as_deploying(initial):
    _, api, sleep = worker_api()
    api._observe_recovered_installation({"generation": 4}, 1, 3, initial)
    sleep.assert_not_called()
    api.get_firmware_workflow_checkpoint.assert_not_called()


def test_equal_and_older_sequence_changes_do_not_finish_observation():
    _, api, sleep = worker_api()
    api.get_firmware_workflow_checkpoint.side_effect = [
        projection("COMPLETE", 1), projection("COMPLETE", 0), projection("COMPLETE", 2)]
    api._observe_recovered_installation({"generation": 4}, 1, 3, projection())
    assert sum(call.args[0] for call in sleep.call_args_list) == 9
    assert api.get_firmware_workflow_checkpoint.call_count == 3
    assert '"sequence": 2' in api._window.evaluate_js.call_args.args[0]


@pytest.mark.parametrize("cause", ["exception", "false", "probe_closed"])
@pytest.mark.parametrize("automatic", [False, True])
def test_failed_retry_retains_disconnected_workflow_instead_of_generic_error(monkeypatch, cause, automatic):
    api = main.Api()
    api.set_device_state = Mock()
    candidate = Mock()
    candidate.is_connected.return_value = False
    candidate.connect.return_value = cause != "false"
    if cause == "exception":
        candidate.connect.side_effect = TimeoutError("Host is still rebooting")
    monkeypatch.setattr(main, "SSHSession", lambda: candidate)
    monkeypatch.setattr(api, "_refresh_remote_power_authority", lambda _: None)
    result = api.connect_ssh("pi.local", "kace", "simulated", _reconnect_attempt=0 if automatic else None)
    assert result["status"] == "failed"
    state, progress, _ = api.set_device_state.call_args.args
    assert (state, progress) == ("SSH_DISCONNECTED" if automatic else "ERROR", 0)
    candidate.run_command_stream.assert_not_called()


@pytest.mark.parametrize("scenario", ["reconnected_terminal_never_calls_pending_installation_complete",
                                      "recovered_installation_waits_for_authoritative_verification",
                                      "recovery_late_checkpoint_rebinds_preserved_workflow",
                                      "historical_complete_cannot_finish_newer_active_workflow_on_reconnect"])
def test_reconnected_terminal_never_calls_pending_installation_complete(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend execution")
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run([
        node, str(root / "tests/bootstrap_session_harness.cjs"), str(root / "web/app.js"),
        scenario,
    ], text=True, encoding="utf-8", capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip().endswith("completed")
