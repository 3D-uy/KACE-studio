"""Moonraker-only printer power control for KACE Studio.

This controller is deliberately independent of SSH and the KACE installation:
it must remain usable before bootstrap completes or after bootstrap fails.
"""

import re
import time

from backend.moonraker_client import MoonrakerHttpClient, MoonrakerHttpError


VALID_STATES = frozenset(("on", "off", "init", "error"))
_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class PowerControllerError(RuntimeError):
    """Raised when Moonraker cannot provide a verified power result."""


class MoonrakerPowerController:
    """Single Studio API for one configured Moonraker power device."""

    def __init__(
        self,
        host: str,
        device: str,
        *,
        http_client=None,
        poll_interval: float = 0.5,
    ):
        if not isinstance(device, str) or not _DEVICE_RE.fullmatch(device):
            raise ValueError("POWER_DEVICE is missing or invalid")
        self.device = device
        self._http = http_client or MoonrakerHttpClient(host)
        self.poll_interval = float(poll_interval)

    def get_status(self) -> str:
        """Return the real Moonraker state: on, off, init, or error."""
        try:
            body = self._http.get("/machine/device_power/devices")
        except MoonrakerHttpError as exc:
            raise PowerControllerError(str(exc)) from exc
        result = body.get("result")
        devices = result.get("devices", []) if isinstance(result, dict) else None
        if not isinstance(devices, list):
            raise PowerControllerError("Moonraker returned an invalid power device list")
        for item in devices:
            if isinstance(item, dict) and item.get("device") == self.device:
                status = str(item.get("status", "error")).lower()
                return status if status in VALID_STATES else "error"
        raise PowerControllerError(
            f"POWER_DEVICE '{self.device}' is not configured in Moonraker"
        )

    def wait_until_ready(self, timeout: float = 30.0) -> str:
        """Wait until the configured device leaves init; fail on error."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.get_status()
            if status == "error":
                raise PowerControllerError(
                    f"Moonraker power device '{self.device}' entered error state"
                )
            if status != "init":
                return status
            if time.monotonic() >= deadline:
                raise PowerControllerError(
                    f"timed out waiting for power device '{self.device}' to leave init"
                )
            time.sleep(self.poll_interval)

    def _set_and_confirm(self, action: str, timeout: float) -> str:
        self.wait_until_ready(timeout=timeout)
        try:
            self._http.post(
                "/machine/device_power/device",
                {"device": self.device, "action": action},
            )
        except MoonrakerHttpError as exc:
            raise PowerControllerError(str(exc)) from exc
        deadline = time.monotonic() + timeout
        while True:
            status = self.get_status()
            if status == action:
                return status
            if status == "error":
                raise PowerControllerError(
                    f"Moonraker power device '{self.device}' entered error state"
                )
            if time.monotonic() >= deadline:
                raise PowerControllerError(
                    f"power device '{self.device}' did not reach {action}"
                )
            time.sleep(self.poll_interval)

    def power_on(self, timeout: float = 30.0) -> str:
        return self._set_and_confirm("on", timeout)

    def power_off(self, timeout: float = 30.0) -> str:
        return self._set_and_confirm("off", timeout)
