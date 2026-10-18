import json
from pathlib import Path

import pytest

from studio import backup
from studio.media import MediaConfig
from studio.storage import IdeaStore, connect


def seed(tmp_path):
    db = tmp_path / 'studio.sqlite'; out = tmp_path / 'out'; out.mkdir()
    store = IdeaStore(db); store.import_items([{'id':'a', 'title':'Đổi trình duyệt vẫn thấy'}])
    video = out / 'V1'; video.mkdir(); final = video / 'final.mp4'; final.write_bytes(b'video fixture')
    (out / 'policy.key').write_text('PRIVATE'); (out / 'tokens.json').write_text('PRIVATE')
    with connect(db) as con:
        con.execute('CREATE TABLE events(seq INTEGER PRIMARY KEY, event_id TEXT, topic TEXT, key TEXT, actor TEXT, ts TEXT, body TEXT)')
        body = {'topic': 'media-assets', 'payload': {'video_id':'V1', 'path':str(final)}, 'event_id':'e'}
        con.execute('INSERT INTO events VALUES (1,?,?,?,?,?,?)', ('e','media-assets','V1','renderer','now',json.dumps(body)))
    return db, MediaConfig(output_dir=out), final


def test_backup_restore_rebases_media_and_preserves_ideas_without_credentials(tmp_path):
    db, cfg, final = seed(tmp_path)
    bundle = tmp_path / 'backup'
    backup.create_backup(db, cfg, bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    assert all({'path','size','sha256'} <= row.keys() for row in manifest['files'])
    assert not list(bundle.rglob('*.key')) and not list(bundle.rglob('tokens.json'))
    target = tmp_path / 'restored'; backup.restore_backup(bundle, target)
    restored_db = target / 'studio.sqlite'
    assert IdeaStore(restored_db).snapshot()['ideas'][0]['title'] == 'Đổi trình duyệt vẫn thấy'
    with connect(restored_db) as con:
        body = json.loads(con.execute('SELECT body FROM events').fetchone()[0])
        assert Path(body['payload']['path']).read_bytes() == final.read_bytes()
        assert Path(body['payload']['path']).is_relative_to(target)
        assert con.execute("SELECT kind FROM factory_commands ORDER BY rowid DESC LIMIT 1").fetchone()[0] == 'pause'
    assert (target / 'media.restore.yaml').is_file()
    assert not (target / 'studio.sqlite.lock').exists()


@pytest.mark.parametrize('kind', ['checksum', 'traversal', 'symlink', 'missing', 'version'])
def test_restore_rejects_corrupt_or_escaping_bundle_before_creating_target(tmp_path, kind):
    db, cfg, _ = seed(tmp_path); bundle = tmp_path / 'backup'; backup.create_backup(db, cfg, bundle)
    manifest_path = bundle / 'manifest.json'; data = json.loads(manifest_path.read_text())
    file = bundle / data['files'][0]['path']
    if kind == 'checksum': file.write_bytes(b'damaged')
    elif kind == 'traversal': data['files'][0]['path'] = '../outside'
    elif kind == 'symlink':
        file.unlink(); file.symlink_to(db)
    elif kind == 'missing': file.unlink()
    else: data['version'] = 99
    manifest_path.write_text(json.dumps(data))
    target = tmp_path / 'restored'
    with pytest.raises(ValueError): backup.restore_backup(bundle, target)
    assert not target.exists()


def test_backup_failure_keeps_existing_destination_and_source_and_cleans_stage(tmp_path, monkeypatch):
    db, cfg, final = seed(tmp_path); destination = tmp_path / 'bundle'
    destination.mkdir(); (destination / 'keep').write_text('existing')
    with pytest.raises(ValueError): backup.create_backup(db, cfg, destination)
    monkeypatch.setattr(backup.shutil, 'copyfile', lambda *a, **kw: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError): backup.create_backup(db, cfg, tmp_path / 'failed')
    assert not (tmp_path / 'failed').exists() and final.read_bytes() == b'video fixture'
    assert not list(tmp_path.glob('.backup-*'))


def test_backup_website_checkpoints_restore_paused_with_embedded_paths(tmp_path):
    db, cfg, _ = seed(tmp_path)
    website = tmp_path / 'website'; website.mkdir()
    (website / 'evidence.json').write_text(json.dumps({'nested':json.dumps({'path':str(website / 'screen.png')}), 'invalid':'{broken'}))
    (website / 'invalid.json').write_text('not json')
    (website / 'screen.png').write_bytes(b'screen fixture')
    with connect(db) as con:
        con.execute('INSERT INTO website_jobs VALUES (?,?)', ('b', json.dumps({'id':'b','directory':str(website),'status':'rendering'})))
    bundle = tmp_path / 'bundle'; backup.create_backup(db, cfg, bundle)
    with pytest.raises(ValueError): backup.create_backup(db, cfg, cfg.output_dir / 'nested')
    restored = tmp_path / 'restored'; backup.restore_backup(bundle, restored)
    with connect(restored / 'studio.sqlite') as con:
        job = json.loads(con.execute('SELECT data FROM website_jobs').fetchone()[0])
    assert job['status'] == 'cancelled' and job['cancel_requested']
    assert Path(job['directory']).is_relative_to(restored)
    data = json.loads((Path(job['directory']) / 'evidence.json').read_text())
    assert Path(json.loads(data['nested'])['path']).is_relative_to(restored)
    with pytest.raises(ValueError): backup.restore_backup(bundle, restored)


def test_restore_invalid_database_and_invalid_manifest_leave_no_workspace(tmp_path):
    db, cfg, _ = seed(tmp_path); bundle = tmp_path / 'bundle'; backup.create_backup(db, cfg, bundle)
    manifest = bundle / 'manifest.json'; data = json.loads(manifest.read_text())
    (bundle / 'studio.sqlite').write_bytes(b'not a database')
    for row in data['files']:
        if row['path'] == 'studio.sqlite': row.update(size=14, sha256=backup.digest(bundle / 'studio.sqlite'))
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError): backup.restore_backup(bundle, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
    manifest.write_text('{}')
    with pytest.raises(ValueError): backup.restore_backup(bundle, tmp_path / 'restored')


def test_backup_cli_create_restore_and_errors(tmp_path, capsys, monkeypatch):
    db, cfg, _ = seed(tmp_path)
    monkeypatch.setattr(backup, 'load_media_config', lambda *a: cfg)
    bundle = tmp_path / 'bundle'
    assert backup.main(['create','--db',str(db),'--to',str(bundle)]) == 0
    assert json.loads(capsys.readouterr().out)['files'] > 0
    assert backup.main(['restore','--from',str(bundle),'--to',str(tmp_path / 'restored')]) == 0
    assert json.loads(capsys.readouterr().out)['paused']
    assert backup.main(['restore','--from',str(bundle),'--to',str(tmp_path / 'restored')]) == 1
    capsys.readouterr()


def test_restore_profile_preserves_estimate_policy_and_removes_commands(tmp_path):
    import yaml
    db,cfg,_ = seed(tmp_path)
    cfg.production.update(pricing={'image_per_request':.2},media_budget_usd=3,review_required=True,review_command=['private'])
    cfg.video.update(layout='full_bleed',caption_max_chars=34,caption_max_chars_portrait=24,
                     trim_voice_tail=False,transition_s=0)
    cfg.production['quality_guidelines']=['Giữ lời đọc phù hợp khán giả của kênh']
    bundle=tmp_path/'bundle'; backup.create_backup(db,cfg,bundle)
    manifest=bundle/'manifest.json'; data=json.loads(manifest.read_text())
    data['profile']['video']['binary']='untrusted'
    data['profile']['render']['sandbox']={'mode':'container','image':'untrusted'}
    data['profile']['production']['review_command']=['untrusted']
    manifest.write_text(json.dumps(data))
    target=tmp_path/'restored';backup.restore_backup(bundle,target)
    config=yaml.safe_load((target/'media.restore.yaml').read_text())
    assert config['production']['pricing']=={'image_per_request':.2}
    assert config['production']['review_required'] is True and config['production']['media_budget_usd']==3
    assert 'review_command' not in config['production'] and 'binary' not in config['video']
    assert 'sandbox' not in config['render']
    assert {key:config['video'].get(key) for key in ['layout','caption_max_chars','caption_max_chars_portrait','trim_voice_tail','transition_s']}=={
        'layout':'full_bleed','caption_max_chars':34,'caption_max_chars_portrait':24,'trim_voice_tail':False,'transition_s':0}
    assert config['production']['quality_guidelines']==cfg.production['quality_guidelines']


def test_backup_removes_private_upload_session_and_restores_reconciliation_hold(tmp_path):
    from studio.upload_sessions import UploadSessions
    db,cfg,_=seed(tmp_path)
    sessions=UploadSessions(db); sessions.save('V1',{'location':'https://private-session','state':'uploading','offset':4})
    bundle=tmp_path/'bundle';backup.create_backup(db,cfg,bundle)
    checkpoint=UploadSessions(bundle/'studio.sqlite').get('V1')
    assert checkpoint['state']=='needs_reconcile' and 'location' not in checkpoint
    assert sessions.get('V1')['location']=='https://private-session'


def test_backup_restore_retry_transient_windows_rename_but_keep_permanent_failures(tmp_path,monkeypatch):
    import time
    db,cfg,final=seed(tmp_path)
    rename=Path.rename;calls=[];delays=[]
    def busy(source,target):
        calls.append(str(target))
        if len(calls)%3:
            error=PermissionError('temporary scanner handle');error.winerror=32
            raise error
        return rename(source,target)
    monkeypatch.setattr(Path,'rename',busy)
    monkeypatch.setattr(time,'sleep',delays.append)
    bundle=tmp_path/'bundle';backup.create_backup(db,cfg,bundle)
    backup.restore_backup(bundle,tmp_path/'restored')
    assert len(calls)==6 and len(delays)==4 and final.read_bytes()==b'video fixture'
    for windows_error in [5,None]:
        calls.clear()
        def forbidden(source,target,code=windows_error):
            calls.append(str(target));error=PermissionError('persistent access failure')
            if code is not None:error.winerror=code
            raise error
        monkeypatch.setattr(Path,'rename',forbidden)
        with pytest.raises(PermissionError):backup.create_backup(db,cfg,tmp_path/'denied')
        assert 1<=len(calls)<=8 and (windows_error is not None or len(calls)==1)
        assert not (tmp_path/'denied').exists() and final.is_file()
