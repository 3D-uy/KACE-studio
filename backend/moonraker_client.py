"""Small reusable HTTP client for Moonraker's JSON API."""

import json
import re
import urllib.error
import urllib.request


_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$")


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep credentials, methods and results bound to the configured endpoint."""

    def http_error_302(self, request, response, code, message, headers):
        response.close()
        raise urllib.error.HTTPError(
            request.full_url, code, "Moonraker redirects are not allowed", headers, None
        )

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _urlopen(request, timeout):
    # A local opener leaves image downloads and other urllib consumers unchanged.
    return urllib.request.build_opener(_RejectRedirects()).open(request, timeout=timeout)


class MoonrakerHttpError(RuntimeError):
    """Raised when Moonraker cannot return a valid JSON response."""


def normalize_target_host(target: str) -> str:
    """Return a validated host, dropping an optional Studio SSH port."""
    if not isinstance(target, str):
        raise ValueError("Moonraker host is missing")
    host = target.strip()
    if ":" in host:
        candidate, suffix = host.rsplit(":", 1)
        if suffix.isdigit():
            host = candidate
    if not host or not _HOST_RE.fullmatch(host):
        raise ValueError("Moonraker host is invalid")
    return host


class MoonrakerHttpClient:
    """Reusable stdlib HTTP transport for one Moonraker instance."""

    def __init__(self, host: str, port: int = 7125, timeout: float = 3.0):
        self.host = normalize_target_host(host)
        self.port = int(port)
        self.timeout = float(timeout)
        self.base_url = f"http://{self.host}:{self.port}"

    def request_json(self, method: str, path: str, payload: dict = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with _urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read().decode("utf-8", errors="replace"))
                detail = error.get("error", {}).get("message") or exc.reason
            except Exception:
                detail = exc.reason
            raise MoonrakerHttpError(f"Moonraker HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MoonrakerHttpError(f"Moonraker connection failed: {exc.reason}") from exc
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise MoonrakerHttpError(f"Invalid Moonraker response: {exc}") from exc
        if not isinstance(body, dict):
            raise MoonrakerHttpError("Moonraker returned a non-object JSON response")
        return body

    def get(self, path: str) -> dict:
        return self.request_json("GET", path)

    def post(self, path: str, payload: dict) -> dict:
        return self.request_json("POST", path, payload)
