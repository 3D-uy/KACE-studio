"""Moonraker-only printer power control for KACE Studio.

This controller is deliberately independent of SSH and the KACE installation:
it must remain usable before bootstrap completes or after bootstrap fails.
"""

import re
import time
import math
from urllib.parse import urlencode

from backend.moonraker_client import MoonrakerHttpClient, MoonrakerHttpError


VALID_STATES = frozenset(("on", "off", "init", "error"))
_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class PowerControllerError(RuntimeError):
    """Raised when Moonraker cannot provide a verified power result."""

    def __init__(self, message, code="powerActionFailed"):
        super().__init__(message)
        self.code = code


class MoonrakerPowerController:
    """Single Studio API for one configured Moonraker power device."""

    def __init__(
        self,
        host: str,
        device: str,
        *,
        http_client=None,
        poll_interval: float = 0.5,
        authorize=None,
    ):
        if not isinstance(device, str) or not _DEVICE_RE.fullmatch(device):
            raise ValueError("POWER_DEVICE is missing or invalid")
        self.device = device
        self._http = http_client or MoonrakerHttpClient(host)
        self.poll_interval = float(poll_interval)
        self._authorize = authorize

    def _device_info(self) -> dict:
        if self._authorize is not None:
            self._authorize()
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
                return item
        raise PowerControllerError(
            f"POWER_DEVICE '{self.device}' is not configured in Moonraker"
        )

    def get_status(self) -> str:
        """Return the real Moonraker state: on, off, init, or error."""
        status = str(self._device_info().get("status", "error")).lower()
        return status if status in VALID_STATES else "error"

    def _objects(self, query):
        if self._authorize is not None:
            self._authorize()
        try:
            body = self._http.get("/printer/objects/query?" + query)
        except MoonrakerHttpError as exc:
            raise PowerControllerError(str(exc), "powerOffUnknown") from exc
        result = body.get("result")
        status = result.get("status") if isinstance(result, dict) else None
        if not isinstance(status, dict):
            raise PowerControllerError("Printer state is unavailable", "powerOffUnknown")
        return status

    def inspect_power_off(self):
        """Observe a reviewable normal OFF; never infer a safe temperature."""
        device = self._device_info()
        if device.get("locked_while_printing") is not True:
            raise PowerControllerError("Moonraker printing protection is not enabled", "powerOffProtection")
        if device.get("status") != "on":
            raise PowerControllerError("Power is not confirmed on", "powerOffChanged")
        status = self._objects("print_stats&heaters")
        stats, heaters = status.get("print_stats"), status.get("heaters")
        state = stats.get("state") if isinstance(stats, dict) else None
        if state not in ("standby", "complete", "cancelled"):
            raise PowerControllerError("Printing, paused or unknown state blocks normal OFF", "powerOffJob")
        names = heaters.get("available_heaters") if isinstance(heaters, dict) else None
        if (not isinstance(names, list) or not 1 <= len(names) <= 64
                or any(not isinstance(name, str) or not name.strip() or len(name) > 128
                       or any(ord(char) < 32 for char in name) for name in names)
                or len(set(names)) != len(names)):
            raise PowerControllerError("Heater inventory is unavailable", "powerOffUnknown")
        values = self._objects("print_stats&heaters&" + urlencode({name: "temperature,target" for name in names}))
        current_stats, current_heaters = values.get("print_stats"), values.get("heaters")
        if (not isinstance(current_stats, dict) or current_stats.get("state") != state
                or not isinstance(current_heaters, dict) or current_heaters.get("available_heaters") != names):
            raise PowerControllerError("Printer state or heater inventory changed", "powerOffChanged")
        observations = {}
        for name in names:
            item = values.get(name)
            if (not isinstance(item, dict) or any(type(item.get(key)) not in (int, float)
                    or not math.isfinite(item[key]) for key in ("temperature", "target"))):
                raise PowerControllerError("Heater readings are unavailable", "powerOffUnknown")
            if item["target"] != 0:
                raise PowerControllerError("An active heater target blocks normal OFF", "powerOffHeating")
            observations[name] = {"temperature": item["temperature"], "target": item["target"]}
        return {"state": state, "heaters": observations}

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

    def _set_and_confirm(self, action: str, timeout: float, before_send=None) -> str:
        self.wait_until_ready(timeout=timeout)
        if before_send is not None:
            before_send()
        if self._authorize is not None:
            self._authorize()
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

    def power_off(self, timeout: float = 30.0, *, approved_review=None, approved_until=None) -> str:
        if not isinstance(approved_review, dict):
            raise PowerControllerError("Normal OFF requires a fresh confirmed review", "powerOffConfirmation")
        def revalidate():
            current = self.inspect_power_off()
            if approved_until is not None and time.monotonic() >= approved_until:
                raise PowerControllerError("Normal OFF confirmation expired", "powerOffConfirmation")
            previous = approved_review.get("heaters", {})
            if (current["state"] != approved_review.get("state")
                    or current["heaters"].keys() != previous.keys()
                    or any(current["heaters"][name]["temperature"] > previous[name]["temperature"]
                           for name in current["heaters"])):
                raise PowerControllerError("Printer conditions changed; review normal OFF again", "powerOffChanged")
        return self._set_and_confirm("off", timeout, before_send=revalidate)

    def emergency_stop(self):
        """Request Klipper's logical emergency stop, never a relay power cutoff."""
        if self._authorize is not None:
            self._authorize()
        try:
            self._http.post("/printer/emergency_stop", {})
        except MoonrakerHttpError as exc:
            raise PowerControllerError(str(exc), "emergencyUnknown") from exc
