"""Fluidd uses the shared verified base, boot injection and frontend bridge."""

from dataclasses import replace
import hashlib
import lzma
from pathlib import Path
import shutil
import subprocess

import pytest

import main
from backend import imager
from backend import resources
from backend.image_manifest import ImageManifest
from backend.provisioning import ImageType


@pytest.mark.parametrize("frozen", [False, True])
def test_fluidd_manifest_and_bootstrap_are_identical_in_source_and_bundle(tmp_path, monkeypatch, frozen):
    root = Path(main.__file__).parent
    expected = ImageManifest.load_bundled().resolve("fluidd_prebaked", "64bit")
    bootstrap = (root / "bootstrap.sh").read_bytes()
    if frozen:
        for name in ("bootstrap.sh", "image-manifest.json", "release-contract.json"):
            shutil.copyfile(root / name, tmp_path / name)
        monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
        monkeypatch.setattr(resources.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert ImageManifest.load_bundled().resolve("fluidd_prebaked", "64bit") == expected
    source = resources.resolve_bootstrap_source()
    if frozen:
        assert source == tmp_path / 'bootstrap.sh'
    elif (root.parent / 'KACE/scripts/bootstrap.sh').is_file():
        assert source == root.parent / 'KACE/scripts/bootstrap.sh'
    assert source.read_bytes() == bootstrap
    assert hashlib.sha256(bootstrap).hexdigest() == resources.load_release_contract()['kace']['bootstrap_sha256']


@pytest.mark.parametrize("architecture", ["32bit", "64bit"])
def test_switching_dashboard_reuses_verified_archive_image_and_provenance(tmp_path, monkeypatch, architecture):
    content = bytearray(4096)
    content[510:512] = b"\x55\xaa"
    content = bytes(content)
    compressed = lzma.compress(content)
    image_hash = hashlib.sha256(content).hexdigest()
    archive_hash = hashlib.sha256(compressed).hexdigest()
    entries = []
    for image_type in ("mainsailos_prebaked", "fluidd_prebaked"):
        entry = ImageManifest.load_bundled().resolve(image_type, architecture)
        entries.append(replace(entry, sha256=archive_hash, attestation=replace(
            entry.attestation, archive_sha256=archive_hash, image_sha256=image_hash,
        )))
    monkeypatch.setattr(ImageManifest, "load_bundled", lambda: ImageManifest(tuple(entries)))
    api = main.Api()
    downloads = []

    def download(url, archive, sidecar, expected_hash, *_args):
        downloads.append(url)
        assert expected_hash == archive_hash
        Path(archive).write_bytes(compressed)
        Path(sidecar).write_text(archive_hash + '\n')

    monkeypatch.setattr(api, "_download_os_image", download)
    mainsail = api._resolve_manifest_image("mainsailos_prebaked", architecture, str(tmp_path))
    monkeypatch.setattr(api, "_decompress_archive", lambda *_a: pytest.fail("shared base was decompressed again"))
    fluidd = api._resolve_manifest_image("fluidd_prebaked", architecture, str(tmp_path))
    assert fluidd == mainsail
    assert fluidd.sha256 == image_hash
    assert len(downloads) == 1
    assert api._image_provenance_is_valid(fluidd.path, entries[1], verify_image=True)


@pytest.mark.parametrize("architecture", ["32bit", "64bit"])
def test_fluidd_boot_injection_carries_requested_dashboard_and_exact_bootstrap(tmp_path, monkeypatch, architecture):
    (tmp_path / "cmdline.txt").write_text('console=serial0,115200 rootwait\n')
    monkeypatch.setattr(imager, "get_boot_drive_letter", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(imager, "_verified_boot_volume", lambda path, *_a: path)
    monkeypatch.setattr(imager.subprocess, "run", lambda *_a, **_k: None)
    assert imager.inject_config(
        99, "fluidd-printer", "", "", "validpass123", "fluidd",
        image_type=ImageType.FLUIDD_PREBAKED, os_arch=architecture,
        drive_identity={
            "number": 99, "friendly_name": "Test SD", "serial_number": "TEST",
            "unique_id": "test-id", "size_bytes": 32 * 1024**3,
            "path": "test-usb-path",
            "bus_type": "USB", "is_system": False, "is_boot": False,
        },
    )
    config = (tmp_path / "kace-bootstrap.txt").read_text()
    assert 'DASHBOARD=fluidd\n' in config
    assert 'PREBAKED=true\n' in config
    assert f'OS_ARCH={architecture}\n' in config
    assert (tmp_path / "bootstrap.sh").read_bytes() == Path(imager.resolve_bootstrap_source()).read_bytes()
    assert 'firstrun.sh' in (tmp_path / 'cmdline.txt').read_text()


def test_frontend_selects_fluidd_profile_and_preserves_lite_dashboard_choices():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for frontend execution')
    app = (Path(main.__file__).parent / 'web/app.js').read_text(encoding='utf-8')
    source = app[app.index('function startFlashing()'):app.index('function cancelFlashing()')]
    harness = r'''
const assert = require('node:assert/strict');
const nodes = new Map();
const document = {getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {value:'',checked:false,style:{},dataset:{}});
    return nodes.get(id);
}};
const calls = [];
const window = {updateDeviceState() {}, pywebview:{api:{start_flash(...args) {
    calls.push(args); return Promise.resolve(true);
}}}};
function updateProgress() {}
function alert(message) {throw Error(message);}
let lastFlashedDriveId;
const driveIdentitySnapshots = new Map([['7', {number:7}]]);
function set(id,value) {document.getElementById(id).value=value;}
set('drive-select','7'); set('os-arch-select','64bit'); set('pi-model-select','pi5');
set('hostname-input','printer'); set('ssh-password','validpass123');
'''
    scenario = r'''
for (const dashboard of ['mainsail','fluidd','both']) {
    set('bootstrap-ui-select-imager',dashboard);
    for (const source of ['default','raspios_lite','custom']) {
        set('image-source-select',source);
        set('custom-image-path','custom.img'); set('custom-image-type','custom_vanilla');
        startFlashing();
        const args = calls.at(-1);
        assert.equal(args[6],dashboard);
        assert.equal(args[9],'64bit');
        assert.equal(args[21],source === 'raspios_lite' ? 'raspios_vanilla' :
            source === 'custom' ? 'custom_vanilla' :
            dashboard === 'fluidd' ? 'fluidd_prebaked' : 'mainsailos_prebaked');
    }
}
assert.equal(calls.length,9);
'''
    result = subprocess.run([node, '-e', harness + source + scenario], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
