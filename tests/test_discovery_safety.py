import socket
import time

from backend.discovery import _reverse_dns, get_local_subnet_ips


def test_reverse_dns_returns_at_requested_deadline(monkeypatch):
    def slow_lookup(_ip):
        time.sleep(0.3)
        return ("late.example", [], [])

    monkeypatch.setattr(socket, "gethostbyaddr", slow_lookup)

    started = time.monotonic()
    result = _reverse_dns("192.0.2.1", timeout=0.01, default="fallback.local")
    elapsed = time.monotonic() - started

    assert result == "fallback.local"
    assert elapsed < 0.15


def test_no_detected_interface_does_not_scan_arbitrary_subnet(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(socket, "socket", unavailable)
    monkeypatch.setattr(socket, "getaddrinfo", unavailable)

    assert get_local_subnet_ips() == []
