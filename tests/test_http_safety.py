import json

from main import Api, HTTP_TIMEOUT_SECONDS, KaceWsgiApp


def _call_wsgi(app, path, method="GET", query=""):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app({
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "REQUEST_METHOD": method,
    }, start_response))
    return captured, body


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
    )

    assert headers["status"] == "500 Internal Server Error"
    assert str(secret_path) not in json.loads(body)["error"]
    assert "Protected Path" in json.loads(body)["error"]


def test_github_release_query_uses_bounded_http_timeout(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "assets": [{
                    "name": "image-rpi-arm64.img.xz",
                    "browser_download_url": "https://example.invalid/image.img.xz",
                }]
            }).encode()

    def fake_urlopen(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    Api()._get_latest_github_release_asset("owner/repo", "64bit")

    assert calls
    assert calls[0][1]["timeout"] == HTTP_TIMEOUT_SECONDS
    assert HTTP_TIMEOUT_SECONDS > 0
