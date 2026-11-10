"""Generate v1 wire fixtures with KACE's current and original serializers.

Run with KACE's development Python: python this_file.py PATH_TO_KACE.
Only synthetic firmware in temporary directories is used; no physical delivery.
The normal Studio tests consume the JSON without requiring a sibling checkout.
"""

import ast
from dataclasses import fields, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
from unittest.mock import patch
import uuid


HISTORICAL = "216674f4a9647ba3d911936ee1d8d8264d8f6c55"


def generate(producer):
    sys.path.insert(0, str(producer))
    from tests.unit.test_robin_transformation import prepared_case
    from firmware.deployment.models import DeploymentResult, DeploymentStatus

    def historical_source(path):
        return subprocess.check_output(["git", "-C", str(producer), "show", f"{HISTORICAL}:{path}"])

    def module(name, source):
        loaded = ModuleType(name)
        sys.modules[name] = loaded
        exec(compile(source, name, "exec"), loaded.__dict__)
        return loaded

    old_artifacts_source = historical_source("firmware/artifacts.py")
    old_models_source = historical_source("firmware/deployment/models.py")
    old_service_source = historical_source("firmware/deployment/service.py")
    old_artifacts = module("fixture_historical_artifacts", old_artifacts_source)
    old_models = module("fixture_historical_models", old_models_source)
    service_class = next(node for node in ast.parse(old_service_source).body
                         if isinstance(node, ast.ClassDef) and node.name == "FirmwareDeploymentService")
    writer = next(node for node in service_class.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_write_manifest")
    writer_source = "from __future__ import annotations\n" + ast.unparse(writer)
    namespace = {"os": os, "json": json, "tempfile": tempfile}
    exec(compile(writer_source, "historical_manifest_writer", "exec"), namespace)
    cases = []
    with tempfile.TemporaryDirectory() as directory, \
            patch("firmware.artifacts.uuid.uuid4", return_value=uuid.UUID("4" * 32)):
        built, service, prepared, _ = prepared_case(Path(directory))
        built = replace(built, path="/home/kace/kace/native/klipper.bin")
        plan = replace(prepared.plan, artifact=built)
        proof = dict(prepared.transformation, native_path=built.path,
                     final_path="/home/kace/kace/deploy/fixture/Robin_nano.bin")
        prepared = replace(prepared, plan=plan, staged_path=proof["final_path"], transformation=proof)
        old_built = old_artifacts.BuildArtifact(**{
            item.name: getattr(built, item.name) for item in fields(old_artifacts.BuildArtifact)
        })
        old_plan = old_models.DeploymentPlan(**{
            item.name: getattr(plan, item.name) for item in fields(old_models.DeploymentPlan)
        })
        old_target = old_models.DeploymentTarget(**{
            item.name: getattr(plan.target, item.name) for item in fields(old_models.DeploymentTarget)
        })
        old_plan = replace(old_plan, artifact=old_built, target=old_target, final_filename="klipper.bin")
        old_prepared = old_models.PreparedDeployment(old_plan, "/home/kace/kace/deploy/fixture/klipper.bin", built.sha256)
        states = {"ARTIFACT_READY": None, "MEDIA_PREPARED": DeploymentStatus.MEDIA_PREPARED,
                  "ACTION_REQUIRED": DeploymentStatus.ACTION_REQUIRED, "FLASHED": DeploymentStatus.FLASHED,
                  "CANCELLED": DeploymentStatus.CANCELLED, "FAILED_FLASH": DeploymentStatus.FAILED}
        for prefix, candidate, write in (
            ("current-robin", prepared, type(service)._write_manifest),
            ("historical-v1", old_prepared, namespace["_write_manifest"]),
        ):
            for sequence, (state, outcome) in enumerate(states.items(), 1):
                if prefix == "historical-v1" and state == "MEDIA_PREPARED":
                    continue  # This result did not exist in the original producer.
                service._sequences[candidate.plan.deployment_id] = sequence
                result = None if outcome is None else DeploymentResult(
                    outcome, "Synthetic delivery fixture: " + state, candidate,
                    executed_automatically=state == "FLASHED",
                )
                write(service, candidate, state, result)
                wire = json.loads((Path(service.output_dir) / "deployment-manifest.json").read_text())
                cases.append({"case": prefix + "-" + state, "manifest": wire})
    source = producer / "firmware/deployment/service.py"
    result = {
        "provenance": {
            "producer_commit": subprocess.check_output(["git", "-C", str(producer), "rev-parse", "HEAD"], text=True).strip(),
            "producer_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "historical_commit": HISTORICAL,
            "historical_sources_sha256": {
                name: hashlib.sha256(content).hexdigest() for name, content in (
                    ("firmware/artifacts.py", old_artifacts_source),
                    ("firmware/deployment/models.py", old_models_source),
                    ("firmware/deployment/service.py", old_service_source),
                )
            },
            "generation": "Real current/original v1 to_dict serializers and atomic _write_manifest writers; synthetic result states.",
            "limits": "Synthetic native/Robin bytes, not physical evidence. Pi paths replace temporary paths only after preparation. No hardware execution.",
        },
        "cases": cases,
    }
    destination = Path(__file__).with_name("deployment_manifests.json")
    destination.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(cases)} manifests to {destination.name}")


if __name__ == "__main__":
    generate(Path(sys.argv[1]).resolve())
