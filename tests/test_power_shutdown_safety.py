"""Normal OFF review and independent logical emergency stop, without hardware."""
import pytest

import main
from backend.moonraker_client import MoonrakerHttpError
from tests.test_power_controller import bind_power_session


class Http:
    def __init__(self):
        self.state = "standby"
        self.locked = True
        self.temperature = 42.0
        self.target = 0
        self.power = "on"
        self.posts = []
        self.after_post_error = False

    def get(self, path):
        if path == "/machine/device_power/devices":
            return {"result": {"devices": [{"device": "main_psu", "status": self.power,
                                            "locked_while_printing": self.locked}]}}
        if "print_stats" in path:
            return {"result": {"status": {"print_stats": {"state": self.state},
                "heaters": {"available_heaters": ["extruder"]},
                "extruder": {"temperature": self.temperature, "target": self.target}}}}
        return {"result": {"status": {"extruder": {"temperature": self.temperature, "target": self.target}}}}

    def post(self, path, payload):
        self.posts.append((path, payload))
        if self.after_post_error:
            raise MoonrakerHttpError("deadline exceeded; outcome may be unknown")
        if path == "/machine/device_power/device":
            self.power = payload["action"]
        return {"result": {}}


@pytest.fixture
def rig(monkeypatch):
    http = Http()
    monkeypatch.setattr("backend.power_controller.MoonrakerHttpClient", lambda *_: http)
    api = main.Api()
    context = bind_power_session(api)
    return api, context, http


@pytest.mark.parametrize("state", ["printing", "paused", "error", "unknown", None])
def test_normal_off_rejects_active_or_unknown_job(rig, state):
    api, context, http = rig
    http.state = state
    result = api.inspect_power_off(context["host"], "main_psu", context)
    assert result["ok"] is False
    assert http.posts == []


@pytest.mark.parametrize("field,value", [("locked", False), ("target", 200),
                                         ("temperature", None), ("temperature", float("nan"))])
def test_normal_off_rejects_unverified_protection_or_heaters(rig, field, value):
    api, context, http = rig
    setattr(http, field, value)
    assert api.inspect_power_off(context["host"], "main_psu", context)["ok"] is False
    assert http.posts == []


def test_missing_confirmation_never_sends_off(rig):
    api, context, http = rig
    assert api.power_off(context["host"], "main_psu", context)["ok"] is False
    assert http.posts == []


def test_review_is_read_only_single_use_and_permits_observed_cooling(rig):
    api, context, http = rig
    review = api.inspect_power_off(context["host"], "main_psu", context)
    assert review["ok"] and review["review"]["heaters"]["extruder"]["temperature"] == 42
    assert http.posts == []
    http.temperature = 40
    result = api.power_off(context["host"], "main_psu", context, review["approval_token"])
    assert result["status"] == "off"
    assert len(http.posts) == 1
    assert api.power_off(context["host"], "main_psu", context, review["approval_token"])["ok"] is False
    assert len(http.posts) == 1


@pytest.mark.parametrize("change", ["warming", "printing", "paused", "protection", "expired", "session", "target", "network"])
def test_changed_review_cannot_authorize_off(rig, change):
    api, context, http = rig
    review = api.inspect_power_off(context["host"], "main_psu", context)
    if change == "warming": http.temperature = 43
    if change == "printing": http.state = "printing"
    if change == "paused": http.state = "paused"
    if change == "protection": http.locked = False
    if change == "expired": api._power_off_review["expires"] = 0
    if change == "session": api._ssh_gen += 1
    if change == "target": http.target = 180
    if change == "network":
        def lost(_path):
            raise MoonrakerHttpError("synthetic connection loss")
        http.get = lost
    assert api.power_off(context["host"], "main_psu", context, review["approval_token"])["ok"] is False
    assert http.posts == []


def test_timeout_after_send_never_reuses_approval_or_retries(rig):
    api, context, http = rig
    review = api.inspect_power_off(context["host"], "main_psu", context)
    http.after_post_error = True
    assert api.power_off(context["host"], "main_psu", context, review["approval_token"])["ok"] is False
    assert api.power_off(context["host"], "main_psu", context, review["approval_token"])["ok"] is False
    assert len(http.posts) == 1


def test_logical_emergency_bypasses_normal_state_guard_and_action_lock(rig):
    api, context, http = rig
    http.state, http.temperature, http.locked = "printing", None, False
    with api._power_action_lock:
        result = api.emergency_stop(context["host"], context)
    assert result["ok"] is True
    assert result["status"] == "requested"
    assert http.posts == [("/printer/emergency_stop", {})]
    assert http.power == "on", "logical emergency is not a relay cutoff"


def test_emergency_rejects_stale_session_and_never_retries_timeout(rig):
    api, context, http = rig
    stale = dict(context, session=context["session"] - 1)
    assert api.emergency_stop(context["host"], stale)["ok"] is False
    assert http.posts == []
    http.after_post_error = True
    assert api.emergency_stop(context["host"], context)["ok"] is False
    assert len(http.posts) == 1


@pytest.mark.parametrize("change", ["paused", "inventory", "expired"])
def test_last_heater_observation_cannot_hide_changed_state_or_expired_review(rig, monkeypatch, change):
    api, context, http = rig
    review = api.inspect_power_off(context["host"], "main_psu", context)
    original = http.get
    def changing(path):
        response = original(path)
        if "extruder=" in path:
            if change == "paused":
                response["result"]["status"]["print_stats"]["state"] = "paused"
            elif change == "inventory":
                response["result"]["status"]["heaters"]["available_heaters"].append("heater_bed")
            else:
                monkeypatch.setattr("backend.power_controller.time.monotonic", lambda: float("inf"))
        return response
    http.get = changing
    assert api.power_off(context["host"], "main_psu", context, review["approval_token"])["ok"] is False
    assert http.posts == []
