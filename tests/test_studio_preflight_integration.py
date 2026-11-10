"""Opt-in Pi client -> forwarded-channel equivalent -> real PC Docker loader.

Only the SSH transport is simulated with loopback sockets. No printer, firmware,
service activation or physical hardware is contacted. The ordinary unit suite
does not build images: explicitly provision a receipt before opting in.
"""
import importlib.util
import os
from pathlib import Path
import socket
import threading

import pytest

from backend.klipper_preflight import DockerPreflight
from backend.studio_preflight_bridge import StudioPreflightBridge, tree_digest

pytestmark = pytest.mark.skipif(os.environ.get("KACE_STUDIO_PREFLIGHT_INTEGRATION") != "1",
                                reason="requires PC Docker image and sibling KACE client")

BASE = b"""[mcu]
serial: /dev/null
[printer]
kinematics: none
max_velocity: 100
max_accel: 1000
[controller_fan audit]
pin: PA0
stepper:
"""


def configuration(source, heater):
    override = f"[controller_fan audit]\nheater: {heater}\n".encode()
    if source == "include":
        return {"printer.cfg": BASE + b"[include user.cfg]\n", "user.cfg": b"[include nested.cfg]\n",
                "nested.cfg": override}
    if source == "save":
        return {"printer.cfg": BASE + (
            "\n#*# <---------------------- SAVE_CONFIG ---------------------->\n"
            "#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n#*#\n"
            f"#*# [controller_fan audit]\n#*# heater = {heater}\n").encode()}
    return {"printer.cfg": BASE + b"\n" + override}


class ForwardedSocketTransport:
    """Test equivalent of server-side loopback forwarding, not an SSH server."""
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.active = True
        self.thread = None

    def is_active(self):
        return self.active

    def request_port_forward(self, host, port, handler):
        assert host == "127.0.0.1" and port == 0
        allocated = self.listener.getsockname()[1]
        def accept():
            connection, origin = self.listener.accept()
            handler(connection, origin, (host, allocated))
        self.thread = threading.Thread(target=accept, daemon=True)
        self.thread.start()
        return allocated

    def cancel_port_forward(self, host, port):
        self.close()

    def close(self):
        self.active = False
        self.listener.close()


@pytest.mark.parametrize("source", ["root", "include", "save"])
@pytest.mark.parametrize("heater,expected", [("", "valid"), ("Missing", "invalid")])
def test_real_pc_loader_through_pi_protocol(source, heater, expected, monkeypatch):
    client_path = Path(__file__).resolve().parents[2] / "KACE/core/studio_preflight.py"
    assert client_path.is_file(), "Opt-in integration requires sibling KACE checkout"
    spec = importlib.util.spec_from_file_location("_tested_pi_preflight_client", client_path)
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    receipt = os.environ.get("KACE_STUDIO_PREFLIGHT_RECEIPT")
    assert receipt, "Point KACE_STUDIO_PREFLIGHT_RECEIPT to an explicitly provisioned PC receipt"
    backend = DockerPreflight(receipt_path=Path(receipt))
    # No prepare() here: opt-in tests must not silently build an image.
    transport = ForwardedSocketTransport()
    bridge = StudioPreflightBridge(transport, backend)
    try:
        for name, value in bridge.start().items():
            monkeypatch.setenv(name, value)
        files = configuration(source, heater)
        original = dict(files)
        result = client.validate_files(files, backend.identity, tree_digest(files))
        assert result["status"] == expected, result
        assert result["tree_digest"] == tree_digest(files)
        assert files == original
        if expected == "invalid":
            assert "Unknown heater" in result["detail"]
    finally:
        bridge.close()
        transport.close()
        if transport.thread:
            transport.thread.join(timeout=2)
