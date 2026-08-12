#!/usr/bin/env python3
"""Run the packaged PyWebView smoke with a hard external process deadline."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


DEFAULT_TIMEOUT_SECONDS = 45.0


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        terminated = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if terminated.returncode == 0:
            return
    process.kill()


def run_smoke(executable: Path, timeout_seconds: float) -> int:
    process = subprocess.Popen(
        [str(executable), "--smoke-test"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        process.wait(timeout=10)
        print(
            f"PyWebView smoke exceeded its {timeout_seconds:g}s process deadline.",
            file=sys.stderr,
        )
        return 124

    if process.returncode != 0:
        diagnostic = (stderr or stdout or "no executable diagnostic").strip()
        print(
            f"PyWebView smoke failed with exit code {process.returncode}: {diagnostic}",
            file=sys.stderr,
        )
        return process.returncode or 1
    print("KACE Studio packaged PyWebView smoke passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    if not args.executable.is_file():
        parser.error(f"executable does not exist: {args.executable}")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return run_smoke(args.executable.resolve(), args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
