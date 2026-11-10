"""Real encrypted Paramiko forwarding, entirely local and without Docker/Pi.

The fixture authenticates with a synthetic password and an ephemeral pinned host
key. It accepts only loopback forwarding and never permits command execution.
"""
import base64
import json
import secrets
import socket
import struct
import threading
import uuid

import paramiko
import pytest

from backend.studio_preflight_bridge import SCHEMA, StudioPreflightBridge, tree_digest


IDENTITY = {"pin": "a" * 40, "engine_sha256": "b" * 64,
            "dockerfile_sha256": "c" * 64, "worker_sha256": "d" * 64}
FILES = {"printer.cfg": b"[include user.cfg]\n", "user.cfg": b"# unchanged\x00\xff\r\n"}


class ForwardingServer(paramiko.ServerInterface):
    """Minimal SSH server: authenticate, then forward local sockets as channels."""
    def __init__(self, password):
        self.password = password
        self.transport = None
        self.stop = threading.Event()
        self.authenticated = threading.Event()
        self.cancelled = threading.Event()
        self.listeners = []
        self.connections = []
        self.threads = []
        self.requests = []
        self.errors = []

    def check_auth_password(self, username, password):
        if username == "synthetic-preflight-test" and secrets.compare_digest(password, self.password):
            self.authenticated.set()
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_port_forward_request(self, address, port):
        self.requests.append((address, port))
        if address != "127.0.0.1" or port != 0:
            return False
        listener = socket.socket()
        listener.bind((address, 0))
        listener.listen(4)
        listener.settimeout(0.1)
        self.listeners.append(listener)
        allocated = listener.getsockname()[1]
        thread = threading.Thread(target=self._accept, args=(listener, allocated), daemon=True)
        self.threads.append(thread)
        thread.start()
        return allocated

    def cancel_port_forward_request(self, address, port):
        assert address == "127.0.0.1"
        self.cancelled.set()
        for listener in self.listeners:
            try:
                if listener.getsockname() == (address, port):
                    listener.close()
            except OSError:
                pass

    def _accept(self, listener, port):
        while not self.stop.is_set():
            try:
                incoming, origin = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                # Paramiko documents src_addr as originator and dest_addr as
                # the listening server endpoint; reversing these breaks the
                # bridge's server-address/session guard.
                channel = self.transport.open_forwarded_tcpip_channel(origin, ("127.0.0.1", port))
                incoming.settimeout(3)
                channel.settimeout(3)
                self.connections.extend((incoming, channel))
                for source, target in ((incoming, channel), (channel, incoming)):
                    thread = threading.Thread(target=self._pipe, args=(source, target), daemon=True)
                    self.threads.append(thread)
                    thread.start()
            except (OSError, paramiko.SSHException, EOFError) as exc:
                incoming.close()
                if not self.stop.is_set() and self.transport.is_active():
                    self.errors.append(exc)

    def _pipe(self, source, target):
        try:
            while not self.stop.is_set():
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except (OSError, paramiko.SSHException, EOFError):
            pass  # Closing the SSH session intentionally interrupts both pipes.
        finally:
            source.close()
            target.close()

    def close(self):
        self.stop.set()
        for connection in [*self.listeners, *self.connections]:
            connection.close()
        if self.transport:
            self.transport.close()
        for thread in self.threads:
            thread.join(timeout=2)
        assert not any(thread.is_alive() for thread in self.threads)
        assert not self.errors


@pytest.fixture(scope="module")
def ephemeral_host_key():
    return paramiko.RSAKey.generate(2048)


@pytest.fixture
def ssh_transport(ephemeral_host_key):
    server = ForwardingServer(secrets.token_hex(24))
    client_transport = None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        client_socket = socket.create_connection(listener.getsockname(), timeout=3)
        server_socket, _ = listener.accept()
    try:
        server.transport = paramiko.Transport(server_socket)
        server.transport.add_server_key(ephemeral_host_key)
        negotiation = threading.Event()
        server.transport.start_server(event=negotiation, server=server)
        client_transport = paramiko.Transport(client_socket)
        client_transport.connect(hostkey=ephemeral_host_key, username="synthetic-preflight-test", password=server.password)
        assert negotiation.wait(3)
        assert server.authenticated.wait(3)
        assert client_transport.is_authenticated()
        assert client_transport.local_cipher and client_transport.remote_cipher
        yield client_transport, server
    finally:
        if client_transport:
            client_transport.close()
        client_socket.close()
        server.close()


class Validator:
    identity = IDENTITY

    def __init__(self, status="valid", *, block=False):
        self.status = status
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        if not block:
            self.release.set()

    def validate_files(self, files):
        self.calls.append(files)
        self.entered.set()
        assert self.release.wait(3), "test must release the synthetic validator"
        self.finished.set()
        return {"status": self.status, "detail": "synthetic validator result", "tree_digest": tree_digest(files)}


def request(service, **changes):
    return {
        "schema": SCHEMA, "token": service.token, "session": service.session,
        "request_id": uuid.uuid4().hex, **IDENTITY,
        "tree_digest": tree_digest(FILES),
        "files": [{"path": name, "content_base64": base64.b64encode(content).decode("ascii")}
                  for name, content in FILES.items()], **changes,
    }


def send(connection, payload):
    data = json.dumps(payload).encode()
    connection.sendall(struct.pack("!I", len(data)) + data)


def receive(connection):
    def exact(count):
        parts = bytearray()
        while len(parts) < count:
            chunk = connection.recv(count - len(parts))
            if not chunk:
                raise EOFError("forwarded validation channel closed")
            parts.extend(chunk)
        return bytes(parts)
    size = struct.unpack("!I", exact(4))[0]
    assert 0 < size <= 64 * 1024
    return json.loads(exact(size))


@pytest.mark.parametrize("status", ["valid", "invalid"])
def test_real_encrypted_forwarding_preserves_server_address_and_result(ssh_transport, status):
    transport, server = ssh_transport
    validator = Validator(status)
    bridge = StudioPreflightBridge(transport, validator)
    try:
        environment = bridge.start()
        assert server.requests == [("127.0.0.1", 0)]
        assert all(listener.getsockname()[0] == "127.0.0.1" for listener in server.listeners)
        with socket.create_connection(("127.0.0.1", int(environment["KACE_STUDIO_PREFLIGHT_PORT"])), timeout=3) as connection:
            original = request(bridge)
            send(connection, original)
            result = receive(connection)
        assert result["status"] == status
        assert validator.calls == [FILES]
        for key in {"schema", "session", "request_id", "tree_digest", *IDENTITY}:
            assert result[key] == original[key]
        assert bridge.token not in json.dumps(result)
    finally:
        bridge.close()
    assert server.cancelled.wait(3)


@pytest.mark.parametrize("closure", ["bridge", "ssh"])
def test_closing_session_during_validation_never_returns_approval(ssh_transport, closure):
    transport, _server = ssh_transport
    validator = Validator(block=True)
    bridge = StudioPreflightBridge(transport, validator)
    try:
        bridge.start()
        with socket.create_connection(("127.0.0.1", bridge.port), timeout=3) as connection:
            send(connection, request(bridge))
            assert validator.entered.wait(3)
            if closure == "bridge":
                bridge.close()
            else:
                transport.close()
            validator.release.set()
            assert validator.finished.wait(3)
            with pytest.raises((EOFError, OSError)):
                receive(connection)
        assert validator.calls == [FILES]
    finally:
        validator.release.set()
        bridge.close()


def test_wrong_session_over_real_forwarding_never_calls_validator(ssh_transport):
    transport, _server = ssh_transport
    validator = Validator()
    bridge = StudioPreflightBridge(transport, validator)
    try:
        bridge.start()
        with socket.create_connection(("127.0.0.1", bridge.port), timeout=3) as connection:
            send(connection, request(bridge, session="0" * 32))
            with pytest.raises((EOFError, OSError)):
                receive(connection)
        assert not validator.calls
    finally:
        bridge.close()
