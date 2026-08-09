from unittest.mock import Mock

from backend.ssh_client import SSHSession


def fake_sftp(stat_effect):
    sftp = Mock()
    sftp.normalize.return_value = "/home/kace"
    sftp.stat.side_effect = stat_effect
    return sftp


def test_remote_file_reader_distinguishes_confirmed_absence(monkeypatch):
    session = SSHSession()
    sftp = fake_sftp(FileNotFoundError(2, "missing"))
    monkeypatch.setattr(session, "get_sftp", lambda: sftp)
    assert session.read_text_file_result(".config/kace/power.json") == ("absent", None)
    sftp.close.assert_called_once()


def test_remote_file_reader_fails_closed_on_permission_error(monkeypatch):
    session = SSHSession()
    sftp = fake_sftp(PermissionError(13, "denied"))
    monkeypatch.setattr(session, "get_sftp", lambda: sftp)
    assert session.read_text_file_result(".config/kace/power.json") == ("error", None)


def test_remote_file_reader_rejects_traversal_without_opening_sftp(monkeypatch):
    session = SSHSession()
    get_sftp = Mock()
    monkeypatch.setattr(session, "get_sftp", get_sftp)
    assert session.read_text_file_result("../power.json") == ("error", None)
    get_sftp.assert_not_called()
