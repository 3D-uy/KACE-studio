import base64
import json
import struct
import threading
from unittest.mock import Mock

import pytest

from backend import studio_preflight_bridge as bridge


IDENTITY = {'pin': 'a' * 40, 'engine_sha256': 'b' * 64,
            'dockerfile_sha256': 'c' * 64, 'worker_sha256': 'd' * 64}


class Channel:
    def __init__(self, body):
        self.body = body
        self.output = b''
        self.closed = threading.Event()

    def recv(self, count):
        value, self.body = self.body[:min(count, 17)], self.body[min(count, 17):]
        return value

    def sendall(self, data):
        self.output += data

    def close(self):
        self.closed.set()

    def settimeout(self, _value):
        pass


@pytest.fixture
def service():
    transport = Mock()
    transport.is_active.return_value = True
    transport.request_port_forward.return_value = 4567
    validator = Mock(identity=IDENTITY)
    validator.validate_files.side_effect = lambda files: {
        'status': 'valid', 'detail': 'bounded proof', 'tree_digest': bridge.tree_digest(files)}
    result = bridge.StudioPreflightBridge(transport, validator)
    result.start()
    yield result
    result.close()


def request(service, **overrides):
    files = {'printer.cfg': b'[include child.cfg]\n', 'child.cfg': b'# child\n'}
    value = dict(IDENTITY, schema=bridge.SCHEMA,
                 session=service.session, request_id='e' * 32,
                 tree_digest=bridge.tree_digest(files),
                 files=[{'path': k, 'content_base64': base64.b64encode(v).decode()} for k, v in files.items()])
    value.update(overrides)
    value.setdefault('auth', bridge.authentication(value, service.token, b'request'))
    return value


def exchange(service, payload):
    data = json.dumps(payload).encode()
    channel = Channel(struct.pack('!I', len(data)) + data)
    service.handle_channel(channel, ('127.0.0.1', 6000), ('127.0.0.1', service.port))
    assert channel.closed.wait(3)
    if not channel.output:
        return None
    size = struct.unpack('!I', channel.output[:4])[0]
    assert size == len(channel.output[4:])
    response = json.loads(channel.output[4:])
    assert response['auth'] == bridge.authentication(response, service.token, b'response')
    return response


def test_valid_response_is_bound_and_contains_no_capability(service):
    reply = exchange(service, request(service))
    assert reply['status'] == 'valid'
    assert reply['session'] == service.session
    assert 'token' not in reply
    assert service.token not in json.dumps(reply)
    assert service.transport.request_port_forward.call_args.args[:2] == ('127.0.0.1', 0)


@pytest.mark.parametrize('field,value', [
    ('auth', 'f' * 64), ('session', 'f' * 32), ('schema', 'unknown'),
    ('pin', 'f' * 40), ('worker_sha256', 'f' * 64), ('engine_sha256', 'f' * 64),
    ('dockerfile_sha256', 'f' * 64), ('request_id', ''), ('tree_digest', 12),
])
def test_wrong_context_never_invokes_validator(service, field, value):
    assert exchange(service, request(service, **{field: value})) is None
    service.validator.validate_files.assert_not_called()


def test_replay_and_unexpected_fields_are_rejected(service):
    original = request(service)
    assert exchange(service, dict(original, unexpected='field')) is None
    assert exchange(service, original)['status'] == 'valid'
    assert exchange(service, original) is None
    assert service.validator.validate_files.call_count == 1


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'C:/file', 'a\\b', 'CON', 'x/NUL.cfg', 'a/.', 'a//b', 'a.'])
def test_pc_unsafe_paths_cannot_be_staged(path):
    with pytest.raises(ValueError):
        bridge.decode_files([{'path': path, 'content_base64': ''}])


@pytest.mark.parametrize('names', [('printer.cfg', 'PRINTER.CFG'), ('printer.cfg', 'printer.cfg/a'), ('printer.cfg', 'dir', 'DIR/a')])
def test_collisions_cannot_be_staged(names):
    with pytest.raises(ValueError):
        bridge.decode_files([{'path': path, 'content_base64': ''} for path in names])


def test_digest_change_cannot_be_approved(service):
    payload = request(service)
    payload['files'][1]['content_base64'] = base64.b64encode(b'# changed').decode()
    payload['auth'] = bridge.authentication(payload, service.token, b'request')
    assert exchange(service, payload)['status'] == 'unverified'
    service.validator.validate_files.assert_not_called()


def test_local_result_must_match_tree(service):
    service.validator.validate_files.side_effect = lambda _: {'status': 'valid', 'tree_digest': '0' * 64, 'detail': 'bad'}
    assert exchange(service, request(service))['status'] == 'unverified'


def test_disconnect_during_docker_suppresses_result(service):
    entered, release = threading.Event(), threading.Event()
    def validate(files):
        entered.set()
        assert release.wait(3)
        return {'status': 'valid', 'tree_digest': bridge.tree_digest(files), 'detail': 'late'}
    service.validator.validate_files.side_effect = validate
    payload = json.dumps(request(service)).encode()
    channel = Channel(struct.pack('!I', len(payload)) + payload)
    service.handle_channel(channel, ('127.0.0.1', 6000), ('127.0.0.1', service.port))
    try:
        assert entered.wait(3)
        service.close()
    finally:
        release.set()
    assert channel.closed.wait(3)
    assert not channel.output


def test_oversize_frame_never_reads_body_or_runs_docker(service):
    channel = Channel(struct.pack('!I', bridge.MAX_FRAME + 1))
    service.handle_channel(channel, ('127.0.0.1', 6000), ('127.0.0.1', service.port))
    assert channel.closed.wait(3)
    assert not channel.output
    service.validator.validate_files.assert_not_called()


def test_non_loopback_forward_target_rejected(service):
    channel = Channel(b'')
    service.handle_channel(channel, ('127.0.0.1', 6000), ('0.0.0.0', service.port))
    assert channel.closed.is_set()
    service.validator.validate_files.assert_not_called()


def test_wire_request_never_contains_private_capability(service):
    payload = request(service)
    assert 'token' not in payload
    assert service.token not in json.dumps(payload)
    assert exchange(service, payload)['status'] == 'valid'


def test_request_mac_cannot_be_used_as_response_mac(service):
    payload = request(service)
    reply = exchange(service, payload)
    assert reply['auth'] != payload['auth']
    assert bridge.authentication(reply, service.token, b'request') != reply['auth']


def test_unauthenticated_content_change_never_reaches_docker(service):
    payload = request(service)
    payload['files'][0]['content_base64'] = base64.b64encode(b'# substituted').decode()
    assert exchange(service, payload) is None
    service.validator.validate_files.assert_not_called()
