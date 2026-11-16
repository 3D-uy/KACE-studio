"""Small reusable HTTP client for Moonraker's JSON API."""

import json
import http.client
import ipaddress
import math
import re
import socket
import threading
import time
import urllib.error
import urllib.request


_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$")
# These endpoints return small JSON objects, never files or telemetry streams.
MAX_RESPONSE_BYTES = 1024 * 1024
_DNS_SLOTS = threading.BoundedSemaphore(16)


class _RequestBudget:
    """Bound the whole exchange, including a trickling status line or headers."""

    def __init__(self, timeout, cancelled=None):
        self.deadline = time.monotonic() + timeout
        self.cancelled = cancelled
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.socket = None
        self.expired = False
        self.worker = threading.Thread(target=self._watch, daemon=True,
                                       name="kace-http-deadline")
        self.worker.start()

    def remaining(self):
        if self.cancelled is not None and self.cancelled.is_set():
            raise TimeoutError("Moonraker request cancelled; outcome may be unknown")
        remaining = self.deadline - time.monotonic()
        if self.expired or remaining <= 0:
            raise TimeoutError("Moonraker request deadline exceeded; outcome may be unknown")
        return remaining

    def bind(self, sock):
        with self.lock:
            self.remaining()
            self.socket = sock

    def _watch(self):
        while not self.done.is_set():
            try:
                remaining = self.remaining()
            except TimeoutError:
                with self.lock:
                    self.expired = True
                    sock = self.socket
                # close alone does not interrupt a buffered socket read. Shutdown
                # wakes both headers and body reads on Windows and POSIX.
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return
            self.done.wait(min(remaining, .05) if self.cancelled is not None else remaining)

    def close(self):
        self.done.set()
        with self.lock:
            sock, self.socket = self.socket, None
        if sock is not None:
            sock.close()


def _resolve(host, port, budget):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM,
                                  flags=socket.AI_NUMERICHOST)
    # OS DNS resolution cannot be cancelled portably. Only that read-only call
    # may outlive the caller; cap outstanding resolvers and never send from them.
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        raise OSError("Moonraker DNS resolver capacity exhausted")
    finished = threading.Event()
    outcome = {}
    def resolve():
        try:
            outcome["addresses"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            outcome["error"] = exc
        finally:
            slots.release()
            finished.set()
    try:
        threading.Thread(target=resolve, daemon=True, name="kace-http-dns").start()
    except BaseException:
        slots.release()
        raise
    while not finished.wait(min(budget.remaining(), .05)):
        pass
    budget.remaining()  # A late DNS answer must not initiate a request.
    if "error" in outcome:
        raise outcome["error"]
    return outcome["addresses"]


class _DeadlineConnection(http.client.HTTPConnection):
    def __init__(self, host, *, budget, **kwargs):
        self.budget = budget
        super().__init__(host, **kwargs)

    def connect(self):
        error = None
        for family, kind, protocol, _, address in _resolve(self.host, self.port, self.budget):
            sock = socket.socket(family, kind, protocol)
            try:
                self.budget.bind(sock)
                sock.settimeout(self.budget.remaining())
                sock.connect(address)
                self.budget.remaining()
                self.sock = sock
                return
            except OSError as exc:
                error = exc
                sock.close()
                self.budget.remaining()
        raise error or OSError("Moonraker host has no usable address")

    def send(self, data):
        self.budget.remaining()
        super().send(data)


class _DeadlineHandler(urllib.request.HTTPHandler):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def http_open(self, request):
        def connection(host, **kwargs):
            return _DeadlineConnection(host, budget=self.budget, **kwargs)
        return self.do_open(connection, request)


class _RejectUnbudgetedHttps(urllib.request.HTTPSHandler):
    """An HTTPS proxy must not select urllib's unbounded default transport."""

    def https_open(self, request):
        raise urllib.error.URLError("HTTPS proxy transport is not supported for Moonraker")


class _DeadlineResponse:
    def __init__(self, response, budget):
        self.response, self.budget = response, budget

    def read(self, size):
        self.budget.remaining()
        try:
            read = getattr(self.response, "read1", self.response.read)
            payload = read(size)
        except (OSError, http.client.HTTPException):
            self.budget.remaining()
            raise
        self.budget.remaining()
        remaining = getattr(self.response, "length", None)
        if not payload and isinstance(remaining, int) and remaining > 0:
            raise http.client.IncompleteRead(b"", remaining)
        return payload

    def close(self):
        self.budget.close()
        self.response.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep credentials, methods and results bound to the configured endpoint."""

    def http_error_302(self, request, response, code, message, headers):
        response.close()
        raise urllib.error.HTTPError(
            request.full_url, code, "Moonraker redirects are not allowed", headers, None
        )

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _urlopen(request, timeout):
    budget = _RequestBudget(timeout, getattr(request, "cancel_event", None))
    # Preserve urllib's existing proxy policy, but isolate this deadline handler
    # from image downloads and every other urllib consumer.
    try:
        opener = urllib.request.build_opener(
            _DeadlineHandler(budget), _RejectUnbudgetedHttps(), _RejectRedirects()
        )
        return _DeadlineResponse(opener.open(request, timeout=timeout), budget)
    except urllib.error.HTTPError as exc:
        if exc.fp is not None:
            raise urllib.error.HTTPError(exc.url, exc.code, exc.msg, exc.headers,
                                         _DeadlineResponse(exc, budget)) from exc
        budget.close()
        raise
    except Exception:
        try:
            budget.remaining()
        finally:
            budget.close()
        raise


def _read_json(response):
    payload = bytearray()
    while True:
        block = response.read(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - len(payload)))
        if not block:
            break
        payload.extend(block)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise MoonrakerHttpError("Moonraker response exceeds the size limit")
    return json.loads(payload.decode("utf-8", errors="replace"))


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

    def __init__(self, host: str, port: int = 7125, timeout: float = 3.0, *, cancel_event=None):
        self.host = normalize_target_host(host)
        self.port = int(port)
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("Moonraker timeout must be finite and positive")
        self.cancel_event = cancel_event
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
        request.cancel_event = self.cancel_event
        try:
            with _urlopen(request, timeout=self.timeout) as response:
                body = _read_json(response)
        except urllib.error.HTTPError as exc:
            try:
                error = _read_json(exc)
                detail = error.get("error", {}).get("message") or exc.reason
            except MoonrakerHttpError:
                raise
            except (OSError, http.client.HTTPException) as read_error:
                raise MoonrakerHttpError(f"Invalid Moonraker response: {read_error}") from read_error
            except (ValueError, TypeError, AttributeError, RecursionError):
                detail = exc.reason
            finally:
                exc.close()
            raise MoonrakerHttpError(f"Moonraker HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MoonrakerHttpError(f"Moonraker connection failed: {exc.reason}") from exc
        except (OSError, ValueError, TypeError, RecursionError, http.client.HTTPException) as exc:
            raise MoonrakerHttpError(f"Invalid Moonraker response: {exc}") from exc
        if not isinstance(body, dict):
            raise MoonrakerHttpError("Moonraker returned a non-object JSON response")
        return body

    def get(self, path: str) -> dict:
        return self.request_json("GET", path)

    def post(self, path: str, payload: dict) -> dict:
        return self.request_json("POST", path, payload)
