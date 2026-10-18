import json

import pytest

from studio.bus import InMemoryBus
from studio.media import MediaConfig, MediaError
from studio.resources import MediaAccounting, cleanup_segments, inventory, preview_segments


def test_estimated_media_budget_counts_only_generation_and_preserves_unknown_cost(tmp_path):
    bus = InMemoryBus()
    cfg = MediaConfig(output_dir=tmp_path, production={'pricing':{'tts_per_1000_chars':.2,'image_per_request':.1},'media_budget_usd':.3})
    account = MediaAccounting(bus,cfg)
    assert account.generate('V1','tts',1.,lambda:'audio') == 'audio'
    account.generate('V1','image',1.,lambda:'image')
    with pytest.raises(MediaError): account.generate('V1','image',1.,lambda:pytest.fail('over budget'))
    with pytest.raises(MediaError): account.generate('V1','video',1.,lambda:pytest.fail('price missing'))
    ledger = account.summary()
    assert ledger['projects']['V1']['estimated_usd'] == pytest.approx(.3)
    assert ledger['projects']['V1']['generations'] == 2
    cfg.production.pop('media_budget_usd')
    with pytest.raises(OSError): account.generate('V2','image',1.,lambda:(_ for _ in ()).throw(OSError('provider failed')))
    assert account.summary()['projects']['V2']['unknown_cost'] == 1


def test_cleanup_preview_only_selects_owned_derived_segments_and_rejects_stale_preview(tmp_path):
    cfg = MediaConfig(output_dir=tmp_path)
    folder = tmp_path / 'V1' / '.segments'; folder.mkdir(parents=True)
    owned = folder / ('a'*64+'.mkv'); owned.write_bytes(b'12345')
    owned.with_suffix('.json').write_text(json.dumps({'provider':'ffmpeg','model':'bounded-scene'}))
    foreign = folder / 'family.mkv'; foreign.write_bytes(b'keep')
    paid = tmp_path / '.cache' / 'media'; paid.mkdir(parents=True); (paid / 'paid.wav').write_bytes(b'keep paid')
    plan = preview_segments(cfg,0)
    assert len(plan['files']) == 1 and plan['bytes'] == 5
    assert inventory(cfg)['bytes'] >= 18
    owned.write_bytes(b'changed')
    with pytest.raises(ValueError): cleanup_segments(cfg,0,plan['fingerprint'])
    result = cleanup_segments(cfg,0,preview_segments(cfg,0)['fingerprint'])
    assert result['bytes'] == 7 and not owned.exists()
    assert foreign.is_file() and (paid / 'paid.wav').is_file()


def test_inventory_groups_projects_and_ignores_links(tmp_path):
    cfg=MediaConfig(output_dir=tmp_path/'out'); project=cfg.output_dir/'V1';project.mkdir(parents=True)
    (project/'audio.wav').write_bytes(b'1234')
    uploads=cfg.output_dir/'uploads';uploads.mkdir();(uploads/'photo.png').write_bytes(b'123')
    outside=tmp_path/'website';outside.mkdir();(outside/'clip.mp4').write_bytes(b'12345')
    (project/'linked.wav').symlink_to(outside/'clip.mp4')
    result=inventory(cfg,[{'id':'W1','directory':str(outside)}])
    assert result['by_project']['V1']==4 and result['by_project']['website:W1']==5
    assert result['bytes']==12 and result['by_project']['uploads']==3


def test_ledger_handles_old_bad_evidence_and_counts_cache_and_reported_text(tmp_path):
    from studio.events import AuditLog, Envelope
    bus=InMemoryBus();account=MediaAccounting(bus,MediaConfig(output_dir=tmp_path))
    for action,actor,evidence in [('media.usage','media-accounting','bad'),('media.usage','media-accounting','[]'),
        ('media.usage','media-accounting','{"units":"bad","estimated_usd":null}'),
        ('media.usage','media-accounting','{"units":-1,"estimated_usd":null}'),
        ('render.cache','renderer','{"hits":{"audio":true,"image":false}}')]:
        audit=AuditLog(actor=actor,action=action,video_id='V1',evidence=evidence)
        bus.publish(Envelope(topic='audit-log',key='V1',actor=actor,payload=audit.model_dump()))
    account.record_text('V1',12);account.record_text('V1',-1)
    report=account.summary()
    assert report['cache_hits']==1 and report['text_tokens']==12
    assert report['projects']['V1']['units']['unknown']==0
    for changes in [{'pricing':{'image_per_request':-1}},{'media_budget_usd':-1}]:
        account.cfg.production=changes
        with pytest.raises(MediaError):account.generate('V1','image',1,lambda:pytest.fail('invalid config'))
    with pytest.raises(MediaError):account.generate('V1','image',float('nan'),lambda:None)


def test_cleanup_ignores_unrecognized_metadata_and_missing_files(tmp_path):
    folder=tmp_path/'V1'/'.segments';folder.mkdir(parents=True)
    for index,data in enumerate(['bad','{}','[]']):
        path=folder/(str(index)*64+'.mkv');path.write_bytes(b'fixture');path.with_suffix('.json').write_text(data)
    cfg=MediaConfig(output_dir=tmp_path)
    assert preview_segments(cfg,0)['files']==[]
    with pytest.raises(ValueError):preview_segments(cfg,-1)
    (tmp_path/'unrelated.txt').write_text('keep')
    assert inventory(cfg)['by_kind']=={'metadata':7,'video':21}


def test_inventory_tolerates_file_disappearing_during_scan(tmp_path,monkeypatch):
    from pathlib import Path
    path=tmp_path/'audio.wav';path.write_bytes(b'fixture')
    original=Path.stat;calls=0
    def stat(file,*args,**kwargs):
        nonlocal calls
        if file==path and kwargs.get('follow_symlinks',True):
            calls+=1
            if calls>=2:raise FileNotFoundError('concurrent deletion')
        return original(file,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',stat)
    assert inventory(MediaConfig(output_dir=tmp_path))['bytes']==0
