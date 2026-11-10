"""Portable pinned Klipper Docker runner, bundled unchanged by Studio.

No KACE model, filesystem publication or SSH dependencies belong in this module.
Configuration authority stays with KACE; this runner checks isolated object loads.
"""
from __future__ import annotations
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile

_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")

def _digest(files):
    digest = sha256()
    for name, content in sorted(files.items()):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(sha256(content).digest())
    return digest.hexdigest()

def _check_staged(root, files):
    """Ensure the mounted inputs are still the exact reviewed regular files."""
    actual = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("staged configuration contains a symlink")
        if path.is_file():
            actual[path.relative_to(root).as_posix()] = path.read_bytes()
        elif not path.is_dir():
            raise RuntimeError("staged configuration contains a special file")
    if actual != files:
        raise RuntimeError("staged configuration changed during preflight")

def _sha(path):
    return sha256(path.read_bytes()).hexdigest()

def _classify(result):
    if not isinstance(result, dict) or result.get("infrastructure_error"):
        return "unverified"
    if result.get("valid") is True and result.get("checks") == ["object_load", "fan_references"]:
        return "valid"
    if (result.get("valid") is False and result.get("stage") == "fan_references"
            and result.get("checks") == ["object_load"] and result.get("exception") == "Error"
            and isinstance(result.get("reason"), str)
            and (result["reason"].startswith("Unknown heater ")
                 or result["reason"].startswith("One or more of these steppers are unknown:"))):
        return "invalid"
    return "unverified"

def _run(args, timeout):
    return subprocess.run(args, text=True, encoding="utf-8", errors="replace",
                          capture_output=True, timeout=timeout, check=False,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

def validate_paths(files):
    if not isinstance(files, dict) or "printer.cfg" not in files:
        raise ValueError("printer.cfg missing from effective tree")
    folded = set()
    for name, content in files.items():
        if not isinstance(name, str) or not isinstance(content, bytes):
            raise ValueError("configuration paths and content must be text and bytes")
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or name != path.as_posix()
                or "\\" in name or ":" in name or any(ord(c) < 32 for c in name)
                or any(part in ("", ".", "..") for part in name.split("/"))):
            raise ValueError("unreviewable configuration path")
        if any(parent.as_posix() in files for parent in path.parents if str(parent) != "."):
            raise ValueError("configuration file/directory collision")
        if os.name == "nt":
            if name.casefold() in folded or any(part.endswith((" ", ".")) or
                    re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                    for part in path.parts):
                raise ValueError("configuration tree cannot be represented faithfully on Windows")
            folded.add(name.casefold())


def provision(*, pin, recipe, worker, receipt_path, run=_run, sha=_sha, which=shutil.which):
    """Explicitly build the pinned image and record its immutable local ID."""
    docker = which("docker")
    if not docker:
        raise RuntimeError("Docker CLI not available")
    with tempfile.TemporaryDirectory(prefix="kace-preflight-build-") as temporary:
        directory = Path(temporary)
        context = directory / "context"
        (context / "core").mkdir(parents=True)
        recipe_hash, worker_hash = sha(recipe), sha(worker)
        shutil.copyfile(recipe, context / "core/klipper_preflight.Dockerfile")
        shutil.copyfile(worker, context / "core/klipper_preflight_worker.py")
        if (sha(context / "core/klipper_preflight.Dockerfile") != recipe_hash
                or sha(context / "core/klipper_preflight_worker.py") != worker_hash):
            raise RuntimeError("preflight build resources changed during staging")
        iid = directory / "iid"
        build = run([docker, "build", "--iidfile", str(iid), "--build-arg", f"KLIPPER_REF={pin}",
                      "-f", str(context / "core/klipper_preflight.Dockerfile"), str(context)], 1800)
        if build.returncode != 0:
            raise RuntimeError("preflight image build failed: " + (build.stderr or build.stdout)[-2000:])
        image = iid.read_text(encoding="utf-8").strip()
        if not _IMAGE_ID.fullmatch(image):
            raise RuntimeError("Docker did not provide an immutable image ID")
        inspect = run([docker, "image", "inspect", "--format", "{{.Id}}", image], 30)
        if inspect.returncode or inspect.stdout.strip() != image:
            raise RuntimeError("built image identity could not be verified")
        if sha(recipe) != recipe_hash or sha(worker) != worker_hash:
            raise RuntimeError("preflight build resources changed during provisioning")
        receipt = {"schema": 1, "pin": pin, "image_id": image,
                   "dockerfile_sha256": recipe_hash, "worker_sha256": worker_hash}
        target = Path(receipt_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, target)
        return receipt

def validate_files(files, *, pin, recipe, worker, receipt_path, run=_run,
                   sha=_sha, which=shutil.which, check_staged=_check_staged):
    """Run only supplied bytes; missing infrastructure is never a CFG rejection."""
    digest = ""
    try:
        validate_paths(files)
        digest = _digest(files)
    except Exception as exc:
        return {"status": "unverified", "detail": f"configuration tree unavailable: {exc}", "tree_digest": digest}
    try:
        docker = which("docker")
        if not docker:
            raise RuntimeError("Docker CLI not available")
        try:
            receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeError(
                "preflight image is not provisioned; from the KACE directory run "
                "python -m core.klipper_preflight --provision"
            ) from exc
        image = receipt.get("image_id", "")
        if (receipt.get("schema") != 1 or receipt.get("pin") != pin or not _IMAGE_ID.fullmatch(image)
                or receipt.get("dockerfile_sha256") != sha(recipe)
                or receipt.get("worker_sha256") != sha(worker)):
            raise RuntimeError("preflight image receipt does not match runtime resources")
        inspect = run([docker, "image", "inspect", "--format", "{{.Id}}", image], 30)
        if inspect.returncode or inspect.stdout.strip() != image:
            raise RuntimeError("provisioned image missing or identity mismatch")
        with tempfile.TemporaryDirectory(prefix="kace-preflight-") as temporary:
            stage = Path(temporary)
            for name, content in files.items():
                destination = stage / "tree" / name
                if not destination.resolve().is_relative_to((stage / "tree").resolve()):
                    raise ValueError("configuration path escapes staging tree")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            check_staged(stage / "tree", files)
            manifest = {"klipper_ref": pin, "cases": [{"id": "plan", "config_path": "tree/printer.cfg",
                                                          "generation": {"status": "generated"}}]}
            (stage / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            command = [docker, "run", "--rm", "--pull", "never", "--network", "none", "--cap-drop", "ALL",
                       "--security-opt", "no-new-privileges", "--read-only", "--pids-limit", "128",
                       "--memory", "512m", "--cpus", "1", "--tmpfs", "/tmp:rw,exec,nosuid,nodev",
                       "--mount", f"type=bind,source={stage.resolve()},target=/matrix,readonly", "--entrypoint", "sh",
                       image, "-c", "echo '" + sha(worker) + "  /usr/local/bin/kace-klipper-validator' | sha256sum -c >/dev/null && "
                       "cp -a /opt/klipper /tmp/klipper && "
                       "test \"$(git -C /tmp/klipper rev-parse HEAD)\" = " + pin + " && "
                       "KLIPPER_ROOT=/tmp/klipper python -I /usr/local/bin/kace-klipper-validator "
                       "/matrix/manifest.json /tmp/result.json && cat /tmp/result.json"]
            execution = run(command, 120)
            if execution.returncode:
                raise RuntimeError("isolated Klipper failed: " + (execution.stderr or execution.stdout)[-1000:])
            check_staged(stage / "tree", files)
            payload = json.loads(execution.stdout)
            if payload.get("schema_version") != 1 or payload.get("klipper_ref") != pin:
                raise RuntimeError("invalid Klipper result envelope")
            results = payload.get("results")
            if not isinstance(results, dict) or set(results) != {"plan"}:
                raise RuntimeError("missing or unexpected Klipper results")
            result = results["plan"]
            status = _classify(result)
            detail = (result.get("infrastructure_error") or result.get("reason", "Klipper result unavailable")) if isinstance(result, dict) else "invalid result"
            if status == "valid":
                detail += "; fan references checked in pinned image, target extensions and hardware unverified"
            return {"status": status, "detail": detail, "tree_digest": digest}
    except Exception as exc:
        return {"status": "unverified", "detail": f"isolated Klipper unavailable: {exc}", "tree_digest": digest}
