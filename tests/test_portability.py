"""Tests for the repository portability guard."""

from scripts.check_portability import scan_text


def test_rejects_developer_profiles_and_workspaces():
    separator = chr(92)
    samples = (
        "C:" + separator + "Users" + separator + "fixture-user" + separator + "repo",
        "/" + "Users/fixture-user/repo",
        "/" + "home/fixture-user/repo",
        "D:" + separator + "fixture-workspace" + separator + "project",
        "C:" + separator * 2 + "Users" + separator * 2 + "fixture-user",
        "c:/" + "users/fixture-user/repo",
        "C." + "Users.fixture-user.repo",
        "D:" + "/fixture-workspace/project",
        separator + "home" + separator + "fixture-user" + separator + "repo",
        "/" + "home/user.name/repo",
        "/" + "Desktop/fixture.txt",
        "." + "Documents.fixture.txt",
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


def test_scans_all_tracked_locations_including_security_fixtures(tmp_path, monkeypatch):
    from scripts import check_portability

    paths = [tmp_path / name for name in (
        "build/example.txt", "dist/example.txt",
        "tests/test_portability.py", "docs/example.md",
    )]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("D:" + "/fixture-workspace/file", encoding="utf-8")
    monkeypatch.setattr(check_portability, "_tracked_files", lambda root: paths)
    findings = check_portability.find_violations(tmp_path)
    assert len(findings) == len(paths)
    for path in paths:
        assert any(str(path.relative_to(tmp_path)) in line for line in findings)
