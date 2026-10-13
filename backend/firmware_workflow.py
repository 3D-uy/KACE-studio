"""Read-only validation/projection of KACE's durable firmware checkpoint."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping, Optional


SCHEMA = "kace-firmware-workflow/v1"
_EARLY_STATES = {"HARDWARE_SELECTED", "COMPILE_REQUIRED"}
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
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _integer(value: object, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def _valid_artifact(artifact: dict) -> bool:
    if (not _text(artifact.get("path")) or not _text(artifact.get("final_filename"))
            or not _digest(artifact.get("sha256")) or not _integer(artifact.get("size_bytes"), 1)):
        return False
    if any(not isinstance(artifact.get(key, ""), str) for key in ("method", "strategy")):
        return False
    instructions = artifact.get("instructions", [])
    if not isinstance(instructions, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("text"), str)
        or not isinstance(item.get("id", ""), str) for item in instructions
    ):
        return False
    for key in ("expected_vid_pids", "bootloader_vid_pids"):
        values = artifact.get(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            return False
    build = artifact.get("build")
    if not isinstance(build, dict) or not _text(build.get("mcu")):
        return False
    identity = build.get("firmware_identity")
    if identity is not None and not isinstance(identity, dict):
        return False
    identity = identity or {}
    native_digest = artifact["sha256"].lower()
    transformation = artifact.get("transformation")
    if transformation is not None:
        # Check references between saved records only. KACE owns the board,
        # transformation algorithm, serial selection and physical receipts.
        if (not isinstance(transformation, dict)
                or transformation.get("final_path") != artifact["path"]
                or transformation.get("final_sha256") != native_digest
                or transformation.get("final_filename") != artifact["final_filename"]
                or transformation.get("size_bytes") != artifact["size_bytes"]
                or not _text(transformation.get("native_path"))
                or transformation["native_path"] != build.get("path")
                or not _digest(transformation.get("native_sha256"))):
            return False
        native_digest = transformation["native_sha256"]
    # Legacy v1 builds can lack these identity fields. If recorded, their
    # hashes/sizes must agree; absence never causes Studio to fabricate evidence.
    for record, hash_key, size_key in (
        (build, "sha256", "size_bytes"),
        (identity, "artifact_sha256", "artifact_size"),
    ):
        digest, size = record.get(hash_key), record.get(size_key)
        if digest is not None and (not _digest(digest) or digest != native_digest):
            return False
        if size is not None and (not _integer(size, 1) or size != artifact["size_bytes"]):
            return False
    # Do not compare build.mcu with hardware.mcu as strings: KACE resolves
    # legitimate processor aliases using its BoardContracts, not this reader.
    return True


def parse_checkpoint(raw: object) -> Optional[dict]:
    """Validate the v1 wire structure without reading files or checking hardware.

    This is an observational desktop boundary, not KACE's deployment validator.
    A historical COMPLETE does not require its Pi artifact to still exist.
    """
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return _validated_checkpoint(value)
    except (TypeError, ValueError, RecursionError):
        # Untrusted JSON/mappings must not crash the recovery API or UI.
        return None


def _validated_checkpoint(value: object) -> Optional[dict]:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return None
    expected = value.get("integrity_sha256")
    actual = hashlib.sha256(_canonical_payload(value)).hexdigest()
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        return None
    state = value.get("state")
    if expected != actual or not isinstance(state, str) or state not in ALLOWED_STATES:
        return None
    if not isinstance(value.get("workflow_id"), str) or not value["workflow_id"]:
        return None
    sequence = value.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        return None
    hardware = value.get("hardware")
    wizard = value.get("wizard_data")
    if (not isinstance(hardware, dict) or not isinstance(wizard, dict)
            or not _text(hardware.get("board")) or not _text(hardware.get("mcu"))
            or wizard.get("board") != hardware["board"].strip()):
        return None
    if any(not _integer(value.get(key)) for key in ("created_at", "updated_at")):
        return None
    if not isinstance(value.get("last_error", ""), str) or not isinstance(wizard.get("language", ""), str):
        return None
    if any(not isinstance(hardware.get(key, ""), str)
           for key in ("baseline_serial_path", "verified_serial_path")):
        return None
    baseline = hardware.get("baseline_identity")
    if baseline is not None and not isinstance(baseline, dict):
        return None
    if not _integer(hardware.get("flash_evidence_recorded_at", 0)):
        return None
    if state in {"MCU_VERIFIED", "CONFIG_GENERATED", "READY_TO_DEPLOY", "DEPLOYING", "COMPLETE"}:
        # Validate saved evidence shape only; KACE owns physical verification.
        if (not _text(hardware.get("verified_serial_path"))
                or not _integer(hardware.get("flash_evidence_recorded_at"), 1)):
            return None
    artifact = value.get("artifact")
    if artifact is None:
        if state not in _EARLY_STATES:
            return None
    elif not isinstance(artifact, dict) or not _valid_artifact(artifact):
        return None
    # Credentials are forbidden by the producer contract and rejected here if
    # a damaged/foreign producer ever includes them.
    if _contains_secret(value):
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
