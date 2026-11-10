"""Session-scoped preflight over SSH reverse forwarding; no PC network listener.

The SSH server is asked to bind loopback only. A random capability also protects
the endpoint from other connections on that host. It is never printed in the
terminal. Validation covers the supplied tree, not the live printer environment.
"""
import base64
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets
import struct
import threading
import time
import uuid


SCHEMA = 'kace-studio-preflight/v1'
MAX_FRAME = 32 * 1024 * 1024
MAX_RESPONSE = 64 * 1024
MAX_FILES = 128
MAX_CONTENT = 16 * 1024 * 1024
FORWARD_TIMEOUT = 10.0
REQUEST_TIMEOUT = 15.0
IDENTITY_KEYS = {'pin', 'engine_sha256', 'dockerfile_sha256', 'worker_sha256'}
REQUEST_KEYS = IDENTITY_KEYS | {'schema', 'token', 'session', 'request_id', 'tree_digest', 'files'}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def _recv_exact(channel, size, deadline):
    data = bytearray()
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('preflight request deadline')
        channel.settimeout(remaining)
        part = channel.recv(size - len(data))
        if not part:
            raise EOFError('preflight channel closed')
        data.extend(part)
    return bytes(data)


def tree_digest(files):
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        encoded = name.encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def decode_files(records):
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_FILES:
        raise ValueError('unsupported configuration file count')
    files, folded, total = {}, set(), 0
    for record in records:
        if not isinstance(record, dict) or set(record) != {'path', 'content_base64'}:
            raise ValueError('invalid file entry')
        name, encoded = record['path'], record['content_base64']
        if not isinstance(name, str) or not isinstance(encoded, str):
            raise ValueError('invalid file types')
        parts = name.split('/')
        if (not name or len(name) > 4096 or PurePosixPath(name).is_absolute()
                or '\\' in name or ':' in name or any(ord(c) < 32 for c in name)
                or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in parts)
                or any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', p.split('.')[0], re.I) for p in parts)):
            raise ValueError('configuration path cannot be staged on PC')
        key = name.casefold()
        if key in folded:
            raise ValueError('duplicate or case-colliding configuration path')
        folded.add(key)
        content = base64.b64decode(encoded, validate=True)
        total += len(content)
        if total > MAX_CONTENT:
            raise ValueError('configuration tree exceeds transport limit')
        files[name] = content
    if 'printer.cfg' not in files:
        raise ValueError('printer.cfg missing')
    for name in files:
        if any(p.as_posix().casefold() in folded for p in PurePosixPath(name).parents if str(p) != '.'):
            raise ValueError('configuration file/directory collision')
    return files


class StudioPreflightBridge:
    def __init__(self, transport, validator):
        self.transport = transport
        self.validator = validator
        self.identity = dict(validator.identity)
        if set(self.identity) != IDENTITY_KEYS or any(
            not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}' if key == 'pin' else '[0-9a-f]{64}', value)
            for key, value in self.identity.items()
        ):
            raise ValueError('invalid local validator identity')
        self.token = secrets.token_hex(32)
        self.session = uuid.uuid4().hex
        self.port = None
        self._closed = threading.Event()
        self._busy = threading.Lock()
        self._state_lock = threading.RLock()
        self._channels = set()
        self._requests = set()

    def start(self):
        if self.port is not None or self._closed.is_set() or not self.transport.is_active():
            raise RuntimeError('preflight SSH session unavailable')
        def expire():
            self._closed.set()
            self.transport.close()
        timer = threading.Timer(FORWARD_TIMEOUT, expire)
        timer.daemon = True
        timer.start()
        try:
            port = self.transport.request_port_forward('127.0.0.1', 0, self.handle_channel)
            if self._closed.is_set() or type(port) is not int or not 1 <= port <= 65535:
                raise RuntimeError('preflight forwarding unavailable')
            self.port = port
        finally:
            timer.cancel()
        return {'KACE_STUDIO_PREFLIGHT_PORT': str(self.port),
                'KACE_STUDIO_PREFLIGHT_TOKEN': self.token,
                'KACE_STUDIO_PREFLIGHT_SESSION': self.session}

    def close(self):
        self._closed.set()
        with self._state_lock:
            channels = tuple(self._channels)
        for channel in channels:
            try:
                channel.close()
            except Exception:
                pass
        port, self.port = self.port, None
        if port is not None and self.transport.is_active():
            # Never block SSH/session teardown on a global-request acknowledgement.
            def cancel():
                timer = threading.Timer(FORWARD_TIMEOUT, self.transport.close)
                timer.daemon = True
                timer.start()
                try:
                    self.transport.cancel_port_forward('127.0.0.1', port)
                except Exception:
                    pass
                finally:
                    timer.cancel()
            threading.Thread(target=cancel, daemon=True, name='kace-preflight-cancel').start()

    def handle_channel(self, channel, origin, server):
        # Paramiko invokes this on its transport thread: no reads or Docker here.
        with self._state_lock:
            if (self._closed.is_set() or server != ('127.0.0.1', self.port)
                    or not self._busy.acquire(blocking=False)):
                channel.close()
                return
            self._channels.add(channel)
        threading.Thread(target=self._serve, args=(channel,), daemon=True,
                         name='kace-studio-preflight').start()

    def _serve(self, channel):
        try:
            deadline = time.monotonic() + REQUEST_TIMEOUT
            size = struct.unpack('!I', _recv_exact(channel, 4, deadline))[0]
            if not 0 < size <= MAX_FRAME:
                return
            request = json.loads(_recv_exact(channel, size, deadline).decode('utf-8'), object_pairs_hook=_unique_object)
            if (not isinstance(request, dict) or set(request) != REQUEST_KEYS
                    or request['schema'] != SCHEMA or request['session'] != self.session
                    or not isinstance(request['token'], str)
                    or not secrets.compare_digest(request['token'], self.token)
                    or any(request[key] != self.identity[key] for key in IDENTITY_KEYS)
                    or not isinstance(request['request_id'], str)
                    or not re.fullmatch('[0-9a-f]{32}', request['request_id'])
                    or not isinstance(request['tree_digest'], str)
                    or not re.fullmatch('[0-9a-f]{64}', request['tree_digest'])):
                return
            if request['request_id'] in self._requests or len(self._requests) >= 1024:
                return
            self._requests.add(request['request_id'])
            reply = {key: request[key] for key in IDENTITY_KEYS | {'schema', 'session', 'request_id', 'tree_digest'}}
            try:
                files = decode_files(request['files'])
                if tree_digest(files) != request['tree_digest']:
                    raise ValueError('configuration tree digest differs')
                if self._closed.is_set() or not self.transport.is_active():
                    return
                result = self.validator.validate_files(files)
                if (result.get('status') not in ('valid', 'invalid', 'unverified')
                        or result.get('tree_digest') != request['tree_digest']
                        or not isinstance(result.get('detail'), str)):
                    raise ValueError('local validator returned an unmatched result')
                reply.update(status=result['status'], detail=result['detail'][:4000])
            except Exception:
                # Do not expose bearer data, request content, paths or exception echoes.
                reply.update(status='unverified', detail='Studio could not validate this configuration tree.')
            payload = json.dumps(reply, separators=(',', ':')).encode('utf-8')
            if len(payload) > MAX_RESPONSE:
                return
            with self._state_lock:
                if self._closed.is_set() or not self.transport.is_active():
                    return
                channel.settimeout(5)
                channel.sendall(struct.pack('!I', len(payload)) + payload)
        except Exception:
            pass  # EOF/malformed frame is unverified at the caller; never approve.
        finally:
            with self._state_lock:
                self._channels.discard(channel)
            try:
                channel.close()
            finally:
                self._busy.release()
