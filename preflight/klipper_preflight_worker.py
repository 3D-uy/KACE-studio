#!/usr/bin/env python3
"""Load generated configs with Klipper without connecting to an MCU."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import traceback


KLIPPER_ROOT = Path(os.environ.get("KLIPPER_ROOT", "/opt/klipper"))
sys.path.insert(0, str(KLIPPER_ROOT / "klippy"))

import klippy  # noqa: E402
import reactor  # noqa: E402


def validate_fan_references(printer) -> None:
    """Resolve the pinned fan consumers against already constructed objects.

    ControllerFan.handle_connect only resolves heater and stepper names. For
    HeaterFan, perform its heater lookups without handle_ready, which would
    also register a timer. Never send global connect/ready or identify an MCU.
    The loaded registry, not an approximation from section names, is authority.
    """
    for name, fan in printer.lookup_objects():
        parts = name.split()
        if len(parts) < 2:
            continue
        if parts[0] == "heater_fan":
            heaters = printer.lookup_object("heaters")
            for heater_name in fan.heater_names:
                heaters.lookup_heater(heater_name)
        elif parts[0] == "controller_fan":
            fan.handle_connect()


def validate_config(config_path: Path) -> dict:
    """Load real objects/options, then resolve deferred fan references."""
    started = time.monotonic()
    input_fd = os.open(os.devnull, os.O_RDONLY)
    printer = None
    stage = "object_load"
    checks = []
    unsupported_runtime = []
    try:
        start_args = {
            "config_file": str(config_path),
            "gcode_fd": input_fd,
            "debuginput": os.devnull,
            "software_version": os.environ.get("KLIPPER_REF", "matrix"),
            "start_reason": "matrix_validation",
        }
        main_reactor = reactor.Reactor()
        def reject_pause(waketime):
            # AIO constructors may wait for reactor work. Starting the reactor
            # would also run Printer._connect; sleeping without it cannot finish
            # that work. This is an unavailable validator capability, not proof
            # of an invalid Klipper configuration. Keep the marker even when a
            # constructor catches and wraps the exception.
            reason = "Constructor requires an active reactor; offline validation is incomplete"
            unsupported_runtime.append(reason)
            raise RuntimeError(reason)
        main_reactor.pause = reject_pause
        printer = klippy.Printer(main_reactor, None, start_args)
        # Do not start klippy:mcu_identify. Constructors still may perform file
        # IO, so the caller must isolate the filesystem (matrix runner staging).
        printer._read_config()
        if unsupported_runtime:
            raise RuntimeError(unsupported_runtime[0])
        checks.append(stage)
        stage = "fan_references"
        validate_fan_references(printer)
        if unsupported_runtime:
            raise RuntimeError(unsupported_runtime[0])
        checks.append(stage)
        return {
            "valid": True,
            "reason": "Klipper loaded all sections/options and resolved fan references",
            "checks": checks,
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except Exception as exc:  # Klipper uses several config exception classes.
        return {
            "valid": False,
            "stage": stage,
            "checks": checks,
            "reason": str(exc) or exc.__class__.__name__,
            "exception": exc.__class__.__name__,
            **({"infrastructure_error": unsupported_runtime[0]} if unsupported_runtime else {}),
            "traceback": traceback.format_exc(limit=8),
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    finally:
        if printer is not None:
            try:
                printer.send_event("klippy:disconnect")
            except Exception:
                pass
        os.close(input_fd)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: klipper_validator.py MANIFEST.json RESULTS.json", file=sys.stderr)
        return 2
    manifest_path = Path(sys.argv[1])
    results_path = Path(sys.argv[2])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = {}
    for case in manifest["cases"]:
        if case["generation"]["status"] != "generated":
            continue
        config_path = manifest_path.parent / case["config_path"]
        results[case["id"]] = validate_config(config_path)
    payload = {
        "schema_version": 1,
        "klipper_ref": manifest["klipper_ref"],
        "results": results,
    }
    results_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
