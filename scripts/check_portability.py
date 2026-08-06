#!/usr/bin/env python3
"""Reject developer-machine paths while allowing target runtime paths."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ALLOWED_TARGET_HOME_USERS = {
    "biqu",
    "fluidd",
    "kace",
    "klipper",
    "mainsail",
    "pi",
    "user",
}

LOCAL_PATH_PATTERNS = (
    (
        "Windows user profile",
        re.compile(r"(?i)[a-z]:(?:\\+|/+)users(?:\\+|/+)[^\\/\s\"']+"),
    ),
    (
        "macOS user profile",
        re.compile(r"/" r"Users/[^/\s\"']+"),
    ),
    (
        "Codex attachment path",
        re.compile(r"(?i)\.codex[\\/]+attachments"),
    ),
    (
        "developer workspace path",
        re.compile(r"(?i)(?:open\s+world|KACE[-_]ecosystem)"),
    ),
)
LINUX_HOME_PATTERN = re.compile(r"/home/([A-Za-z0-9._-]+)(?:/|\b)")
SKIPPED_PARTS = {".git", ".pytest_cache", ".venv", "__pycache__", "build", "dist", "node_modules"}


def scan_text(text: str) -> list[tuple[str, str]]:
    violations = []
    for label, pattern in LOCAL_PATH_PATTERNS:
        match = pattern.search(text)
        if match:
            violations.append((label, match.group(0)))
    for match in LINUX_HOME_PATTERN.finditer(text):
        if match.group(1).casefold() not in ALLOWED_TARGET_HOME_USERS:
            violations.append(("developer Linux home", match.group(0)))
    return violations


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [root / name for name in result.stdout.decode("utf-8").split("\0") if name]


def find_violations(root: Path) -> list[str]:
    findings = []
    for path in _tracked_files(root):
        relative = path.relative_to(root)
        if any(part in SKIPPED_PARTS for part in relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for label, match in scan_text(line):
                findings.append(f"{relative}:{line_number}: {label}: {match}")
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = find_violations(root)
    if findings:
        print("Developer-local paths detected:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("Portability check passed: no developer-local paths detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
