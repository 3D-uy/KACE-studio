"""Safe, filesystem-only regression tests for image preparation."""

from __future__ import annotations

import hashlib
import io
import lzma
import os
import zipfile
from types import SimpleNamespace

import pytest

import main
from backend.provisioning import ImageType, validate_provisioning


def raw_image(size: int = 1024 * 1024) -> bytes:
    data = bytearray(os.urandom(size))
    data[510:512] = b"\x55\xaa"
    return bytes(data)


def write_sidecar(path, content: bytes):
    path.with_name(path.name + ".sha256").write_text(
        hashlib.sha256(content).hexdigest() + "\n", encoding="utf-8"
    )


@pytest.fixture
def api():
    instance = main.Api()
    instance._flash_cancel_event.clear()
    return instance


def test_cancelled_zip_extraction_leaves_no_final_or_partial_files(api, tmp_path, monkeypatch):
    archive = tmp_path / "image.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
        output.writestr("disk.img", raw_image(5 * 1024 * 1024))
    target = tmp_path / "image.img"
    calls = 0

    def cancel_after_first_chunk():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise ValueError("synthetic cancellation")

    monkeypatch.setattr(api, "_check_cancelled", cancel_after_first_chunk)
    with pytest.raises(ValueError, match="synthetic cancellation"):
        api._decompress_archive(str(archive), str(target))

    assert not target.exists()
    assert not (tmp_path / "image.img.part").exists()
    assert not (tmp_path / "image.img.sha256").exists()
    assert not (tmp_path / "image.img.sha256.part").exists()


@pytest.mark.parametrize("bytes_removed", [12, 100, 1024])
def test_truncated_xz_is_rejected_without_publishing_cache(api, tmp_path, bytes_removed):
    encoded = lzma.compress(raw_image())
    archive = tmp_path / "image.img.xz"
    archive.write_bytes(encoded[:-bytes_removed])
    target = tmp_path / "image.img"

    with pytest.raises(ValueError, match="complete compressed stream"):
        api._decompress_archive(str(archive), str(target))

    assert not target.exists()
    assert not (tmp_path / "image.img.part").exists()
    assert not (tmp_path / "image.img.sha256").exists()


def test_zip_member_size_mismatch_is_rejected(api, tmp_path, monkeypatch):
    content = raw_image()

    class FakeZip:
        def __init__(self, _path, _mode):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def namelist(self):
            return ["disk.img"]

        def getinfo(self, _name):
            return SimpleNamespace(file_size=len(content) + 1)

        def open(self, _name):
            return io.BytesIO(content)

    archive = tmp_path / "image.zip"
    archive.write_bytes(b"synthetic")
    target = tmp_path / "image.img"
    monkeypatch.setattr(zipfile, "ZipFile", FakeZip)

    with pytest.raises(ValueError, match="size mismatch"):
        api._decompress_archive(str(archive), str(target))
    assert not target.exists()
    assert not (tmp_path / "image.img.part").exists()


def test_partial_cached_image_without_sidecar_is_reextracted(api, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    content = raw_image()
    archive = cache / "fixture.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
        output.writestr("disk.img", content)
    write_sidecar(archive, archive.read_bytes())
    target = cache / "fixture.img"
    target.write_bytes(content[:4096])

    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    entry = SimpleNamespace(
        image_type=ImageType.MAINSAILOS_PREBAKED.value,
        architecture="32bit",
        version="fixture-v1",
        url="https://invalid/fixture.zip",
        filename="fixture.zip",
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    manifest = SimpleNamespace(resolve=lambda *_args: entry)
    monkeypatch.setattr(main.ImageManifest, "load_bundled", lambda: manifest)

    resolved = api._resolve_prebaked_image(ImageType.MAINSAILOS_PREBAKED, "32bit")
    assert resolved == str(target)
    assert target.read_bytes() == content
    assert api._cached_file_is_valid(str(target), raw_image=True)


@pytest.mark.parametrize("suffix", [".zip", ".xz", ".img.xz"])
def test_custom_compressed_images_are_rejected_before_writer(api, tmp_path, monkeypatch, suffix):
    custom = tmp_path / f"custom{suffix}"
    custom.write_bytes(b"compressed")
    writer_called = False

    def forbidden_writer(*_args, **_kwargs):
        nonlocal writer_called
        writer_called = True
        raise AssertionError("raw writer must not be called")

    monkeypatch.setattr(main, "flash_drive", forbidden_writer)
    identity = {
        "number": 99,
        "friendly_name": "Test SD",
        "size_bytes": 32 * 1024**3,
        "bus_type": "USB",
        "is_system": False,
        "is_boot": False,
        "serial_number": "SERIAL",
        "unique_id": "UNIQUE",
        "path": r"\\?\usbstor#test",
        "media_type": "Unspecified",
    }
    api._drive_snapshots[99] = identity
    assert api.start_flash(
        99,
        str(custom),
        "host",
        "",
        "",
        "validpass123",
        "mainsail",
        drive_identity=identity,
        image_type=ImageType.CUSTOM_VANILLA.value,
    ) is False
    assert not writer_called


def test_custom_raw_image_requires_plausible_partition_table(api, tmp_path):
    invalid = tmp_path / "invalid.img"
    invalid.write_bytes(b"not a disk image" * 100)
    with pytest.raises(ValueError, match="MBR/GPT"):
        api._resolve_custom_image(str(invalid))

    valid = tmp_path / "valid.img"
    valid.write_bytes(raw_image())
    assert api._resolve_custom_image(str(valid)) == str(valid)


def test_custom_prebaked_family_reaches_injection_without_vanilla_inference(
    api, tmp_path, monkeypatch
):
    image = tmp_path / "custom.img"
    image.write_bytes(raw_image())
    provisioning = validate_provisioning(
        image_type=ImageType.CUSTOM_PREBAKED,
        image_path=str(image),
        hostname="printer-one",
        wifi_ssid="",
        wifi_password="",
        ssh_password="validpass123",
        dashboard_ui="mainsail",
    )
    captured = {}
    monkeypatch.setattr(api, "_resolve_custom_image", lambda path: path)
    monkeypatch.setattr(api, "_validate_raw_image", lambda _path: 1024)
    monkeypatch.setattr(api, "_preflight_prebaked_image", lambda *_args: None)
    monkeypatch.setattr(main, "flash_drive", lambda *_args: (True, ""))

    def fake_inject(*_args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(main, "inject_config", fake_inject)
    api._flash_worker(99, provisioning, {"number": 99})
    assert captured["image_type"] is ImageType.CUSTOM_PREBAKED


def test_cancelled_download_removes_part_and_preserves_canonical(api, tmp_path, monkeypatch):
    cached = tmp_path / "image.img.xz"
    cached.write_bytes(b"previous archive")
    checksum = tmp_path / "image.img.xz.sha256"
    checksum.write_text("previous checksum\n", encoding="utf-8")

    class Response:
        def __init__(self):
            self.parts = [b"A" * 1024, b"B" * 1024]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def info(self):
            return {"Content-Length": "2048"}

        def read(self, _size):
            return self.parts.pop(0) if self.parts else b""

    calls = 0

    def cancel_after_first_chunk():
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise ValueError("synthetic cancellation")

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr("shutil.disk_usage", lambda _path: SimpleNamespace(free=10 * 1024**3))
    monkeypatch.setattr(api, "_check_cancelled", cancel_after_first_chunk)

    with pytest.raises(ValueError, match="synthetic cancellation"):
        api._download_os_image(
            "unused", str(cached), str(checksum), "0" * 64, "https://invalid", "arm64"
        )

    assert cached.read_bytes() == b"previous archive"
    assert checksum.read_text(encoding="utf-8") == "previous checksum\n"
    assert not (tmp_path / "image.img.xz.part").exists()
    assert not (tmp_path / "image.img.xz.sha256.part").exists()


def test_successful_download_atomically_publishes_checksum(api, tmp_path, monkeypatch):
    content = b"complete archive"
    cached = tmp_path / "image.zip"
    checksum = tmp_path / "image.zip.sha256"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def info(self):
            return {"Content-Length": str(len(content))}

        def read(self, _size):
            if getattr(self, "done", False):
                return b""
            self.done = True
            return content

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr("shutil.disk_usage", lambda _path: SimpleNamespace(free=10 * 1024**3))
    api._download_os_image(
        "unused", str(cached), str(checksum), hashlib.sha256(content).hexdigest(),
        "https://invalid", "arm64",
    )

    assert cached.read_bytes() == content
    assert checksum.read_text(encoding="utf-8").strip() == hashlib.sha256(content).hexdigest()
    assert not (tmp_path / "image.zip.part").exists()
    assert not (tmp_path / "image.zip.sha256.part").exists()
