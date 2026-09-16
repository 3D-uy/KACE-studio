"""Enrollment cannot escape the authenticated printer/session context."""

from unittest.mock import Mock

import main

from tests.test_power_session_identity import rig
from tests.test_moonraker_authorization import remote, remote_file


def test_enrollment_is_explicit_and_never_reused_between_sessions(rig, monkeypatch):
    api, connect, _, _ = rig
    operation = Mock(return_value={"ok": True, "ip": "192.168.1.23", "changed": True})
    monkeypatch.setattr(main, "moonraker_client_access", operation)
    assert not api.inspect_moonraker_client()["ok"]
    assert not api.authorize_moonraker_client("192.168.1.23")["ok"]
    a, session_a = connect("A.local")
    operation.assert_not_called()  # SSH login alone is never approval.
    inspected = api.inspect_moonraker_client(a)
    assert inspected["power_context"] == a
    operation.assert_called_once_with(session_a, None)
    assert not api.authorize_moonraker_client(None, a)["ok"]
    assert api.authorize_moonraker_client(inspected["ip"], a)["ok"]
    operation.assert_called_with(session_a, "192.168.1.23")
    b, session_b = connect("B.local")
    operation.reset_mock()
    assert not api.authorize_moonraker_client("192.168.1.23", a)["ok"]
    operation.assert_not_called()
    assert api.authorize_moonraker_client("192.168.1.23", b)["ok"]
    operation.assert_called_once_with(session_b, "192.168.1.23")
    api.disconnect_ssh()
    operation.reset_mock()
    assert not api.authorize_moonraker_client("192.168.1.23", b)["ok"]
    operation.assert_not_called()


def test_late_enrollment_response_is_rejected_after_target_change(rig, monkeypatch):
    api, connect, _, _ = rig
    a, session_a = connect("A.local")
    def pending(session, approved_ip):
        assert session is session_a
        assert api.select_power_target("B.local", a["selection"] + 1)
        return {"ok": True, "ip": "192.168.1.23", "changed": True}
    monkeypatch.setattr(main, "moonraker_client_access", pending)
    assert not api.authorize_moonraker_client("192.168.1.23", a)["ok"]
    assert api._remote_power_authority is None


def test_power_http_is_available_only_after_explicit_enrollment(rig, remote_file, monkeypatch):
    import configparser
    import ipaddress
    from backend import power_controller
    from backend.moonraker_client import MoonrakerHttpError

    api, connect, posts, _ = rig
    program, path, restart = remote_file
    original_http = power_controller.MoonrakerHttpClient
    address = "192.168.1.23"

    def trusted():
        parser = configparser.ConfigParser()
        parser.read_string(path.read_bytes().decode())
        return any(ipaddress.ip_address(address) in ipaddress.ip_network(value)
                   for value in parser["authorization"]["trusted_clients"].split())

    class ProtectedHttp(original_http):
        def get(self, route):
            if not trusted():
                raise MoonrakerHttpError("Moonraker HTTP 401: Unauthorized")
            return super().get(route)

        def post(self, route, payload):
            assert trusted()
            return super().post(route, payload)

    monkeypatch.setattr(power_controller, "MoonrakerHttpClient", ProtectedHttp)
    context, session = connect("A.local", active_low=True)
    assert not api.power_on("A.local", "shared_relay", context)["ok"]
    assert posts == []

    def enroll(current, approved):
        assert current is session and approved == address
        return {"ok": True, "ip": address, "changed": program.authorize(path, address)}

    monkeypatch.setattr(main, "moonraker_client_access", enroll)
    assert api.authorize_moonraker_client(address, context)["ok"]
    assert api.power_on("A.local", "shared_relay", context)["status"] == "on"
    assert posts[0][2] == {"device": "shared_relay", "action": "on"}
    assert api._remote_power_authority["config"]["active_low"] is True
    restart.assert_called_once()
