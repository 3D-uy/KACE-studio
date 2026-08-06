"""Tests for the repository portability guard."""

from scripts.check_portability import scan_text


def test_rejects_developer_profiles_and_workspaces():
    separator = chr(92)
    samples = (
        "C:" + separator + "Users" + separator + "developer" + separator + "repo",
        "/" + "Users/developer/repo",
        "/" + "home/developer/repo",
        "D:" + separator + "Open" + " World" + separator + "project",
    )
    for sample in samples:
        assert scan_text(sample)


def test_allows_target_runtime_and_generic_paths():
    separator = chr(92)
    samples = (
        "/home/kace/printer_data/config",
        "/home/pi/klipper",
        "C:" + separator + "Downloads" + separator + "image.img",
        "/tmp/kace-download.part",
    )
    for sample in samples:
        assert scan_text(sample) == []
