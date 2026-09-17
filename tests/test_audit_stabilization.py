"""Cross-stage audit regressions; every device and network operation is simulated."""
import hashlib
import io
import json
import struct
import shutil
import subprocess
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

import main
from backend import kace_writer, resources
from backend.ssh_client import SSHSession
from scripts import release
from tests.test_drive_identity import snapshot


def handle_reply(identity, serial=None):
    serial = (serial or identity["serial_number"]).encode() + b"\0"
    descriptor = bytearray(36 + len(serial))
    struct.pack_into("<II", descriptor, 0, 36, len(descriptor))
    struct.pack_into("<II", descriptor, 24, 36, 7)
    descriptor[36:] = serial
    def reply(code, size, request=None):
        if code == 0x002D1080:
            return struct.pack("<III", 7, identity["number"], 0xFFFFFFFF)
        if code == 0x0007405C:
            return struct.pack("<q", identity["size_bytes"])
        assert code == 0x002D1400
        return bytes(descriptor[:size])
    return reply


@pytest.mark.parametrize("replacement", [False, True])
def test_main_binds_write_to_open_handle_even_when_number_is_reused(tmp_path, monkeypatch, replacement):
    expected = snapshot(size_bytes=4096)
    image = tmp_path / "image.img"
    image.write_bytes(b"x" * 512)
    status = tmp_path / ("kace_flash_3_" + "a" * 32 + ".json")
    writes, opens = [], []
    class Disk(io.BytesIO):
        lock_failed = False
        volume_handles = ()
        verify_identity = kace_writer.Win32DiskWriter.verify_identity
        def __init__(self, path, number):
            super().__init__()
            opens.append(path)
            self._query_handle = handle_reply(expected, "OTHER" if replacement else None)
        def write(self, content):
            writes.append(content)
            return super().write(content)
    monkeypatch.setattr(kace_writer, "_validate_disk_identity", lambda *_: dict(expected))
    monkeypatch.setattr(kace_writer, "Win32DiskWriter", Disk)
    monkeypatch.setattr(kace_writer, "_expected_status_path", lambda *_: str(status))
    command = Mock()
    monkeypatch.setattr(kace_writer.subprocess, "run", command)
    contract = {"operation_id": "a" * 32, "disk_identity": expected, "image_size": 512, "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
    monkeypatch.setattr(kace_writer.sys, "argv", ["writer", "3", str(image), str(status), json.dumps(contract)])
    with pytest.raises(SystemExit) as result:
        kace_writer.main()
    assert result.value.code == (2 if replacement else 0)
    assert bool(writes) is not replacement
    assert opens == [expected["path"]]
    assert json.loads(status.read_text())["status"] == ("error" if replacement else "success")
    assert json.loads(status.read_text())["operation_id"] == "a" * 32
    assert all("Set-Disk" not in str(c) for c in command.call_args_list)


def test_unreadable_or_incomplete_handle_identity_fails_closed():
    disk = kace_writer.Win32DiskWriter.__new__(kace_writer.Win32DiskWriter)
    disk._query_handle = lambda *_: b""
    with pytest.raises(OSError, match="Incomplete"):
        disk.verify_identity(snapshot())


@pytest.mark.parametrize("failure", [False, True])
def test_sftp_download_preserves_backup_until_complete(tmp_path, monkeypatch, failure):
    target = tmp_path / "backup.cfg"
    target.write_bytes(b"original backup")
    session = SSHSession()
    sftp = Mock()
    def get(remote, local):
        # Paramiko opens/truncates the supplied filename before receiving bytes.
        assert target.read_bytes() == b"original backup"
        Path(local).write_bytes(b"partial" if failure else b"complete")
        if failure:
            raise OSError("transfer interrupted")
    sftp.get.side_effect = get
    monkeypatch.setattr(session, "get_sftp", lambda: sftp)
    assert session.download_file("/remote/backup.cfg", str(target)) is not failure
    assert target.read_bytes() == (b"original backup" if failure else b"complete")
    assert list(tmp_path.iterdir()) == [target]
    sftp.close.assert_called_once()


def test_download_rejects_session_switch_inside_save_dialog():
    api = main.Api()
    original, replacement = Mock(), Mock()
    api._ssh = original
    api._window = Mock()
    def dialog(*_, **__):
        api._ssh = replacement
        api._ssh_gen += 1
        return "backup.cfg"
    api._window.create_file_dialog.side_effect = dialog
    assert api.download_file("/a/file.cfg", api._ssh_gen) is False
    original.download_file.assert_not_called()
    replacement.download_file.assert_not_called()


def test_listing_rejects_session_switch_and_stale_download_generation():
    api = main.Api()
    original = Mock()
    api._ssh = original
    def listing(_):
        api._ssh = Mock()
        api._ssh_gen += 1
        return [{"name": "old-host.cfg"}]
    original.list_directory.side_effect = listing
    with pytest.raises(RuntimeError, match="session changed"):
        api.list_sftp_directory("/a")
    api._window = Mock()
    assert api.download_file("/a/old-host.cfg", api._ssh_gen - 1) is False
    api._window.create_file_dialog.assert_not_called()


@pytest.fixture
def pending_runtime(monkeypatch):
    contract = release.load_contract()
    contract["kace"]["runtime_status"] = "pending_commit"
    monkeypatch.setattr(resources, "load_release_contract", lambda: contract)
    return contract


def test_pending_candidate_blocks_bootstrap_delivery_and_old_pin_is_rejected(pending_runtime):
    with pytest.raises(resources.ResourceContractError, match="pending"):
        resources.resolve_bootstrap_source()
    with pytest.raises(release.ReleaseContractError, match="required runtime"):
        release.verify_local_candidate(release.ROOT.parent / "KACE", "243d020457942dac9335c411a1b860cbeee6d099", pending_runtime)


def test_pending_candidate_blocks_before_any_flash_worker(monkeypatch, pending_runtime):
    api = main.Api()
    expected = snapshot()
    api._drive_snapshots[3] = dict(expected)
    worker = Mock()
    monkeypatch.setattr(main.threading.Thread, "start", worker)
    state = Mock()
    api.set_device_state = state
    assert api.start_flash(3, "image.img", "host", "", "", "validpass123", "mainsail", drive_identity=expected) is False
    worker.assert_not_called()
    assert "pending" in state.call_args.args[2]
    api._ssh = Mock()
    assert api.start_bootstrap("mainsail")["status"] == "failed"
    api._ssh.send_input.assert_not_called()


def test_packaging_gate_runs_before_pyinstaller_analysis(pending_runtime):
    # Execute only the real spec's preamble: never invoke Analysis or a build.
    source = (release.ROOT / "main.spec").read_text()
    preamble = source[:source.index("from PyInstaller")]
    with pytest.raises(resources.ResourceContractError, match="pending"):
        exec(compile(preamble, "main.spec", "exec"), {"SPECPATH": str(release.ROOT)})


def test_candidate_verification_compares_commit_bytes(monkeypatch, tmp_path):
    contract = {"kace": {"required_runtime_files": {"core/example.py": hashlib.sha256(b"fixed").hexdigest()}}}
    run = Mock(return_value=Mock(returncode=0, stdout=b"fixed"))
    monkeypatch.setattr(release.subprocess, "run", run)
    release.verify_local_candidate(tmp_path, "a" * 40, contract)
    assert run.call_args.args[0][-1] == "a" * 40 + ":core/example.py"
    run.return_value.stdout = b"old"
    with pytest.raises(release.ReleaseContractError, match="required runtime"):
        release.verify_local_candidate(tmp_path, "a" * 40, contract)


@pytest.mark.parametrize("matching", [True, False])
def test_remote_bootstrap_only_executes_exact_contract_bytes(tmp_path, monkeypatch, finalized_release_contract, matching):
    bash = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("Bash is required to exercise the remote command locally")
    script = tmp_path / "bootstrap.sh"
    script.write_bytes(b"printf 'executed-verified-bootstrap\\n'\n")
    contract = resources.load_release_contract()
    contract["kace"]["bootstrap_sha256"] = hashlib.sha256(script.read_bytes()).hexdigest() if matching else "0" * 64
    monkeypatch.setattr(main, "load_release_contract", lambda: contract)
    api = main.Api()
    api._ssh = Mock()
    assert api.start_bootstrap("mainsail")["status"] == "started"
    command = api._ssh.send_input.call_args.args[0]
    # Production boot paths have no spaces; local temporary roots may have them.
    # Quote the file predicates before substituting the fixture paths, just like
    # the digest check and invocation already do in the real command.
    command = command.replace("-f /boot/firmware/bootstrap.sh", "-f '/boot/firmware/bootstrap.sh'")
    command = command.replace("-f /boot/bootstrap.sh", "-f '/boot/bootstrap.sh'")
    command = command.replace("/boot/firmware/bootstrap.sh", script.as_posix())
    command = command.replace("/boot/bootstrap.sh", (tmp_path / "absent.sh").as_posix())
    result = subprocess.run([bash, "-c", "export PATH=/usr/bin:/bin:$PATH; " + command], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert ("executed-verified-bootstrap" in result.stdout) is matching
    assert ("BOOTSTRAP_CONTRACT_MISMATCH" in result.stdout) is not matching


def test_remote_candidate_checks_runtime_not_only_installer(monkeypatch):
    contract = release.load_contract()
    calls = []
    monkeypatch.setattr(release, "_download_verified", lambda url, destination, digest: calls.append((url, digest)))
    release.verify_remote_installer(contract)
    for name, digest in contract["kace"]["required_runtime_files"].items():
        assert (f'https://raw.githubusercontent.com/3D-uy/KACE/{contract["kace"]["candidate_ref"]}/{name}', digest) in calls
