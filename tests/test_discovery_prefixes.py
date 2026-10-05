"""Subnet discovery uses interface prefixes and never expands unbounded networks."""
import pytest
from backend import discovery


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.setattr(discovery.socket, "socket", lambda *_a, **_k: (_ for _ in ()).throw(OSError("No host network in tests")))
    monkeypatch.setattr(discovery.socket, "getaddrinfo", lambda *_a, **_k: [])


@pytest.mark.parametrize("prefix, included, excluded", [(23,"10.20.9.2", "10.20.10.1"), (25,"10.20.8.100","10.20.8.200"), (30,"10.20.8.9","10.20.8.11")])
def test_uses_actual_prefix_and_excludes_local_address(monkeypatch, prefix, included, excluded):
    monkeypatch.setattr(discovery, "get_ipv4_interfaces", lambda: [{"IPAddress":"10.20.8.10", "PrefixLength":prefix}], raising=False)
    addresses = discovery.get_local_subnet_ips()
    assert included in addresses
    assert excluded not in addresses
    assert "10.20.8.10" not in addresses


@pytest.mark.parametrize("interfaces", [
    [{"IPAddress":"10.20.8.10","PrefixLength":16}],
    [{"IPAddress":"10.20.8.10","PrefixLength":24},{"IPAddress":"192.168.1.2","PrefixLength":24}],
    [{"IPAddress":"10.20.8.10","PrefixLength":99}],
])
def test_oversize_ambiguous_or_invalid_interfaces_do_not_start_probes(monkeypatch, interfaces):
    monkeypatch.setattr(discovery, "get_ipv4_interfaces", lambda: interfaces, raising=False)
    probes = []
    monkeypatch.setattr(discovery, "probe_ip_ports", lambda *_a, **_k: probes.append(True))
    with pytest.raises(RuntimeError):
        discovery.scan_network()
    assert probes == []


@pytest.mark.parametrize("output, expected", [
    ('{"IPAddress":"10.20.8.10","PrefixLength":25}', [{"IPAddress":"10.20.8.10","PrefixLength":25}]),
    ('[]', []), ('null', []), ('"invalid"', []), ('invalid JSON', []),
])
def test_windows_interface_provider_handles_single_and_empty_json(monkeypatch, output, expected):
    from types import SimpleNamespace
    monkeypatch.setattr(discovery, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(discovery.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=output)
    monkeypatch.setattr(discovery.subprocess, "run", run)
    assert discovery.get_ipv4_interfaces() == expected
    command, options = calls[0]
    assert command[:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
    assert "Get-NetIPAddress" in command[-1]
    assert options["timeout"] == 5
    assert options["check"] is True
