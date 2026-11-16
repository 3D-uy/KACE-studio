"""Bounded read-only remote receipts; SFTP and printer remain simulated."""
import hashlib
import io
import json
import stat
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend import kace_configuration as config
from test_studio_configuration import prepare
from test_studio_resume import resume


CHECKPOINT = 'd' * 64
ROOT = '/home/kace/kace/configuration-operations'


class Sftp:
    def __init__(self):
        self.entries = []
        self.files = {}
        self.modes = {ROOT: stat.S_IFDIR}
        self.closed = False
        self.reads = []
        self.visited = 0
        self.channel = Mock()

    def normalize(self, path):
        return '/home/kace' if path == '.' else path

    def lstat(self, path):
        if path not in self.modes:
            raise FileNotFoundError(2, 'missing')
        return SimpleNamespace(st_mode=self.modes[path], st_size=len(self.files.get(path, b'')))

    def listdir_iter(self, path, read_aheads=1):
        assert path == ROOT and read_aheads == 1
        for item in self.entries:
            self.visited += 1
            yield item

    def open(self, path, mode):
        assert mode == 'rb'
        self.reads.append(path)
        return io.BytesIO(self.files[path])

    def close(self):
        self.closed = True

    def get_channel(self):
        return self.channel

    def add(self, identity, **changes):
        directory = ROOT + '/' + identity
        self.entries.append(SimpleNamespace(filename=identity, st_mode=stat.S_IFDIR, st_mtime=999999))
        self.modes[directory] = stat.S_IFDIR
        manifest = json.dumps({'printer.cfg':'a'*64, 'printer.cfg.provenance.json':'b'*64}).encode()
        receipt = dict(schema='kace-configuration-operation/v1', operation_id=identity,
            source_checkpoint_sha256=CHECKPOINT, sequence=3, state='FAILED', exit_code=10,
            generated_manifest_sha256=hashlib.sha256(manifest).hexdigest())
        receipt.update(changes)
        for name, value in [('generated.json', manifest), ('result.json', json.dumps(receipt).encode())]:
            path = directory + '/' + name
            self.files[path], self.modes[path] = value, stat.S_IFREG
        return receipt


def discover(sftp, **kwargs):
    return config.discover_configuration_operations(SimpleNamespace(get_sftp=lambda: sftp), CHECKPOINT, **kwargs)


def test_multiple_operations_are_read_only_and_not_ordered_by_mtime():
    sftp = Sftp()
    sftp.add('c'*32)
    sftp.add('a'*32, state='COMPLETE', exit_code=0)
    sftp.add('b'*32, generated_manifest_sha256=None)
    result = discover(sftp)
    assert [item['operation_id'] for item in result['operations']] == ['a'*32,'b'*32,'c'*32]
    assert [item['recoverable'] for item in result['operations']] == [False,False,True]
    assert result['operations'][0]['reason'] == 'complete'
    assert result['operations'][1]['reason'] == 'manifest_unavailable'
    assert len(result['operations'][2]['result_sha256']) == 64
    assert sftp.closed and not result['truncated']


@pytest.mark.parametrize('change', [{'operation_id':'other'}, {'source_checkpoint_sha256':'e'*64},
    {'sequence':True}, {'state':'unknown'}, {'schema':'old'}, {'exit_code':0}])
def test_wrong_or_stale_receipts_are_rejected(change):
    sftp = Sftp()
    sftp.add('a'*32, **change)
    result = discover(sftp)
    assert result['operations'] == [] and result['rejected'] == 1
    assert sftp.closed


@pytest.mark.parametrize('kind', ['name', 'directory_symlink', 'receipt_symlink', 'manifest_symlink', 'manifest_hash'])
def test_discovery_does_not_walk_malformed_names_or_symlinks(kind):
    sftp = Sftp()
    sftp.add('a'*32)
    if kind == 'name': sftp.entries[0].filename = '../outside'
    if kind == 'directory_symlink': sftp.modes[ROOT+'/'+ 'a'*32] = stat.S_IFLNK
    if kind == 'receipt_symlink': sftp.modes[ROOT+'/'+ 'a'*32+'/result.json'] = stat.S_IFLNK
    if kind == 'manifest_symlink': sftp.modes[ROOT+'/'+ 'a'*32+'/generated.json'] = stat.S_IFLNK
    if kind == 'manifest_hash': sftp.files[ROOT+'/'+ 'a'*32+'/generated.json'] = b'{}'
    result = discover(sftp)
    assert not any(item['recoverable'] for item in result['operations'])
    assert not any('outside' in path for path in sftp.reads)
    assert sftp.closed


def test_enumeration_limit_is_reported_not_silently_discarded():
    sftp = Sftp()
    for number in range(200):
        sftp.entries.append(SimpleNamespace(filename='invalid-'+str(number), st_mode=stat.S_IFDIR))
    result = discover(sftp)
    assert result['truncated'] is True
    assert sftp.visited <= 129
    assert sftp.closed


def test_api_discovery_survives_empty_local_state_and_rechecks_session(resume, monkeypatch):
    api, session, checkpoint, engine = prepare(resume)
    context = {'host':'pi.local','selection':1,'session':7}
    def remote(*args, **kwargs):
        return {'operations':[], 'rejected':0, 'truncated':False}
    monkeypatch.setattr(config, 'discover_configuration_operations', remote, raising=False)
    result = api.inspect_kace_configurations(checkpoint['workflow_id'], checkpoint['sequence'], 7, context)
    assert result['status'] == 'ok' and result['operations'] == []
    engine.prepare.assert_not_called()
    assert session.commands == []
    def changed(*args, **kwargs):
        api._ssh_gen += 1
        return remote()
    monkeypatch.setattr(config, 'discover_configuration_operations', changed)
    assert api.inspect_kace_configurations(checkpoint['workflow_id'], checkpoint['sequence'], 7, context)['status'] == 'failed'
    assert session.commands == []


def test_resume_rejects_receipt_changed_since_explicit_selection(resume):
    api, session, checkpoint, engine = prepare(resume)
    raw = json.dumps(checkpoint)
    receipt = json.dumps(dict(schema='kace-configuration-operation/v1', operation_id='c'*32,
        source_checkpoint_sha256=hashlib.sha256(raw.encode()).hexdigest(), sequence=5, state='FAILED', exit_code=10))
    session.read_text_file.side_effect = lambda path: receipt if path.endswith('/result.json') else raw
    result = api.resume_kace_configuration('c'*32, checkpoint['workflow_id'], checkpoint['sequence'], 7,
        {'host':'pi.local','selection':1,'session':7}, result_sha256='a'*64)
    assert result['status'] == 'failed'
    assert session.commands == []
    engine.prepare.assert_not_called()


@pytest.mark.parametrize('kind', ['oversized', 'invalid_json', 'deep_json'])
def test_malformed_receipt_does_not_hide_other_valid_operations(kind):
    sftp = Sftp()
    sftp.add('a'*32)
    sftp.add('b'*32)
    path = ROOT + '/' + 'a'*32 + '/result.json'
    sftp.files[path] = {'oversized':b'x'*32769, 'invalid_json':b'{', 'deep_json':b'['*2000+b']'*2000}[kind]
    result = discover(sftp)
    assert [item['operation_id'] for item in result['operations']] == ['b'*32]
    assert result['rejected'] == 1 and sftp.closed


def test_total_discovery_budget_closes_only_its_sftp_channel(monkeypatch):
    sftp = Sftp()
    closed = threading.Event()
    sftp.channel.close.side_effect = closed.set
    def blocked(*args, **kwargs):
        assert closed.wait(1), 'watcher must interrupt blocked SFTP enumeration'
        yield SimpleNamespace(filename='a'*32)
    sftp.listdir_iter = blocked
    monkeypatch.setattr(config, 'DISCOVERY_TIMEOUT_SECONDS', .03)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        discover(sftp)
    assert time.monotonic() - started < .5
    assert closed.is_set() and sftp.closed


def test_channel_setup_failure_still_closes_sftp():
    sftp = Sftp()
    sftp.get_channel = Mock(side_effect=OSError('channel failed'))
    with pytest.raises(OSError):
        discover(sftp)
    assert sftp.closed


def test_checkpoint_change_during_discovery_cannot_publish_inventory(resume, monkeypatch):
    api, session, checkpoint, engine = prepare(resume)
    def changed(*args, **kwargs):
        session.read_text_file.return_value = '{}'
        return {'operations':[], 'rejected':0, 'truncated':False}
    monkeypatch.setattr(config, 'discover_configuration_operations', changed)
    result = api.inspect_kace_configurations(checkpoint['workflow_id'], checkpoint['sequence'], 7,
        {'host':'pi.local','selection':1,'session':7})
    assert result['status'] == 'failed' and session.commands == []
    engine.prepare.assert_not_called()
