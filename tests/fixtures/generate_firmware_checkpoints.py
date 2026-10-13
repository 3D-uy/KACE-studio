"""Regenerate wire fixtures using a separately supplied KACE checkout.

Run manually with the producer environment: python this_file.py PATH_TO_KACE.
The normal Studio suite reads the committed JSON and needs no sibling checkout.
Only simulated artifact bytes/identity are used; no hardware is contacted.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
import uuid


HISTORICAL_COMMIT = "243d020457942dac9335c411a1b860cbeee6d099"


def generate(producer):
    sys.path.insert(0, str(producer))
    from core import firmware_workflow as current
    from firmware.artifacts import BuildArtifact
    from firmware.identity import FirmwareBuildInputs, ToolchainIdentity

    def git(*args):
        return subprocess.check_output(["git", "-C", str(producer), *args])

    historical_source = git("show", f"{HISTORICAL_COMMIT}:core/firmware_workflow.py")
    historical = ModuleType("historical_firmware_workflow")
    exec(compile(historical_source, "historical_firmware_workflow.py", "exec"), historical.__dict__)
    user = {
        "board": "generic-bigtreetech-skr-v1.4.cfg", "mcu_type": "lpc1769",
        "mcu_path": "/dev/serial/by-id/fixture-original", "language": "Español",
    }
    fixtures = []

    def snapshots(module, data, artifact, prefix):
        # Stable clock/UUIDs keep regeneration reviewable. The state transitions,
        # validation, hashes and wire writer are the producer's real functions.
        with patch.object(module.time, "time", return_value=1700000000), \
                patch.object(module.uuid, "uuid4", return_value=SimpleNamespace(hex="1" * 32)):
            kwargs = {} if module is historical else {"identity_reader": SimpleNamespace(read=lambda _: None)}
            checkpoint = module.create_checkpoint(data, **kwargs)
            states = list(module.FirmwareWorkflowState)
            with tempfile.TemporaryDirectory() as output:
                for state in states:
                    if state.value != "HARDWARE_SELECTED":
                        changes = {}
                        if state.value == "ARTIFACT_READY":
                            changes["artifact"] = artifact
                        if state.value == "MCU_VERIFIED":
                            changes.update(verified_serial_path="/dev/serial/by-id/fixture-verified",
                                           flash_evidence_recorded_at=1700000000)
                        checkpoint = module.transition_checkpoint(checkpoint, state, **changes)
                    target = Path(output) / (state.value + ".json")
                    module.write_checkpoint(deepcopy(checkpoint), str(target))
                    wire = json.loads(target.read_text())
                    current.validate_checkpoint(wire, verify_artifact=False)
                    if prefix == "current" or state.value == "COMPLETE":
                        fixtures.append({"case": prefix + "-" + state.value, "checkpoint": wire})

    with tempfile.TemporaryDirectory() as directory:
        native = Path(directory) / "firmware.bin"
        native.write_bytes(b"synthetic firmware checkpoint contract fixture\n")
        inputs = FirmwareBuildInputs.create(
            klipper_commit="fe4eb8650bd7de4c2100a14eaf09b0965c430e29",
            canonical_config="CONFIG_MACH_LPC176X=y\nCONFIG_MCU=\"lpc1769\"\n",
            toolchain=ToolchainIdentity("fixture", "fixture", "fixture", "fixture"),
            build_id="2" * 32,
        )
        with patch("firmware.artifacts.uuid.uuid4", return_value="fixture-build"):
            build = BuildArtifact.create(
                path=str(native), native_filename=native.name, size_bytes=native.stat().st_size,
                mcu="lpc1769", firmware_fingerprint="", mock_build=True,
                size_warning=False, build_identity=inputs,
            )
        evidence = current.artifact_evidence({**user, "firmware_artifact": build, "firmware_path": str(native)})
        # The same public producer API accepts Pi paths without inspecting them
        # when writing/validating the durable structure (verify_artifact=False).
        evidence["path"] = evidence["build"]["path"] = "/home/kace/kace/firmware.bin"
        snapshots(current, user, evidence, "current")
        old_evidence = historical.artifact_evidence({**user, "firmware_path": str(native),
            "firmware_artifact": SimpleNamespace(to_dict=lambda: {
                "mcu": "lpc1769", "sha256": evidence["sha256"], "size_bytes": evidence["size_bytes"],
            })})
        old_evidence["path"] = "/home/kace/kace/historical/firmware.bin"
        snapshots(historical, user, old_evidence, "historical-v1")
        alias = deepcopy(evidence)
        alias["build"]["mcu"] = "stm32f446xx"
        snapshots(current, {**user, "board": "generic-bigtreetech-octopus-v1.1.cfg",
                            "mcu_type": "stm32f446"}, alias, "producer-mcu-alias")

    # Reuse the producer's actual synthetic identify-dictionary fixture and
    # native -> Robin preparation service. The codec, hashes and evidence are
    # real; the bytes and physical installation are explicitly simulated.
    from tests.unit.test_robin_transformation import prepared_case
    with tempfile.TemporaryDirectory() as directory, \
            patch("firmware.artifacts.uuid.uuid4", return_value=uuid.UUID("3" * 32)):
        _build, _service, _prepared, robin_user = prepared_case(Path(directory))
        evidence = current.artifact_evidence(robin_user)
        proof = evidence["transformation"]
        proof["native_path"] = evidence["build"]["path"] = "/home/kace/kace/native/klipper.bin"
        proof["final_path"] = evidence["path"] = "/home/kace/kace/deploy/Robin_nano.bin"
        robin_user["firmware_path"] = evidence["path"]
        snapshots(current, robin_user, evidence, "producer-robin-transformation")

    source = producer / "core/firmware_workflow.py"
    result = {
        "provenance": {
            "producer_commit": git("rev-parse", "HEAD").decode().strip(),
            "producer_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "historical_commit": HISTORICAL_COMMIT,
            "historical_source_sha256": hashlib.sha256(historical_source).hexdigest(),
            "generation": "create_checkpoint -> transition_checkpoint -> write_checkpoint; validated by current validate_checkpoint(verify_artifact=False)",
            "limits": "Simulated firmware only. Paths are Pi wire data, not local resources or physical evidence. MCU alias decisions remain in KACE.",
            "legacy_optional_fields": ["hardware.baseline_identity", "artifact.expected_vid_pids",
                "artifact.bootloader_vid_pids", "artifact.build.firmware_identity"],
            "robin_fixture_source_sha256": hashlib.sha256(
                (producer / "tests/unit/test_robin_transformation.py").read_bytes()).hexdigest(),
        },
        "cases": fixtures,
    }
    destination = Path(__file__).with_name("firmware_checkpoints.json")
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(fixtures)} producer-validated checkpoints to {destination.name}")


if __name__ == "__main__":
    generate(Path(sys.argv[1]).resolve())
