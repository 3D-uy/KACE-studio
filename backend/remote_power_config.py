"""Validation boundary for KACE's remote, non-secret power contract."""

from __future__ import annotations

import json
import re


POWER_SCHEMA = "kace-power/v1"
_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_PIN_RE = re.compile(r"^gpiochip[0-9]+/gpio[0-9]{1,3}$")


class RemotePowerConfigError(ValueError):
    pass


def parse_remote_power_config(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RemotePowerConfigError(f"remote power.json is invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RemotePowerConfigError("remote power.json must be a JSON object")

    # One-cycle compatibility for installations created before Steps 16-17.
    if data.get("schema") == 1:
        if not isinstance(data.get("enabled"), bool):
            raise RemotePowerConfigError("legacy remote enabled must be a boolean")
        enabled = data["enabled"]
        device = data.get("device") if enabled else None
        if enabled and (not isinstance(device, str) or not _DEVICE_RE.fullmatch(device)):
            raise RemotePowerConfigError("legacy remote POWER_DEVICE is missing or invalid")
        return {
            "schema": 1,
            "revision": 0,
            "enabled": enabled,
            "device": device,
            "pin": None,
            "active_low": None,
            "initial_state": None,
            "restart_klipper_when_powered": None,
            "off_when_shutdown": None,
            "legacy": True,
        }

    required = {
        "schema", "revision", "enabled", "device", "pin", "active_low",
        "initial_state", "restart_klipper_when_powered", "off_when_shutdown",
    }
    missing = sorted(required - set(data))
    if missing:
        raise RemotePowerConfigError("remote power.json is missing: " + ", ".join(missing))
    if data.get("schema") != POWER_SCHEMA:
        raise RemotePowerConfigError(f"unsupported remote power schema: {data.get('schema')!r}")
    revision = data.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise RemotePowerConfigError("remote power revision must be a positive integer")
    for key in ("enabled", "active_low", "restart_klipper_when_powered", "off_when_shutdown"):
        if not isinstance(data.get(key), bool):
            raise RemotePowerConfigError(f"remote {key} must be a boolean")
    if data.get("initial_state") not in ("on", "off"):
        raise RemotePowerConfigError("remote initial_state must be 'on' or 'off'")
    enabled = data["enabled"]
    device = data.get("device")
    pin = data.get("pin")
    if enabled:
        if not isinstance(device, str) or not _DEVICE_RE.fullmatch(device):
            raise RemotePowerConfigError("remote POWER_DEVICE is missing or invalid")
        if not isinstance(pin, str) or not _PIN_RE.fullmatch(pin):
            raise RemotePowerConfigError("remote power pin is invalid")
    elif device is not None or pin is not None:
        raise RemotePowerConfigError("disabled remote power configuration names a device or pin")
    return {
        "schema": POWER_SCHEMA,
        "revision": revision,
        "enabled": enabled,
        "device": device,
        "pin": pin,
        "active_low": data["active_low"],
        "initial_state": data["initial_state"],
        "restart_klipper_when_powered": data["restart_klipper_when_powered"],
        "off_when_shutdown": data["off_when_shutdown"],
        "legacy": False,
    }
