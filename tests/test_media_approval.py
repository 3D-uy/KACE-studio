"""Source and optional packaged UI regression using two synthetic disks."""
import os
from pathlib import Path
from html.parser import HTMLParser
import json
import shutil
import subprocess
from unittest.mock import Mock

import pytest


def test_confirmed_media_lifecycle(tmp_path):
    tests = Path(__file__).parent
    node = shutil.which('node')
    package = os.environ.get('KACE_STUDIO_MEDIA_TEST_EXE')
    if not node and not package:
        pytest.skip('Node is required for the frontend regression')
    assert node, 'Node is required to validate the packaged frontend'
    sources = [tests.parent / 'web/app.js']
    if package:
        from PyInstaller.archive.readers import CArchiveReader
        packaged = tmp_path / 'bundled-app.js'
        archive = CArchiveReader(package)
        name, = [name for name in archive.toc if name.replace(chr(92), '/') == 'web/app.js']
        packaged.write_bytes(archive.extract(name))
        sources.append(packaged)
    for source in sources:
        result = subprocess.run([node, tests / 'bootstrap_session_harness.cjs', source, 'media_approval'],
                                text=True, encoding='utf-8', capture_output=True, timeout=15)
        assert result.returncode == 0, f'{source}: {result.stdout}\n{result.stderr}'


def test_bridge_uses_same_disk_for_writer_receipt_and_eject(tmp_path, monkeypatch, coherent_provisioning_candidate):
    import main
    from backend.image_manifest import ResolvedImage
    from tests.test_drive_identity import snapshot
    from tests.test_readback_outcome import evidence
    from types import SimpleNamespace
    api = main.Api()
    first = snapshot(number=3)
    replacement = {**first, "serial_number": "fresh-device"}
    api._drive_snapshots[3] = first
    operation = "f" * 32
    image = tmp_path / "mock.img"
    image.write_bytes(b"test" * 128)
    digest = "b" * 64
    monkeypatch.setattr(api, "_resolve_default_image", lambda _: ResolvedImage(str(image), digest, 512))
    monkeypatch.setattr(api, "_validate_raw_image", lambda _: 512)
    monkeypatch.setattr(main, "application_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(main.shutil, "disk_usage", lambda _: SimpleNamespace(free=16 * 1024**3))
    def sync_thread(*, target, args, daemon):
        return SimpleNamespace(start=lambda: target(*args))
    monkeypatch.setattr(main, "threading", SimpleNamespace(Thread=sync_thread))
    writer = Mock(return_value=(True, evidence(operation, first, digest, 512)))
    monkeypatch.setattr(main, "flash_drive", writer)
    monkeypatch.setattr(main, "inject_config", Mock(return_value=True))
    eject = Mock(return_value={"success": True})
    monkeypatch.setattr(main, "request_safe_eject", eject)
    def start():
        return api.start_flash(3, "default_lite", "printer", "", "", "user-test", "mainsail",
                               drive_identity=first, operation_id=operation)
    api._drive_snapshots[3] = replacement
    assert start() is False
    writer.assert_not_called()
    api._drive_snapshots[3] = first
    assert start() is True
    assert writer.call_args.args[0] == 3 and writer.call_args.args[3] == first
    assert api._completed_flash["operation_id"] == operation
    api._drive_snapshots[3] = replacement
    assert api.eject_drive(3, operation)["success"] is False
    eject.assert_not_called()
    api._drive_snapshots[3] = first
    assert api.eject_drive(3, operation)["success"] is True
    eject.assert_called_once_with(3, first)
    assert api.eject_drive(3, operation)["success"] is False


@pytest.mark.skipif(not os.environ.get('KACE_STUDIO_CHROMIUM'), reason="optional headless Chromium DOM test")
def test_disk_approval_in_native_html_controls(tmp_path):
    """No native bridge is loaded. Chromium uses a disposable local profile."""
    tests = Path(__file__).parent
    assets = tmp_path / "web"
    shutil.copytree(tests.parent / "web", assets)
    package = os.environ.get('KACE_STUDIO_MEDIA_TEST_EXE')
    if package:
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(package)
        for name in archive.toc:
            relative = name.replace(chr(92), '/')
            if relative in ('web/app.js', 'web/index.html'):
                (tmp_path / relative).write_bytes(archive.extract(name))
    html = assets / "index.html"
    scenario = (tests / "media_approval_browser.js").read_text(encoding='utf-8')
    html.write_text(html.read_text(encoding='utf-8').replace("</body>", "<script>" + scenario + "</script></body>"),
                    encoding='utf-8')
    result = subprocess.run([
        os.environ["KACE_STUDIO_CHROMIUM"], "--headless", "--disable-gpu", "--no-first-run",
        "--disable-extensions", "--disable-background-networking", "--disable-sync", "--disable-component-update",
        "--no-default-browser-check", f"--user-data-dir={tmp_path / 'profile'}", "--dump-dom",
        "--virtual-time-budget=4000", html.as_uri(),
    ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    class ResultParser(HTMLParser):
        outcome = ""
        def handle_starttag(self, tag, attrs):
            if tag == "body":
                self.outcome = dict(attrs).get("data-kace-media-test", "")
    parser = ResultParser()
    parser.feed(result.stdout)
    assert result.returncode == 0 and parser.outcome == "passed", json.dumps(
        {"outcome": parser.outcome, "browser": result.stderr[-1500:]}, ensure_ascii=False)
