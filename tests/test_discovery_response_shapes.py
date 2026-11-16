"""Malformed services on port 7125 must not hide healthy SSH candidates."""
import pytest

from backend import discovery


@pytest.mark.parametrize("result", [None, [], 1, "ready", {}, {"state": None},
                                    {"state": []}, {"state": 1}, {"state": ""}])
def test_invalid_printer_info_is_not_identified_as_klipper(monkeypatch, result):
    monkeypatch.setattr(discovery.MoonrakerHttpClient, "get", lambda *_: {"result": result})
    assert discovery.check_klipper("192.0.2.1") is False


@pytest.mark.parametrize("state", ["ready", "startup", "shutdown", "error"])
def test_valid_klipper_states_remain_discoverable(monkeypatch, state):
    monkeypatch.setattr(discovery.MoonrakerHttpClient, "get", lambda *_: {"result": {"state": state}})
    assert discovery.check_klipper("192.0.2.1") is True


def test_mixed_scan_retains_ssh_host_with_invalid_moonraker_and_healthy_host(monkeypatch):
    monkeypatch.setattr(discovery, "probe_ip_ports", lambda *_: {22: True, 7125: True})
    monkeypatch.setattr(discovery, "_reverse_dns", lambda ip, **_: ip)
    monkeypatch.setattr(discovery.MoonrakerHttpClient, "get", lambda client, _: {
        "result": None if client.host.endswith(".1") else {"state": "ready"}})
    result = discovery.scan_network(["192.0.2.1", "192.0.2.2"])
    assert [(item["ip"], item["ssh"], item["klipper"]) for item in result] == [
        ("192.0.2.1", True, False), ("192.0.2.2", True, True)]
