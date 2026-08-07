import json
from pathlib import Path

from backend.power_controller import KacePowerClient


ROOT = Path(__file__).resolve().parents[1]


class FakeSSH:
    def __init__(self):
        self.commands = []

    def run_command(self, command):
        self.commands.append(command)
        action = command.rsplit(" ", 1)[-1]
        status = {"status": "on", "on": "on", "off": "off", "wait": "off"}[action]
        payload = {
            "ok": True,
            "available": True,
            "device": "main_psu",
            "status": status,
            "detail": "",
        }
        return {"exit_status": 0, "stdout": json.dumps(payload) + "\n", "stderr": ""}


def test_studio_bridge_exposes_one_power_api_over_the_existing_ssh_session():
    ssh = FakeSSH()
    power = KacePowerClient(ssh)

    assert power.get_status()["device"] == "main_psu"
    assert power.power_on()["status"] == "on"
    assert power.power_off()["status"] == "off"
    assert power.wait_until_ready()["status"] == "off"
    assert [command.rsplit(" ", 1)[-1] for command in ssh.commands] == [
        "status", "on", "off", "wait"
    ]


def test_power_button_uses_real_states_and_backend_controller_only():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    backend = (ROOT / "backend" / "power_controller.py").read_text(encoding="utf-8")

    assert 'id="printer-power-btn"' in html
    assert "['on', 'off', 'init', 'error']" in app
    assert "window.pywebview.api.get_power_status()" in app
    assert "'power_off' : 'power_on'" in app
    assert "/machine/device_power" not in app
    assert "/machine/device_power" not in backend
    assert "gpiochip" not in backend.lower()
    assert "gpiod" not in backend.lower()


def test_power_button_never_falls_back_to_printer_device_name():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    line = next(line for line in app.splitlines() if "const powerDevice =" in line)
    assert "printer" not in line
