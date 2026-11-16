"""Bounded real loopback HTTP; never connects to Moonraker or printer hardware."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend import moonraker_client as http


@pytest.fixture
def endpoint():
    mode = {"body": b'{"result":{}}', "status": 200, "length": True, "drip": False,
            "headers_drip": False}
    stopped = threading.Event()
    started_response = threading.Event()
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            started_response.set()
            try:
                if mode["headers_drip"]:
                    for byte in b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n":
                        self.connection.sendall(bytes([byte]))
                        if stopped.wait(0.02):
                            return
                else:
                    self.send_response(mode["status"])
                    if mode["length"]:
                        length = mode["length"] if type(mode["length"]) is int else len(mode["body"])
                        self.send_header("Content-Length", str(length))
                    self.end_headers()
                if mode["drip"]:
                    for byte in mode["body"]:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        if stopped.wait(0.04):
                            return
                else:
                    self.wfile.write(mode["body"])
            except (OSError, ConnectionError):
                pass
        def log_message(self, *_):
            pass
        do_POST = do_GET
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    mode["started"] = started_response
    try:
        yield http.MoonrakerHttpClient("127.0.0.1", port=server.server_port, timeout=.2), mode, seen
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize("length", [True, False, 10000])
def test_large_success_and_error_bodies_are_rejected(endpoint, monkeypatch, status, length):
    client, mode, _ = endpoint
    monkeypatch.setattr(http, "MAX_RESPONSE_BYTES", 128, raising=False)
    mode.update(status=status, length=length, body=json.dumps({"error": {"message": "x" * 200}}).encode())
    with pytest.raises(http.MoonrakerHttpError, match="[Ll]imit|[Ll]arge"):
        client.get("/oversized")


@pytest.mark.parametrize("phase", ["drip", "headers_drip"])
def test_total_deadline_interrupts_continuously_arriving_bytes(endpoint, phase):
    client, mode, _ = endpoint
    mode[phase] = True
    started = time.monotonic()
    with pytest.raises(http.MoonrakerHttpError, match="[Dd]eadline|[Tt]imed out"):
        client.get("/slow")
    assert time.monotonic() - started < .65


def test_exact_body_limit_is_accepted(endpoint, monkeypatch):
    client, mode, _ = endpoint
    monkeypatch.setattr(http, "MAX_RESPONSE_BYTES", len(mode["body"]), raising=False)
    assert client.get("/exact") == {"result": {}}


def test_invalid_json_is_controlled(endpoint):
    client, mode, _ = endpoint
    mode["body"] = b"not JSON"
    with pytest.raises(http.MoonrakerHttpError, match="Invalid Moonraker response"):
        client.get("/invalid")


def test_truncated_response_with_claimed_length_is_rejected(endpoint):
    client, mode, _ = endpoint
    mode["length"] = len(mode["body"]) + 1
    with pytest.raises(http.MoonrakerHttpError, match="Invalid Moonraker response"):
        client.get("/truncated")


def test_post_timeout_never_retries_a_possibly_executed_action(endpoint):
    client, mode, seen = endpoint
    mode["drip"] = True
    with pytest.raises(http.MoonrakerHttpError, match="outcome may be unknown"):
        client.post("/machine/device_power/device", {"device": "synthetic", "action": "off"})
    assert seen == ["/machine/device_power/device"]


def test_cancellation_before_request_does_not_send(endpoint):
    client, _mode, seen = endpoint
    client.cancel_event = threading.Event()
    client.cancel_event.set()
    with pytest.raises(http.MoonrakerHttpError, match="cancelled"):
        client.get("/cancelled")
    assert seen == []


@pytest.mark.parametrize("status", [200, 500])
def test_cancellation_interrupts_body_read_and_closes_request(endpoint, status):
    client, mode, _ = endpoint
    client.timeout = 5
    client.cancel_event = threading.Event()
    mode.update(status=status, drip=True)
    def cancel():
        assert mode["started"].wait(1)
        client.cancel_event.set()
    worker = threading.Thread(target=cancel)
    worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(http.MoonrakerHttpError, match="cancelled"):
            client.get("/cancelled-body")
        assert time.monotonic() - started < .7
    finally:
        worker.join(1)


def test_slow_error_body_obeys_same_total_deadline(endpoint):
    client, mode, _ = endpoint
    mode.update(status=500, drip=True)
    started = time.monotonic()
    with pytest.raises(http.MoonrakerHttpError, match="[Dd]eadline|[Tt]imed out"):
        client.get("/slow-error")
    assert time.monotonic() - started < .65


def test_slow_dns_cannot_send_late_request_and_has_bounded_capacity(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    completed = threading.Event()
    real_getaddrinfo = http.socket.getaddrinfo
    calls = []
    def resolve(*args, **kwargs):
        calls.append(args)
        entered.set()
        try:
            assert release.wait(2)
            return real_getaddrinfo("127.0.0.1", 12345, type=http.socket.SOCK_STREAM)
        finally:
            completed.set()
    monkeypatch.setattr(http.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(http, "_DNS_SLOTS", threading.BoundedSemaphore(1))
    sent = []
    def socket(*_args, **_kwargs):
        sent.append(True)
        raise AssertionError("Late DNS must not open a connection")
    monkeypatch.setattr(http.socket, "socket", socket)
    try:
        with pytest.raises(http.MoonrakerHttpError, match="deadline"):
            http.MoonrakerHttpClient("synthetic.invalid", timeout=.05).get("/no-late-request")
        assert entered.is_set()
        second = http._RequestBudget(.1)
        try:
            with pytest.raises(OSError, match="capacity"):
                http._resolve("another.invalid", 7125, second)
        finally:
            second.close()
        assert len(calls) == 1
    finally:
        release.set()
        assert completed.wait(1)
    assert sent == []


def test_https_proxy_cannot_bypass_deadline_transport(endpoint, monkeypatch):
    client, _mode, seen = endpoint
    monkeypatch.setattr(http.urllib.request, "getproxies", lambda: {"http": "https://127.0.0.1:9"})
    monkeypatch.setattr(http.urllib.request, "proxy_bypass", lambda host: False)
    connections = []
    def forbidden(*args, **kwargs):
        connections.append(args)
        raise AssertionError("Unsupported proxy must not open any socket")
    monkeypatch.setattr(http.socket, "create_connection", forbidden)
    with pytest.raises(http.MoonrakerHttpError, match="HTTPS proxy transport is not supported"):
        client.get("/unsupported-proxy")
    assert seen == connections == []


def test_opener_construction_failure_stops_budget(monkeypatch):
    budgets = []
    original = http._RequestBudget
    def capture(*args):
        budget = original(*args)
        budgets.append(budget)
        return budget
    def broken(*args):
        raise OSError("simulated opener failure")
    monkeypatch.setattr(http, "_RequestBudget", capture)
    monkeypatch.setattr(http.urllib.request, "build_opener", broken)
    with pytest.raises(http.MoonrakerHttpError, match="simulated opener failure"):
        http.MoonrakerHttpClient("127.0.0.1").get("/unused")
    assert len(budgets) == 1
    budgets[0].worker.join(.2)
    assert budgets[0].done.is_set() and not budgets[0].worker.is_alive()
