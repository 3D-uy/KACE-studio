"""Suite-wide isolation for per-user writable application data."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_local_application_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))


@pytest.fixture
def finalized_release_contract(monkeypatch):
    """Exercise provisioning with a finalized candidate, independently of release staging.

    Tests of the pending candidate use the actual contract and do not use this
    fixture. Resource bytes and pin/hash validation remain unchanged here.
    """
    from backend import resources
    load = resources.load_release_contract
    def finalized():
        contract = load()
        contract["kace"]["runtime_status"] = "pinned"
        return contract
    monkeypatch.setattr(resources, "load_release_contract", finalized)


@pytest.fixture
def coherent_provisioning_candidate(tmp_path_factory, monkeypatch):
    """Synthetic, byte-bound candidate for provisioning-content tests only.

    Copy the current producer into isolated source and frozen resource layouts.
    All SHA checks and file writes remain real. This does not update or qualify
    the repository's release contract; its separate delivery gates stay intact.
    """
    import copy
    import hashlib
    from backend import provisioning, resources

    project = resources.PROJECT_ROOT
    producer = project.parent / "KACE" / "scripts" / "bootstrap.sh"
    if not producer.is_file():
        producer = project / "bootstrap.sh"
    payload = producer.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    isolated = tmp_path_factory.mktemp("provisioning-candidate") / "KACE-studio"
    sibling = isolated.parent / "KACE" / "scripts" / "bootstrap.sh"
    sibling.parent.mkdir(parents=True)
    isolated.mkdir()
    sibling.write_bytes(payload)
    (isolated / "bootstrap.sh").write_bytes(payload)
    (isolated / "image-manifest.json").write_bytes((project / "image-manifest.json").read_bytes())
    contract = copy.deepcopy(resources.load_release_contract())
    contract["kace"]["runtime_status"] = "pinned"
    contract["kace"]["bootstrap_sha256"] = digest
    monkeypatch.setattr(resources, "PROJECT_ROOT", isolated)
    monkeypatch.setattr(resources, "load_release_contract", lambda: copy.deepcopy(contract))
    monkeypatch.setattr(provisioning, "EXPECTED_BOOTSTRAP_SHA256", digest)
    return {"root": isolated, "source": sibling, "sha256": digest, "bytes": payload}


@pytest.fixture
def isolated_boot_refresh(monkeypatch):
    """Keep content-only injection tests off the host storage-cache refresh.

    Replace imager's bindings, not the shared time/subprocess modules: the
    upstream WiFi parser and other shell/timing contracts still execute normally.
    Check calls in teardown because inject_config catches refresh exceptions.
    Password hashing, file publication and bootstrap validation remain real.
    """
    from subprocess import CompletedProcess
    from types import SimpleNamespace

    from backend import imager

    calls = []

    def sleep(*args, **kwargs):
        calls.append(("sleep", args, kwargs))

    def run(*args, **kwargs):
        calls.append(("run", args, kwargs))
        return CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(imager, "time", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(imager, "subprocess", SimpleNamespace(run=run))
    yield

    expected_pair = [
        ("sleep", (1,), {}),
        ("run", (["powershell", "-Command", "Update-HostStorageCache"],),
         {"capture_output": True, **imager.SUBPROCESS_FLAGS}),
    ]
    assert calls and len(calls) % 2 == 0, calls
    assert calls == expected_pair * (len(calls) // 2)
