import json
from unittest.mock import Mock
import main

import pytest

from main import Api, HTTP_TIMEOUT_SECONDS, KaceWsgiApp


def _call_wsgi(app, path, method="GET", query="", **headers):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app({
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "REQUEST_METHOD": method,
        **headers,
    }, start_response))
    return captured, body


@pytest.mark.parametrize("token", [None, "wrong", "évil"])
def test_sftp_requires_bridge_session_token_before_touching_ssh(tmp_path, token):
    api = Api()
    api._ssh.list_directory = Mock(return_value=[])
    headers = {} if token is None else {"HTTP_X_PYWEBVIEW_TOKEN": token}
    response, body = _call_wsgi(KaceWsgiApp(str(tmp_path), api), '/api/sftp/list', **headers)
    assert response['status'] == '403 Forbidden'
    assert json.loads(body) == {'error': 'Forbidden'}
    api._ssh.list_directory.assert_not_called()


def test_sftp_accepts_bridge_token_and_never_caches_listing(tmp_path):
    api = Api()
    api._ssh.list_directory = Mock(return_value=[])
    response, body = _call_wsgi(KaceWsgiApp(str(tmp_path), api), '/api/sftp/list',
                               HTTP_X_PYWEBVIEW_TOKEN=main.webview.token)
    assert response['status'] == '200 OK'
    assert response['headers']['Cache-Control'] == 'no-store'
    assert json.loads(body)['items'] == []
    api._ssh.list_directory.assert_called_once_with('/home/kace')


def test_static_sibling_prefix_cannot_escape_web_root(tmp_path):
    web_root = tmp_path / "web"
    sibling = tmp_path / "web-secret"
    web_root.mkdir()
    sibling.mkdir()
    (web_root / "index.html").write_text("ok", encoding="utf-8")
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")

    headers, body = _call_wsgi(
        KaceWsgiApp(str(web_root), Api()),
        "/../web-secret/secret.txt",
    )

    assert headers["status"] == "403 Forbidden"
    assert body == b"Forbidden"


def test_sftp_route_rejects_non_get_methods(tmp_path):
    app = KaceWsgiApp(str(tmp_path), Api())

    headers, body = _call_wsgi(app, "/api/sftp/list", method="POST")

    assert headers["status"] == "405 Method Not Allowed"
    assert headers["headers"]["Allow"] == "GET"
    assert json.loads(body) == {"error": "Method not allowed"}


def test_sftp_route_sanitizes_internal_paths(tmp_path):
    api = Api()
    secret_path = tmp_path / "private" / "key"
    api._ssh.list_directory = lambda _path: (_ for _ in ()).throw(
        RuntimeError(f"failed at {secret_path}")
    )

    headers, body = _call_wsgi(
        KaceWsgiApp(str(tmp_path), api),
        "/api/sftp/list",
        query="path=/home/kace",
        HTTP_X_PYWEBVIEW_TOKEN=main.webview.token,
    )

    assert headers["status"] == "500 Internal Server Error"
    assert str(secret_path) not in json.loads(body)["error"]
    assert "Protected Path" in json.loads(body)["error"]


def test_pinned_image_download_uses_bounded_http_timeout(monkeypatch, tmp_path):
    calls = []
    payload = b"pinned payload"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def info(self):
            return {"Content-Length": str(len(payload))}

        def read(self, _size):
            if getattr(self, "done", False):
                return b""
            self.done = True
            return payload

    def fake_urlopen(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("shutil.disk_usage", lambda _path: type("Usage", (), {"free": 6 * 1024**3})())

    Api()._download_os_image(
        "https://example.invalid/image.img.xz",
        str(tmp_path / "image.img.xz"),
        str(tmp_path / "image.img.xz.sha256"),
        __import__("hashlib").sha256(payload).hexdigest(),
        "https://example.invalid/image.img.xz",
        "arm64",
    )

    assert calls
    assert calls[0][1]["timeout"] == HTTP_TIMEOUT_SECONDS
    assert HTTP_TIMEOUT_SECONDS > 0


def test_automatic_download_rejects_missing_checksum_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")),
    )

    with pytest.raises(ValueError, match="pinned SHA-256"):
        Api()._download_os_image(
            "https://example.invalid/image.img.xz",
            str(tmp_path / "image.img.xz"),
            str(tmp_path / "image.img.xz.sha256"),
            "",
            "https://example.invalid/image.img.xz",
            "arm64",
        )
