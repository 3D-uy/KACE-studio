"""Copy the authoritative KACE preflight runner as verified, portable resources.

Run explicitly when KACE's loader changes. This does not change release pins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FILES = ("klipper_preflight_engine.py", "klipper_preflight.Dockerfile", "klipper_preflight_worker.py")


def synchronize(source: Path, destination: Path, *, check: bool = False):
    contract = (source / "data/klipper_contract.yaml").read_text(encoding="utf-8")
    matches = re.findall(r"^validated_commit: ([0-9a-f]{40})\s*$", contract, re.M)
    if len(matches) != 1:
        raise ValueError("KACE must declare exactly one validated Klipper commit")
    contents = {name: (source / "core" / name).read_bytes().replace(b"\r\n", b"\n") for name in FILES}
    manifest = {"schema": "kace-studio-preflight/v1", "pin": matches[0],
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}}
    contents["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    if check:
        for name, data in contents.items():
            if (destination / name).read_bytes() != data:
                raise ValueError(f"Preflight bundled resource differs from KACE: {name}")
    else:
        destination.mkdir(parents=True, exist_ok=True)
        for name, data in contents.items():
            (destination / name).write_bytes(data)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT.parent / "KACE")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    synchronize(args.source, ROOT / "preflight", check=args.check)
    print("Preflight resources match KACE." if args.check else "Preflight resources synchronized from KACE.")
