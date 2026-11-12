"""Content fixtures keep the production missing/hash gates active."""
import sys
from pathlib import Path

import pytest

from backend import imager, resources
from backend.provisioning import ProvisioningValidationError, validate_provisioning
from tests.test_provisioning_preflight import valid_request


def test_candidate_fixture_does_not_populate_destination(tmp_path, coherent_provisioning_candidate):
    assert not coherent_provisioning_candidate["root"].is_relative_to(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("frozen", [False, True], ids=["source", "frozen"])
@pytest.mark.parametrize("change", ["none", "tamper", "missing"])
def test_synthetic_candidate_still_checks_exact_bytes(
    coherent_provisioning_candidate, monkeypatch, frozen, change,
):
    candidate = coherent_provisioning_candidate
    monkeypatch.setattr(resources, "is_frozen", lambda: frozen)
    if frozen:
        monkeypatch.setattr(sys, "_MEIPASS", str(candidate["root"]), raising=False)
    path = candidate["root"] / "bootstrap.sh" if frozen else candidate["source"]
    if change == "tamper":
        path.write_bytes(candidate["bytes"] + b"\n# unreviewed bytes\n")
    elif change == "missing":
        path.unlink()
        if not frozen:
            # Source mode may legitimately fall back to the bundled resource.
            (candidate["root"] / "bootstrap.sh").unlink()
    resolved, exists, digest = imager.bootstrap_preflight_facts()
    request = valid_request(bootstrap_exists=exists, bootstrap_sha256=digest)
    if change == "none":
        assert digest == candidate["sha256"]
        assert validate_provisioning(**request).hostname == "printer-one"
        assert Path(resolved).read_bytes() == candidate["bytes"]
    else:
        with pytest.raises(ProvisioningValidationError) as caught:
            validate_provisioning(**request)
        assert caught.value.field == "bootstrap"
