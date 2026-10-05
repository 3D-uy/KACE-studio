"""HTTP regressions with synthetic loopback endpoints; no printer access."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def endpoints():
    seen = []
    mode = {"code": 302, "relative": False, "direct": False}

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append((self.server.role, self.command, self.path, dict(self.headers), data))
            if self.server.role == "origin" and self.path != "/sink" and not mode["direct"]:
                self.send_response(mode["code"])
                target = "/sink" if mode["relative"] is True else f"http://127.0.0.1:{destination.server_port}/sink"
                if mode["relative"] == "scheme":
                    target = target.replace("http:", "https:", 1)
                self.send_header("Location", target)
                self.end_headers()
            else:
                body = json.dumps({"result": {"devices": [{"device": "main_psu", "status": "on"}]}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        do_GET = do_POST = do_DELETE = handle_request

        def log_message(self, *_args):
            pass

    servers = []
    workers = []
    for role in ("destination", "origin"):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.role = role
        servers.append(server)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        workers.append(worker)
    destination, origin = servers
    try:
        yield f"http://127.0.0.1:{origin.server_port}", mode, seen
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for worker in workers:
            worker.join(2)

from backend.moonraker_client import MoonrakerHttpClient, MoonrakerHttpError
from backend.power_controller import MoonrakerPowerController, PowerControllerError


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("relative", [False, True, "scheme"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_redirect_never_sends_a_second_request(endpoints, code, relative, method):
    base, mode, seen = endpoints
    mode.update(code=code, relative=relative)
    client = MoonrakerHttpClient("127.0.0.1", port=int(base.rsplit(":", 1)[1]))
    with pytest.raises(MoonrakerHttpError, match="[Rr]edirect"):
        client.request_json(method, "/request", {"action": "on"} if method == "POST" else None)
    assert len(seen) == 1
    assert seen[0][0:2] == ("origin", method)


def test_power_cannot_be_confirmed_from_a_redirected_endpoint(endpoints):
    base, _mode, seen = endpoints
    client = MoonrakerHttpClient("127.0.0.1", port=int(base.rsplit(":", 1)[1]))
    controller = MoonrakerPowerController("127.0.0.1", "main_psu", http_client=client)
    with pytest.raises(PowerControllerError, match="[Rr]edirect"):
        controller.power_on(timeout=1)
    assert len(seen) == 1


def test_direct_response_still_confirms_power(endpoints):
    base, mode, seen = endpoints
    mode["direct"] = True
    client = MoonrakerHttpClient("127.0.0.1", port=int(base.rsplit(":", 1)[1]))
    controller = MoonrakerPowerController("127.0.0.1", "main_psu", http_client=client)
    assert controller.power_on(timeout=1) == "on"
    assert all(item[0] == "origin" for item in seen)
