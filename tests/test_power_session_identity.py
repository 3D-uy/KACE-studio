"""Power authority must never cross a selected host or SSH-session boundary."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import main


class FakeSession:
    def __init__(self, device="shared_relay", *, active_low=False, read_hook=None):
        self.connected = False
        self.on_close = None
        self.read_hook = read_hook
        self.config = {
            "schema": "kace-power/v1", "revision": 1, "enabled": True,
            "device": device, "pin": "gpiochip0/gpio20", "active_low": active_low,
            "initial_state": "on", "restart_klipper_when_powered": True,
            "off_when_shutdown": False,
        }

    def connect(self, *_args):
        self.connected = True
        return True

    def is_connected(self):
        return self.connected

    def close(self):
        self.connected = False

    def read_text_file_result(self, path, **_kwargs):
        assert path == ".config/kace/power.json"
        if self.read_hook:
            self.read_hook()
        return "ok", json.dumps(self.config)

    def run_command_stream(self, _command, _on_data, on_close, **_kwargs):
        self.on_close = on_close


@pytest.fixture
def rig(monkeypatch):
    # Any accidental transport escape fails the test, including HTTP and SSH.
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Real network/hardware access is forbidden")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr(main, "SSHSession", FakeSession)
    api = main.Api()
    api.set_device_state = lambda *_args: None
    api._interrupt_bootstrap = lambda *_args: None
    api._suspend_bootstrap_for_ssh_loss = lambda *_args, **_kwargs: False
    posts = []
    clients = []

    class Http:
        def __init__(self, host):
            self.host = host
            self.state = "off"
            self.get_hook = None
            self.device = api._remote_power_authority["config"]["device"]
            clients.append(self)

        def get(self, path):
            assert path == "/machine/device_power/devices"
            if self.get_hook:
                self.get_hook()
            return {"result": {"devices": [{"device": self.device, "status": self.state}]}}

        def post(self, path, payload):
            posts.append((self.host, path, payload))
            self.state = payload["action"]
            return {"result": {}}

    monkeypatch.setattr("backend.power_controller.MoonrakerHttpClient", Http)

    def connect(host, device="shared_relay", **kwargs):
        selection = api._power_selection + 1
        assert api.select_power_target(host, selection)
        session = FakeSession(device, **kwargs)
        candidates = [session]
        monkeypatch.setattr(main, "SSHSession", lambda: candidates.pop() if candidates else FakeSession())
        result = api.connect_ssh(host, "kace", "simulated", power_selection=selection)
        assert result["status"] == "success"
        return result["power_config"]["power_context"], session

    return api, connect, posts, clients


def test_reproduced_cross_host_authority_is_rejected_even_without_selection(rig):
    api, connect, posts, _ = rig
    context, _ = connect("A.local", "relay-from-A")
    result = api.power_on("B.local", "relay-from-B", context)
    assert result["ok"] is False
    assert posts == []


def test_selection_invalidates_authority_controller_and_pending_context(rig):
    api, connect, posts, _ = rig
    context_a, _ = connect("A.local", "relay-from-A")
    assert api.power_on("A.local", "ignored", context_a)["status"] == "on"
    assert api._power_controller is not None
    assert api.select_power_target("B.local", context_a["selection"] + 1)
    assert api._remote_power_authority is api._power_controller is api._power_context is None
    assert api._power_target is api._power_session is None
    assert api.power_on("B.local", "relay-from-B", context_a)["ok"] is False
    assert api.power_on("B.local", "relay-from-B")["ok"] is False
    assert api.power_off("A.local", "relay-from-A", context_a)["ok"] is False
    assert len(posts) == 1
    context_b, _ = connect("B.local", "relay-from-B")
    assert api.power_on("B.local", "relay-from-A", context_b)["status"] == "on"
    assert [(host, payload["device"]) for host, _, payload in posts] == [
        ("a.local", "relay-from-A"), ("b.local", "relay-from-B"),
    ]


@pytest.mark.parametrize("active_low", [False, True])
def test_same_device_on_off_and_active_low_semantics_unchanged(rig, active_low):
    api, connect, posts, _ = rig
    context, session = connect("A.local", active_low=active_low)
    assert api._remote_power_authority["config"]["active_low"] is active_low
    assert api.get_power_status("A.local", "ignored", context)["status"] == "off"
    assert api.power_on("A.local", "ignored", context)["status"] == "on"
    assert api.wait_power_ready("A.local", "ignored", context)["status"] == "on"
    assert api.power_off("A.local", "ignored", context)["status"] == "off"
    assert [payload for _, _, payload in posts] == [
        {"device": "shared_relay", "action": "on"},
        {"device": "shared_relay", "action": "off"},
    ]
    assert session.config["active_low"] is active_low


@pytest.mark.parametrize("disconnect", ["explicit", "stream_close", "transport_lost"])
def test_disconnect_and_reconnection_require_new_authority(rig, disconnect):
    api, connect, posts, _ = rig
    old_context, session = connect("A.local")
    if disconnect == "explicit":
        assert api.disconnect_ssh(old_context["selection"])
    elif disconnect == "stream_close":
        session.close()
        session.on_close()
    else:
        session.close()
    assert api.power_on("A.local", "shared_relay", old_context)["ok"] is False
    assert api._remote_power_authority is api._power_controller is None
    assert posts == []
    context, _ = connect("A.local", "new_relay")
    assert context != old_context
    assert api.power_on("A.local", "shared_relay", old_context)["ok"] is False
    assert api.power_on("A.local", "shared_relay", context)["status"] == "on"
    assert posts[0][2]["device"] == "new_relay"


def test_rapid_a_b_a_rejects_old_sessions_even_with_identical_relay_names(rig):
    api, connect, posts, _ = rig
    first, old_session = connect("A.local")
    second, _ = connect("B.local")
    latest, _ = connect("A.local")
    old_session.on_close()  # An old close callback cannot invalidate the new A.
    for context in (first, second):
        assert api.power_on("A.local", "shared_relay", context)["ok"] is False
        assert api.get_remote_power_config(context)["status"] == "error"
    assert posts == []
    assert api.power_on("A.local", "shared_relay", latest)["status"] == "on"


@pytest.mark.parametrize("action", ["get_power_status", "power_on"])
def test_late_http_response_is_discarded_and_pending_on_never_posts(rig, action):
    api, connect, posts, clients = rig
    context_a, _ = connect("A.local")
    api.get_power_status("A.local", "shared_relay", context_a)
    started, release = threading.Event(), threading.Event()

    def block():
        started.set()
        assert release.wait(3)

    clients[0].get_hook = block
    with ThreadPoolExecutor() as pool:
        future = pool.submit(getattr(api, action), "A.local", "shared_relay", context_a)
        try:
            assert started.wait(2)
            context_b, _ = connect("B.local")
            assert api._remote_power_authority["power_context"] == context_b
        finally:
            release.set()
        result = future.result(timeout=2)
    assert result["ok"] is False
    assert result["available"] is False
    assert result["power_context"] == context_a
    assert posts == []
    assert api.power_on("B.local", "shared_relay", context_b)["ok"] is True


def test_queued_command_is_reauthorized_after_selection_change(rig):
    api, connect, posts, _ = rig
    context, _ = connect("A.local")
    reached_lock = threading.Event()

    class Gate:
        def __enter__(self):
            reached_lock.set()
            assert release.wait(3)

        def __exit__(self, *_args):
            pass

    release = threading.Event()
    api._power_action_lock = Gate()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(api.power_on, "A.local", "shared_relay", context)
        try:
            assert reached_lock.wait(2)
            connect("B.local")
        finally:
            release.set()
        assert future.result(timeout=2)["ok"] is False
    assert posts == []


def test_late_authority_read_cannot_replace_b_or_new_a(rig):
    api, connect, posts, _ = rig
    old_context, session = connect("A.local", "old_relay")
    started, release = threading.Event(), threading.Event()

    def block():
        started.set()
        assert release.wait(3)

    session.read_hook = block
    with ThreadPoolExecutor() as pool:
        future = pool.submit(api.get_remote_power_config, old_context)
        try:
            assert started.wait(2)
            assert api.power_on("A.local", "old_relay", old_context)["ok"] is False
            connect("B.local")
            current, _ = connect("A.local", "new_relay")
        finally:
            release.set()
        assert future.result(timeout=2)["status"] == "error"
    assert api._remote_power_authority["power_context"] == current
    assert api._remote_power_authority["config"]["device"] == "new_relay"
    assert posts == []


def test_reconnect_blocks_commands_while_new_authority_is_loading(rig):
    api, connect, posts, _ = rig
    old_context, _ = connect("A.local")
    started, release = threading.Event(), threading.Event()

    def block():
        started.set()
        assert release.wait(3)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(connect, "A.local", "new_relay", read_hook=block)
        try:
            assert started.wait(2)
            assert api._remote_power_authority is None
            assert api.power_on("A.local", "shared_relay", old_context)["ok"] is False
            assert api.power_on("A.local", "shared_relay", api._power_context)["ok"] is False
        finally:
            release.set()
        context, _ = future.result(timeout=2)
    assert posts == []
    assert api.power_on("A.local", "shared_relay", context)["ok"] is True


def test_stale_selection_login_and_disconnect_cannot_affect_current_session(rig):
    api, connect, posts, _ = rig
    old, _ = connect("A.local")
    current, session = connect("B.local")
    assert api.select_power_target("A.local", old["selection"]) is False
    assert api.connect_ssh("A.local", "kace", "simulated", power_selection=old["selection"])["status"] == "failed"
    assert api.disconnect_ssh(old["selection"]) is False
    assert api._power_context == current
    assert session.is_connected()
    assert posts == []


@pytest.mark.parametrize("bad_context", [None, {}, {"host": "a.local"}, {"host": "a.local", "selection": True, "session": True}])
def test_missing_or_ambiguous_context_never_sends_commands(rig, bad_context):
    api, connect, posts, _ = rig
    connect("A.local")
    assert api.power_on("A.local", "shared_relay", bad_context)["ok"] is False
    assert posts == []


def test_missing_host_or_replaced_session_object_never_sends_commands(rig):
    api, connect, posts, _ = rig
    context, _ = connect("A.local")
    assert api.power_on(None, "shared_relay", context)["ok"] is False
    replacement = FakeSession()
    replacement.connect()
    api._ssh = replacement
    assert api.power_on("A.local", "shared_relay", context)["ok"] is False
    assert posts == []


def test_refresh_responses_out_of_order_in_same_session_keep_newest_authority(rig):
    api, connect, posts, _ = rig
    context, session = connect("A.local")
    started, release = threading.Event(), threading.Event()
    original = session.read_text_file_result

    def blocked_read(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        started.set()
        assert release.wait(3)
        return snapshot

    session.read_text_file_result = blocked_read
    with ThreadPoolExecutor() as pool:
        future = pool.submit(api.get_remote_power_config, context)
        try:
            assert started.wait(2)
            session.read_text_file_result = original
            session.config["device"] = "new_relay"
            assert api.get_remote_power_config(context)["config"]["device"] == "new_relay"
        finally:
            release.set()
        assert future.result(timeout=2)["status"] == "error"
    assert api._remote_power_authority["config"]["device"] == "new_relay"
    assert posts == []


def test_bootstrap_and_disconnect_notifications_carry_originating_session(rig):
    api, connect, posts, _ = rig
    context, session = connect("A.local")
    scripts = []

    class Window:
        evaluate_js = staticmethod(scripts.append)

    api._window = Window()
    event = {"workflow_id": "A", "event": "workflow_succeeded"}
    api._forward_bootstrap_event(event, context["session"], context)
    assert scripts[-1] == f"window.updateBootstrapEvent({json.dumps(event)}, {json.dumps(context)});"
    session.on_close()
    assert scripts[-1] == f"window.invalidatePowerSession({json.dumps(context)})"
    assert api._remote_power_authority is None
    assert posts == []
