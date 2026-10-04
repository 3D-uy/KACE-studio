"""Check PR bootstrap identity and mandatory frontend coverage without release fetches."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_MODULES = {
    "test_bootstrap_frontend_session", "test_imager_persistence", "test_discovery_controls",
    "test_installation_recovery", "test_moonraker_authorization_frontend",
    "test_power_frontend_identity", "test_sftp_frontend_identity", "test_ssh_recovery",
}
FRONTEND_CASES = {
    "test_audit_followup.test_firmware_download_passes_originating_generation_and_rejects_stale_view",
    "test_bootstrap_contract.test_frontend_rejects_bootstrap_error_marker",
    "test_fluidd_provisioning.test_frontend_selects_fluidd_profile_and_preserves_lite_dashboard_choices",
}


def verify_bootstrap(root=ROOT):
    committed = subprocess.check_output(["git", "show", "HEAD:bootstrap.sh"], cwd=root)
    current = (root / "bootstrap.sh").read_bytes()
    if current != committed:
        raise RuntimeError("Source CI must test bootstrap.sh from the checked-out commit")
    return hashlib.sha256(current).hexdigest()


def check_frontend_report(path):
    cases = list(ET.parse(path).iter("testcase"))
    required = []
    for case in cases:
        module = case.get("classname", "").removeprefix("tests.")
        name = case.get("name", "").split("[", 1)[0]
        identity = module + "." + name
        skipped = case.find("skipped")
        node_skip = skipped is not None and "node" in (
            skipped.get("message", "") + (skipped.text or "")).lower()
        if (module in FRONTEND_MODULES or identity in FRONTEND_CASES
                or "frontend" in identity.lower() or node_skip):
            required.append((module, identity, case))
    seen_modules = {module for module, _, _ in required}
    seen_cases = {identity for _, identity, _ in required}
    missing = sorted((FRONTEND_MODULES - seen_modules) | (FRONTEND_CASES - seen_cases))
    rejected = [identity for _, identity, case in required
                if any(case.find(tag) is not None for tag in ("skipped", "failure", "error"))]
    if missing or rejected:
        raise RuntimeError(f"Mandatory frontend coverage missing={missing}, unsuccessful={rejected}")
    return len(required)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    report = {"success": False}
    try:
        report["bootstrap_sha256"] = verify_bootstrap()
        report["tested_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        if args.junit:
            report["frontend_cases"] = check_frontend_report(args.junit)
        report["success"] = True
    except Exception as error:
        report["error"] = str(error)
        print(error)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
