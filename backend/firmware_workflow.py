"""Read-only validation/projection of KACE's durable firmware checkpoint."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping, Optional


SCHEMA = "kace-firmware-workflow/v1"
ALLOWED_STATES = {
    "HARDWARE_SELECTED",
    "COMPILE_REQUIRED",
    "ARTIFACT_READY",
    "AWAITING_FLASH",
    "VERIFYING_MCU",
    "MCU_VERIFIED",
    "CONFIG_GENERATED",
    "READY_TO_DEPLOY",
    "DEPLOYING",
    "COMPLETE",
}
_SECRET_KEYS = {
    "password",
    "ssh_password",
    "wifi_password",
    "api_key",
    "moonraker_api_key",
}


def _contains_secret(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in _SECRET_KEYS or _contains_secret(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def _canonical_payload(value: Mapping[str, object]) -> bytes:
    payload = dict(value)
    payload.pop("integrity_sha256", None)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def parse_checkpoint(raw: object) -> Optional[dict]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return None
    expected = value.get("integrity_sha256")
    actual = hashlib.sha256(_canonical_payload(value)).hexdigest()
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        return None
    if expected != actual or value.get("state") not in ALLOWED_STATES:
        return None
    if not isinstance(value.get("workflow_id"), str) or not value["workflow_id"]:
        return None
    sequence = value.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        return None
    hardware = value.get("hardware")
    if not isinstance(hardware, dict) or not hardware.get("board") or not hardware.get("mcu"):
        return None
    artifact = value.get("artifact")
    if artifact is not None and not isinstance(artifact, dict):
        return None
    # Credentials are forbidden by the producer contract and rejected here if
    # a damaged/foreign producer ever includes them.
    wizard = value.get("wizard_data")
    if not isinstance(wizard, dict) or _contains_secret(value):
        return None
    return value


def checkpoint_event(checkpoint: Mapping[str, object]) -> dict:
    artifact = checkpoint.get("artifact")
    data = {
        "firmware_authority": "durable_checkpoint",
        "last_error": checkpoint.get("last_error", ""),
        "language": checkpoint.get("wizard_data", {}).get("language", "English"),
        "download_available": checkpoint["state"] in {"ARTIFACT_READY", "AWAITING_FLASH", "VERIFYING_MCU"},
        "board": checkpoint["hardware"]["board"],
        "mcu": checkpoint["hardware"]["mcu"],
        "verified_serial_path": checkpoint["hardware"].get("verified_serial_path", ""),
    }
    if isinstance(artifact, dict):
        data.update({
            "method": artifact.get("method", ""),
            "strategy": artifact.get("strategy", ""),
            "final_filename": artifact.get("final_filename", ""),
            "staged_path": artifact.get("path", ""),
            "instructions": artifact.get("instructions", []),
        })
    return {
        "schema": 2,
        "workflow_kind": "firmware_deployment",
        "workflow_id": checkpoint["workflow_id"],
        "sequence": checkpoint["sequence"],
        "state": checkpoint["state"],
        "detail": checkpoint.get("last_error") or checkpoint["state"].replace("_", " ").title(),
        "data": data,
    }
