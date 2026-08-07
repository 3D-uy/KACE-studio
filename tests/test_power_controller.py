from pathlib import Path
import json

import pytest

from backend.power_controller import MoonrakerPowerController, PowerControllerError
from backend.moonraker_client import MoonrakerHttpClient


ROOT = Path(__file__).resolve().parents[1]


class FakeMoonrakerHttp:
    def __init__(self, states):
        self.states = list(states)
        self.posts = []

    def get(self, path):
        assert path == "/machine/device_power/devices"
        status = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {
            "result": {
                "devices": [
                    {"device": "lights", "status": "off"},
                    {"device": "main_psu", "status": status},
                ]
            }
        }

    def post(self, path, payload):
        self.posts.append((path, payload))
        return {"result": {"device": payload["device"], "status": payload["action"]}}


def test_controller_reads_the_configured_device_directly_from_moonraker():
    http = FakeMoonrakerHttp(["on"])
    power = MoonrakerPowerController("192.168.1.20", "main_psu", http_client=http)

    assert power.get_status() == "on"


def test_power_on_uses_power_api_and_confirms_final_state():
    http = FakeMoonrakerHttp(["off", "on"])
    power = MoonrakerPowerController(
        "192.168.1.20", "main_psu", http_client=http, poll_interval=0
    )

    assert power.power_on(timeout=1) == "on"
    assert http.posts == [
        (
            "/machine/device_power/device",
            {"device": "main_psu", "action": "on"},
        )
    ]


def test_wait_until_ready_fails_on_real_error_state():
    power = MoonrakerPowerController(
        "192.168.1.20", "main_psu", http_client=FakeMoonrakerHttp(["error"])
    )
    with pytest.raises(PowerControllerError, match="entered error state"):
        power.wait_until_ready(timeout=1)


def test_http_client_uses_selected_host_and_moonraker_port(monkeypatch):
    observed = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"result":{"devices":[]}}'

    def fake_urlopen(request, timeout):
        observed.update(
            url=request.full_url,
            method=request.get_method(),
            payload=json.loads(request.data.decode("utf-8")),
            timeout=timeout,
        )
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = MoonrakerHttpClient("192.168.1.20:2222", timeout=2)
    client.post(
        "/machine/device_power/device",
        {"device": "main_psu", "action": "off"},
    )

    assert observed == {
        "url": "http://192.168.1.20:7125/machine/device_power/device",
        "method": "POST",
        "payload": {"device": "main_psu", "action": "off"},
        "timeout": 2.0,
    }


def test_pywebview_api_power_action_does_not_require_ssh(monkeypatch):
    import main

    calls = []

    class FakeController:
        def __init__(self, host, device):
            calls.append(("init", host, device))
            self.device = device

        def power_on(self):
            calls.append(("on", self.device))
            return "on"

    monkeypatch.setattr(main, "MoonrakerPowerController", FakeController)
    api = main.Api()

    result = api.power_on("192.168.1.20", "main_psu")

    assert api._ssh.client is None
    assert result == {
        "ok": True,
        "available": True,
        "device": "main_psu",
        "status": "on",
        "detail": "",
    }
    assert calls == [("init", "192.168.1.20", "main_psu"), ("on", "main_psu")]


def test_end_to_end_status_then_power_on_without_ssh_or_kace(monkeypatch):
    import main

    get_states = iter(("off", "off", "on"))
    requests = []

    class Response:
        def __init__(self, body):
            self._body = json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    def fake_urlopen(request, timeout):
        method = request.get_method()
        payload = json.loads(request.data.decode("utf-8")) if request.data else None
        requests.append((method, request.full_url, payload, timeout))
        if method == "POST":
            return Response({"result": {"device": "main_psu", "status": "on"}})
        return Response(
            {
                "result": {
                    "devices": [{"device": "main_psu", "status": next(get_states)}]
                }
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    api = main.Api()

    initial = api.get_power_status("192.168.1.20", "main_psu")
    powered = api.power_on("192.168.1.20", "main_psu")

    assert api._ssh.client is None
    assert initial["status"] == "off"
    assert powered == {
        "ok": True,
        "available": True,
        "device": "main_psu",
        "status": "on",
        "detail": "",
    }
    assert [method for method, *_rest in requests] == ["GET", "GET", "POST", "GET"]
    assert requests[2][2] == {"device": "main_psu", "action": "on"}


def test_studio_power_controller_has_no_kace_or_ssh_bootstrap_dependency():
    backend = (ROOT / "backend" / "power_controller.py").read_text(encoding="utf-8")
    assert "~/kace" not in backend
    assert "run_command" not in backend
    assert "paramiko" not in backend
    assert "gpiochip" not in backend.lower()
    assert "gpiod" not in backend.lower()
    assert "/machine/device_power/devices" in backend
    assert "/machine/device_power/device" in backend


def test_power_button_works_for_selected_host_without_ssh_connection():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="printer-power-btn"' in html
    assert "['on', 'off', 'init', 'error']" in app
    assert "get_power_status(currentDeviceIp, powerDevice)" in app
    assert "api[action](currentDeviceIp, powerDevice)" in app

    refresh = app.split("async function refreshPrinterPower", 1)[1].split(
        "function startPowerPolling", 1
    )[0]
    toggle = app.split("async function togglePrinterPower", 1)[1].split(
        "function disconnectSSH", 1
    )[0]
    connect = app.split("function connectToDevice", 1)[1].split(
        "function initTerminal", 1
    )[0]
    connection_state = app.split("function updateConnectionStatus", 1)[1].split(
        "function renderPrinterPower", 1
    )[0]

    assert "sshConnected" not in refresh
    assert "sshConnected" not in toggle
    assert "startPowerPolling()" in connect
    assert "stopPowerPolling()" not in connection_state


def test_power_button_never_falls_back_to_printer_device_name():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    line = next(line for line in app.splitlines() if "const powerDevice =" in line)
    assert "printer" not in line
