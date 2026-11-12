"""Reject incompatible packaging tools before PyInstaller analyzes sources."""
from pathlib import Path
import runpy
import sys

import pytest

from scripts import release


ROOT = Path(__file__).resolve().parents[1]


class AnalysisReached(Exception):
    pass


def run_spec():
    def analysis(*args, **kwargs):
        raise AnalysisReached("source analysis started")

    runpy.run_path(str(ROOT / "main.spec"), init_globals={
        "SPECPATH": str(ROOT), "Analysis": analysis,
    })


@pytest.mark.parametrize("tool", ["python", "pyinstaller"])
def test_spec_rejects_wrong_toolchain_before_analysis(monkeypatch, tool):
    contract = release.load_contract()
    monkeypatch.setattr(release.platform, "python_version",
                        lambda: "3.12.14" if tool == "python" else contract["python"])
    monkeypatch.setattr(release.importlib.metadata, "version",
                        lambda name: "0.0.0" if tool == "pyinstaller" else contract["pyinstaller"])
    with pytest.raises(release.ReleaseContractError, match="toolchain mismatch"):
        run_spec()


def test_pinned_toolchain_versions_are_returned(monkeypatch):
    contract = release.load_contract()
    monkeypatch.setattr(release.platform, "python_version", lambda: contract["python"])
    monkeypatch.setattr(release.importlib.metadata, "version", lambda name: contract["pyinstaller"])
    assert release.verify_toolchain(contract) == (contract["python"], contract["pyinstaller"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PE metadata is required after the gate")
def test_spec_allows_pinned_toolchain_to_reach_analysis(monkeypatch):
    contract = release.load_contract()
    monkeypatch.setattr(release.platform, "python_version", lambda: contract["python"])
    monkeypatch.setattr(release.importlib.metadata, "version", lambda name: contract["pyinstaller"])
    with pytest.raises(AnalysisReached):
        run_spec()
