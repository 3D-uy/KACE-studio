"""Remote listing failures must not be successful empty directories."""
from unittest.mock import Mock
import json
import pytest
import main
from backend.ssh_client import SSHSession


@pytest.mark.parametrize("failure", [PermissionError("denied"), TimeoutError("timeout"), OSError("transport failed")])
def test_listing_failure_is_propagated_and_channel_is_closed(monkeypatch, failure):
    session = SSHSession()
    sftp = Mock()
    sftp.listdir_attr.side_effect = failure
    monkeypatch.setattr(session, "get_sftp", lambda: sftp)
    with pytest.raises(RuntimeError):
        session.list_directory("/denied")
    sftp.close.assert_called_once()


def test_unavailable_sftp_is_not_an_empty_directory():
    with pytest.raises(RuntimeError):
        SSHSession().list_directory("/home/kace")


def test_real_empty_directory_remains_a_success(monkeypatch):
    session = SSHSession()
    sftp = Mock()
    sftp.listdir_attr.return_value = []
    monkeypatch.setattr(session, "get_sftp", lambda: sftp)
    assert session.list_directory("/empty") == []
    sftp.close.assert_called_once()


def test_wsgi_listing_error_is_not_http_success(monkeypatch):
    api = main.Api()
    session = SSHSession()
    monkeypatch.setattr(session, "get_sftp", lambda: None)
    api._ssh = session
    app = main.KaceWsgiApp("web", api)
    status = []
    body = b"".join(app({"PATH_INFO":"/api/sftp/list", "QUERY_STRING":"path=/denied", "HTTP_X_PYWEBVIEW_TOKEN": app._api_token}, lambda value, _headers: status.append(value)))
    assert status == ["500 Internal Server Error"]
    assert "items" not in json.loads(body)
