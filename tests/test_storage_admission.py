"""Storage admission observes bounded disk usage without deleting or stopping work."""
import os
from pathlib import Path

import pytest

from studio.media import MediaConfig
from studio.storage_admission import StorageAdmissionError, StorageMonitor


def monitor(tmp_path, **settings):
    return StorageMonitor(MediaConfig(output_dir=tmp_path / 'output', production={'storage': settings}), tmp_path / 'bus.sqlite')


def put(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    return path


def test_union_categories_hardlinks_database_sidecars_and_no_deletion(tmp_path):
    out = tmp_path / 'output'
    put(out / 'video.mp4', 10)
    cached = put(out / '.cache' / 'media' / 'blob.wav', 20)
    put(out / 'V' / '.segments' / 'scene.mkv', 30)
    put(out / 'V' / '.concat-one' / 'final.mp4', 40)
    put(out / 'uploads' / 'source.png', 50)
    put(out / 'bus.sqlite', 60)
    put(out / 'bus.sqlite-wal', 70)
    put(tmp_path / 'unrelated.db', 900)
    os.link(cached, out / '.cache' / 'duplicate.wav')
    mon = StorageMonitor(MediaConfig(output_dir=out), out / 'bus.sqlite')
    before = sorted(p for p in out.rglob('*') if p.is_file())
    result = mon.check_admission()
    assert result['total_bytes'] == 280
    assert result['categories'] == {'outputs': 10, 'cache': 50, 'temp': 40, 'uploads': 50, 'database': 130}
    assert result['complete'] and result['status'] == 'normal' and not result['errors']
    assert sorted(p for p in out.rglob('*') if p.is_file()) == before
    assert str(tmp_path) not in str(result)


def test_external_upload_overlap_and_hardlinks(tmp_path):
    out = tmp_path / 'shared' / 'output'
    source = put(tmp_path / 'shared' / 'source', 11)
    put(out / 'render', 23)
    os.link(source, out / 'same_source')
    mon = StorageMonitor(MediaConfig(output_dir=out, upload_dir=tmp_path / 'shared'), tmp_path / 'db')
    result = mon.snapshot()
    assert result['total_bytes'] == 34 and result['complete']
    external = tmp_path / 'elsewhere'
    put(external / 'asset', 19)
    mon = StorageMonitor(MediaConfig(output_dir=out, upload_dir=external), tmp_path / 'db')
    assert mon.snapshot()['total_bytes'] == 53


def test_missing_roots_soft_hard_threshold_and_force_fresh(tmp_path, monkeypatch):
    mon = monitor(tmp_path, soft_limit_bytes=10, hard_limit_bytes=20)
    monkeypatch.setattr('studio.storage_admission.time.monotonic', lambda: 100)
    assert mon.snapshot()['total_bytes'] == 0
    path = put(tmp_path / 'output' / 'video', 10)
    assert mon.snapshot()['total_bytes'] == 0
    assert mon.check_admission()['status'] == 'warning'
    path.write_bytes(b'x' * 20)
    with pytest.raises(StorageAdmissionError, match='hard'):
        mon.check_admission()
    assert mon.snapshot()['status'] == 'blocked'
    path.write_bytes(b'x')
    monkeypatch.setattr('studio.storage_admission.time.monotonic', lambda: 131)
    assert mon.snapshot()['status'] == 'normal'
    copy = mon.snapshot(); copy['categories']['outputs'] = 500
    assert mon.snapshot()['categories']['outputs'] == 1


@pytest.mark.parametrize('settings', [None, [], {'hard_limit_bytes': -1}, {'soft_limit_bytes': True},
    {'hard_limit_bytes': 1.5}, {'scan_interval_s': float('nan')}, {'scan_interval_s': float('inf')},
    {'scan_interval_s': -1}, {'scan_interval_s': True}, {'max_scan_entries': 0},
    {'max_scan_entries': False}, {'soft_limit_bytes': 2, 'hard_limit_bytes': 1}])
def test_invalid_storage_configuration_rejected(tmp_path, settings):
    with pytest.raises(ValueError, match='storage'):
        StorageMonitor(MediaConfig(production={'storage': settings}), tmp_path / 'db')


def test_symlinks_not_followed_and_incomplete_scan_fails_closed_only_if_enabled(tmp_path):
    outside = put(tmp_path / 'private' / 'secret', 10000)
    put(tmp_path / 'output' / 'video', 7)
    (tmp_path / 'output' / 'link').symlink_to(outside.parent, target_is_directory=True)
    mon = monitor(tmp_path)
    result = mon.check_admission()
    assert result['total_bytes'] == 7 and result['skipped_symlinks'] == 1
    assert not result['complete'] and result['status'] == 'warning'
    assert 'secret' not in str(result)
    with pytest.raises(StorageAdmissionError, match='incomplete'):
        monitor(tmp_path, hard_limit_bytes=99999).check_admission()
    linked = tmp_path / 'linked'; linked.symlink_to(outside.parent, target_is_directory=True)
    result = StorageMonitor(MediaConfig(output_dir=linked), tmp_path / 'db').snapshot()
    assert result['total_bytes'] == 0 and not result['complete']


def test_entry_limit_is_bounded_and_fails_closed(tmp_path):
    for i in range(20): put(tmp_path / 'output' / str(i), 1)
    mon = monitor(tmp_path, hard_limit_bytes=100, max_scan_entries=10)
    with pytest.raises(StorageAdmissionError, match='incomplete'):
        mon.check_admission()
    result = mon.snapshot()
    assert result['scanned_entries'] == 10 and not result['complete']
    assert any('limit' in e for e in result['errors'])


def test_io_failures_do_not_disclose_paths_and_fail_closed(tmp_path, monkeypatch):
    put(tmp_path / 'output' / 'asset', 4)
    original = os.scandir
    def broken(path):
        if Path(path) == tmp_path / 'output': raise PermissionError('secret credential /private/password')
        return original(path)
    monkeypatch.setattr('studio.storage_admission.os.scandir', broken)
    mon = monitor(tmp_path, hard_limit_bytes=100)
    with pytest.raises(StorageAdmissionError, match='incomplete'):
        mon.check_admission()
    assert 'password' not in str(mon.snapshot())


def test_blocked_queue_reuses_cache_but_admission_always_refreshes_when_hard_enabled(tmp_path, monkeypatch):
    from unittest.mock import Mock

    mon = monitor(tmp_path, hard_limit_bytes=10)
    path = put(tmp_path / 'output' / 'asset', 10)
    monkeypatch.setattr('studio.storage_admission.time.monotonic', lambda: 100)
    scan = Mock(wraps=os.scandir)
    monkeypatch.setattr('studio.storage_admission.os.scandir', scan)
    with pytest.raises(StorageAdmissionError): mon.check_admission(force=False)
    initial = scan.call_count
    for _ in range(10):
        with pytest.raises(StorageAdmissionError): mon.check_admission(force=False)
    assert scan.call_count == initial
    path.write_bytes(b'x')
    monkeypatch.setattr('studio.storage_admission.time.monotonic', lambda: 131)
    assert mon.check_admission(force=False)['total_bytes'] == 1
    assert scan.call_count > initial
    # A cached normal result is insufficient to admit another project.
    path.write_bytes(b'x' * 10)
    with pytest.raises(StorageAdmissionError): mon.check_admission(force=False)
    # Explicit user admission refreshes even a blocked snapshot.
    path.write_bytes(b'x')
    assert mon.check_admission()['total_bytes'] == 1
    observation = monitor(tmp_path)
    assert observation.check_admission()['total_bytes'] == 1
    path.write_bytes(b'x' * 20)
    assert observation.check_admission()['total_bytes'] == 1


@pytest.mark.parametrize('failure', [FileNotFoundError, PermissionError])
def test_unreadable_or_disappearing_files_report_incomplete(tmp_path, monkeypatch, failure):
    path = put(tmp_path / 'output' / 'asset', 4)
    original = Path.lstat
    def broken(self, *args, **kwargs):
        if self == path: raise failure('private path and credentials')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', broken)
    result = monitor(tmp_path).snapshot()
    assert not result['complete'] and result['total_bytes'] == 0
    assert 'credentials' not in str(result)


def test_database_directory_not_recursed_and_limit_counts_roots(tmp_path):
    put(tmp_path / 'bus.sqlite' / 'not-database', 100)
    result = monitor(tmp_path).snapshot()
    assert not result['complete'] and result['total_bytes'] == 0
    assert any('database file' in message for message in result['errors'])
    result = monitor(tmp_path, max_scan_entries=1).snapshot()
    assert not result['complete'] and result['scanned_entries'] == 1


@pytest.mark.parametrize('root_kind', ['output', 'uploads', 'database'])
def test_symlink_parent_before_dotdot_cannot_bypass_admission(tmp_path, root_kind):
    child = tmp_path / 'real' / 'child'
    child.mkdir(parents=True)
    (tmp_path / 'link').symlink_to(child, target_is_directory=True)
    tricky = tmp_path / 'link' / '..'
    out, uploads, db = tmp_path / 'output', tmp_path / 'uploads', tmp_path / 'bus.sqlite'
    if root_kind == 'output':
        out = tricky / 'output'
        put(out / '.cache' / 'media' / 'video', 200)
    elif root_kind == 'uploads':
        uploads = tricky / 'uploads'
        put(uploads / 'source', 200)
    else:
        db = tricky / 'bus.sqlite'
        put(db, 100); put(Path(str(db) + '-wal'), 100)
    mon = StorageMonitor(MediaConfig(output_dir=out, upload_dir=uploads,
                         production={'storage': {'hard_limit_bytes': 100}}), db)
    with pytest.raises(StorageAdmissionError):
        mon.check_admission()
    assert not mon.snapshot()['complete']
    assert mon.snapshot()['skipped_symlinks'] > 0


def test_ordinary_dotdot_and_upload_sibling_preserve_filesystem_meaning(tmp_path):
    out = tmp_path / 'output'
    out.mkdir()
    uploads = out / '..' / 'uploads'
    put(out / '.cache' / 'media' / 'cache', 30)
    put(uploads / 'source', 50)
    db = out / '..' / 'bus.sqlite'
    put(db, 10); put(Path(str(db) + '-wal'), 10)
    mon = StorageMonitor(MediaConfig(output_dir=out, upload_dir=uploads,
                         production={'storage': {'hard_limit_bytes': 100}}), db)
    result = mon.snapshot()
    assert result['complete'] and result['total_bytes'] == 100
    with pytest.raises(StorageAdmissionError): mon.check_admission()


def test_unreadable_root_component_fails_closed_without_disclosing_path(tmp_path, monkeypatch):
    blocked = tmp_path / 'private'
    original = Path.lstat

    def denied(path, *args, **kwargs):
        if path == blocked: raise PermissionError('secret credential')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', denied)
    mon = StorageMonitor(MediaConfig(output_dir=blocked / 'output',
                         production={'storage': {'hard_limit_bytes': 100}}), tmp_path / 'bus.sqlite')
    with pytest.raises(StorageAdmissionError): mon.check_admission()
    assert not mon.snapshot()['complete']
    assert 'secret' not in str(mon.snapshot())
