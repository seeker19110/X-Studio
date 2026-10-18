"""Checksummed directory bundles. Caller must hold the factory lease and stop media writers."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import yaml
from xagents_core.sqlite_bus import Lease, LeaseError

from .media import MediaConfig, load_media_config
from .storage import connect, migrate

MEDIA_SUFFIXES = {'.mp4', '.webm', '.mkv', '.mov', '.png', '.jpg', '.jpeg', '.webp', '.wav', '.mp3',
                  '.flac', '.srt', '.ass', '.json', '.svg', '.yaml'}
PRIVATE_NAME = re.compile(r'token|credential|secret|policy\.key|^\.|^llm\.|^media\.', re.I)
PROFILE_FIELDS = {
    'video': {'provider','fps','resolution','burn_captions','fit','motion','tail_pad_s','layout','caption_max_chars',
              'caption_max_chars_portrait','trim_voice_tail','transition_s'},
    'production': {'style','library','scenes','music','music_volume','pricing','media_budget_usd','review_required','quality_guidelines'},
    'render': {'mode','threads','timeout_s','segment_cache_max_bytes','min_free_disk_bytes'},
}


def safe_profile(profile: dict) -> dict:
    return {group:{k:v for k,v in profile.get(group,{}).items() if k in fields}
            for group,fields in PROFILE_FIELDS.items()}


def digest(path: Path) -> str:
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def _readonly(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)


def _rows(con: sqlite3.Connection, table: str, column: str) -> list[Any]:
    if not con.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone(): return []
    return [json.loads(row[0]) for row in con.execute(f'SELECT {column} FROM {table}')]


def _rename_ready(stage: Path, destination: Path) -> None:
    # Windows scanners can briefly hold newly closed SQLite/media files. Keep
    # the atomic directory rename, bound the retry, and retain real access errors.
    for attempt in range(6):
        try:
            stage.rename(destination); return
        except PermissionError as exc:
            if getattr(exc,'winerror',None) not in {5,32} or attempt==5: raise
            time.sleep(.05*2**attempt)


def create_backup(db: Path, cfg: MediaConfig, destination: Path) -> dict:
    """Called at a factory checkpoint, or offline by CLI while holding the database lease."""
    destination = destination.resolve()
    if destination.exists(): raise ValueError('Backup destination must be a new directory')
    output = cfg.output_dir.resolve()
    uploads = (cfg.upload_dir or output / 'uploads').resolve()
    roots = [{'source': str(output), 'target': 'output'}, {'source': str(uploads), 'target': 'uploads'}]
    source = _readonly(db)
    try:
        events = _rows(source, 'events', 'body')
        jobs = _rows(source, 'website_jobs', 'data')
        for job in jobs:
            roots.append({'source': str(Path(job['directory']).resolve()), 'target': 'website/' + job['id']})
        if any(destination.is_relative_to(Path(r['source'])) for r in roots):
            raise ValueError('Backup destination must be outside media directories')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.backup-', dir=destination.parent) as temp:
            stage = Path(temp) / 'bundle'; stage.mkdir()
            with connect(stage / 'studio.sqlite') as target:
                source.backup(target)
                if target.execute("SELECT 1 FROM sqlite_master WHERE name='upload_sessions'").fetchone():
                    for id, raw in target.execute('SELECT id,data FROM upload_sessions').fetchall():
                        session = json.loads(raw); session.pop('location', None)
                        if not session.get('result'): session['state'] = 'needs_reconcile'
                        target.execute('UPDATE upload_sessions SET data=? WHERE id=?', (json.dumps(session), id))
            candidates: set[Path] = set()
            for event in events:
                payload = event.get('payload', {})
                vid = payload.get('video_id')
                if isinstance(vid, str) and re.fullmatch(r'[a-zA-Z0-9_-]+', vid):
                    folder = output / vid
                    if folder.is_dir(): candidates.update(folder.rglob('*'))
            for folder in (output / '.cache', uploads, *[Path(j['directory']) for j in jobs]):
                if folder.is_dir(): candidates.update(folder.rglob('*'))
            for file in sorted(candidates):
                if file.is_symlink() or not file.is_file() or file.suffix.lower() not in MEDIA_SUFFIXES: continue
                if PRIVATE_NAME.search(file.name): continue
                root = next((r for r in sorted(roots, key=lambda r: -len(r['source']))
                             if file.absolute().is_relative_to(Path(r['source']))), None)
                if root is None or file.resolve() != file.absolute(): raise ValueError('Media symlinks are not supported in backup')
                relative = file.relative_to(root['source'])
                if any(part.startswith('.') and part != '.cache' for part in relative.parts[:-1]): continue
                target_path = stage / 'media' / root['target'] / relative
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target_path)
            files = [{'path': f.relative_to(stage).as_posix(), 'size': f.stat().st_size, 'sha256': digest(f)}
                     for f in sorted(stage.rglob('*')) if f.is_file()]
            profile = safe_profile({'video':cfg.video,'production':cfg.production,'render':cfg.render})
            manifest = {'version': 1, 'roots': roots, 'files': files, 'profile':profile}
            (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            _rename_ready(stage,destination)
    finally: source.close()
    return {'directory': str(destination), 'files': len(files), 'bytes': sum(f['size'] for f in files)}


def _bundle_file(root: Path, name: str) -> Path:
    posix = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or posix.is_absolute() or '..' in posix.parts
            or name != posix.as_posix()): raise ValueError('Invalid bundle path')
    path = root / name
    if not path.is_file() or path.resolve() != path.absolute(): raise ValueError('Missing file or symlink in backup')
    return path


def _rebase(value: Any, roots: list[dict], destination: Path) -> Any:
    if isinstance(value, dict): return {k: _rebase(v, roots, destination) for k, v in value.items()}
    if isinstance(value, list): return [_rebase(v, roots, destination) for v in value]
    if isinstance(value, str):
        if value.startswith(('{', '[')):
            try: return json.dumps(_rebase(json.loads(value), roots, destination), ensure_ascii=False)
            except ValueError: pass
        for root in sorted(roots, key=lambda r: -len(r['source'])):
            old = root['source'].rstrip('/\\')
            if value == old or value.startswith((old + '/', old + '\\')):
                tail = value[len(old):].lstrip('/\\').replace('\\', '/')
                if '..' in PurePosixPath(tail).parts: raise ValueError('Media reference escapes restored workspace')
                return str(destination / 'media' / root['target'] / tail)
    return value


def restore_backup(bundle: Path, destination: Path) -> dict:
    bundle = bundle.absolute(); destination = destination.absolute()
    if destination.exists(): raise ValueError('Restore destination must be a new directory')
    try:
        path = _bundle_file(bundle, 'manifest.json')
        if path.stat().st_size > 16 * 1024 * 1024: raise ValueError('Manifest too large')
        manifest = json.loads(path.read_text(encoding='utf-8'))
        profile = safe_profile(manifest.get('profile',{}))
        if manifest['version'] != 1 or not 1 <= len(manifest['files']) <= 100000: raise ValueError('Unsupported backup version')
        names = [row['path'] for row in manifest['files']]
        if len(names) != len(set(names)) or 'studio.sqlite' not in names: raise ValueError('Invalid backup file list')
        for root in manifest['roots']:
            target = PurePosixPath(root['target'])
            if (not isinstance(root['source'], str) or not root['source'] or target.is_absolute()
                    or not re.fullmatch(r'output|uploads|website/[a-zA-Z0-9]+', root['target'])): raise ValueError('Invalid media root')
        for row in manifest['files']:
            file = _bundle_file(bundle, row['path'])
            if file.stat().st_size != row['size'] or digest(file) != row['sha256']: raise ValueError('Backup checksum mismatch')
    except (OSError, KeyError, TypeError, AttributeError) as exc: raise ValueError('Invalid or incomplete backup') from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-', dir=destination.parent) as temp:
        stage = Path(temp) / 'workspace'; stage.mkdir()
        for row in manifest['files']:
            target_path = stage / row['path']; target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle / row['path'], target_path)
            if digest(target_path) != row['sha256']: raise ValueError('Backup changed while restoring')
        db = stage / 'studio.sqlite'
        try:
            with connect(db) as con:
                if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ValueError('Invalid backup database')
            migrate(db)
            with connect(db) as con:
                for table, column in (('events', 'body'), ('website_jobs', 'data'), ('factory_commands', 'body')):
                    if not con.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone(): continue
                    for rowid, raw in con.execute(f'SELECT rowid,{column} FROM {table}').fetchall():
                        value = _rebase(json.loads(raw), manifest['roots'], destination)
                        if table == 'website_jobs' and value['status'] not in {'completed', 'failed', 'cancelled'}:
                            value.update(status='cancelled', cancel_requested=True, message='Khôi phục xong; chọn Tiếp tục khi sẵn sàng')
                        con.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (json.dumps(value, ensure_ascii=False), rowid))
                con.execute("UPDATE factory_commands SET status='error',error='Cancelled during restore' WHERE status='pending'")
                con.execute('INSERT INTO factory_commands(id,kind,body,status) VALUES (?,?,?,\'done\')',
                            (uuid4().hex, 'settings', json.dumps({'automation':'manual','style':profile['production'].get('style',''),
                             'threads':profile['render'].get('threads',2),'burn_captions':profile['video'].get('burn_captions',True)})))
                con.execute('INSERT INTO factory_commands(id,kind,body,status) VALUES (?,?,?,\'done\')', (uuid4().hex, 'pause', '{}'))
                # A new identity allows each browser to import its local drafts into this restored workspace.
                con.execute("UPDATE studio_meta SET value=? WHERE key='database_id'", (uuid4().hex,))
        except sqlite3.DatabaseError as exc: raise ValueError('Invalid backup database') from exc
        for file in (stage / 'media').rglob('*.json'):
            try: value = json.loads(file.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError): continue
            file.write_text(json.dumps(_rebase(value, manifest['roots'], destination), ensure_ascii=False, indent=2), encoding='utf-8')
        config = {'output_dir': str(destination / 'media' / 'output'), 'upload_dir': str(destination / 'media' / 'uploads'),
                  'video':profile.get('video',{}), 'render':profile.get('render',{}),
                  'production': {**_rebase(profile.get('production',{}),manifest['roots'],destination),'automation':'manual'}}
        (stage / 'media.restore.yaml').write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
        _rename_ready(stage,destination)
    return {'directory': str(destination), 'db': str(destination / 'studio.sqlite'), 'paused': True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='X-Studio backup/restore vào thư mục mới')
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('create'); create.add_argument('--db', type=Path, default=Path('studio.sqlite'))
    create.add_argument('--media', type=Path); create.add_argument('--to', type=Path, required=True)
    restore = sub.add_parser('restore'); restore.add_argument('--from', dest='bundle', type=Path, required=True)
    restore.add_argument('--to', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'restore': result = restore_backup(args.bundle, args.to)
        else:
            lease = Lease(args.db); lease.acquire()
            try: result = create_backup(args.db, load_media_config(args.media), args.to)
            finally: lease.release()
    except (ValueError, OSError, sqlite3.DatabaseError, LeaseError) as exc:
        print(json.dumps({'error': str(exc)[:300]}, ensure_ascii=False)); return 1
    print(json.dumps(result, ensure_ascii=False)); return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
