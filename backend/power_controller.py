"""Thin Studio bridge to KACE's MoonrakerPowerController.

Studio never talks to GPIO and does not duplicate the firmware workflow. Each
operation is delegated over the already-authenticated SSH connection to KACE's
single Power API implementation on the Pi.
"""

import json
import threading


class KacePowerClient:
    _COMMANDS = frozenset(("status", "on", "off", "wait"))
    _KACE_COMMAND = "~/kace/venv/bin/python ~/kace/kace.py --power {}"

    def __init__(self, ssh_session):
        self._ssh = ssh_session
        self._lock = threading.Lock()

    def _run(self, action: str) -> dict:
        if action not in self._COMMANDS:
            raise ValueError("invalid power action")
        with self._lock:
            result = self._ssh.run_command(self._KACE_COMMAND.format(action))
        stdout = result.get("stdout", "")
        payload = None
        for line in reversed(stdout.splitlines()):
            try:
                candidate = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break
        if payload is None:
            detail = result.get("stderr", "").strip() or "KACE power command returned no JSON"
            return {
                "ok": False,
                "available": False,
                "device": None,
                "status": "error",
                "detail": detail,
            }
        return payload

    def get_status(self) -> dict:
        return self._run("status")

    def power_on(self) -> dict:
        return self._run("on")

    def power_off(self) -> dict:
        return self._run("off")

    def wait_until_ready(self) -> dict:
        return self._run("wait")
