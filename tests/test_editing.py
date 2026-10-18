import json
from io import BytesIO

import pytest
from PIL import Image

from studio.editing import MediaLibrary, SceneEdit
from studio.media import MediaConfig
from studio.storage import ConflictError


def test_library_imports_existing_yaml_and_checks_revision_and_content(tmp_path):
    import yaml
    Image.new('RGB',(100,100),'red').save(tmp_path / 'image.png')
    (tmp_path / 'library.yaml').write_text(yaml.safe_dump({'assets':{'old':{'kind':'image','path':'image.png','license':'owned'}}}))
    library = MediaLibrary(MediaConfig(upload_dir=tmp_path))
    assert library.snapshot()['assets'][0]['id'] == 'old'
    result = library.save(0, {'id':'new','kind':'image','path':'image.png','license':'cc-by',
                              'source_url':'https://example.org','attribution':'Tác giả'})
    assert result['revision'] == 1
    assert library.path('new') == tmp_path / 'image.png'
    with pytest.raises(ConflictError): library.save(0, {'id':'third','kind':'image','path':'image.png','license':'owned'})
    with pytest.raises(ValueError): library.save(1, {'id':'outside','kind':'image','path':'../outside.png','license':'owned'})
    with pytest.raises(ValueError): library.save(1, {'id':'credit','kind':'image','path':'image.png','license':'cc-by'})
    (tmp_path / 'image.png').write_bytes(b'changed')
    assert library.path('new') is None


def test_uploaded_media_is_opaque_path_and_license_stays_unknown(tmp_path):
    library = MediaLibrary(MediaConfig(upload_dir=tmp_path))
    image = BytesIO(); Image.new('RGB',(100,100),'blue').save(image,format='PNG')
    data = image.getvalue()
    result = library.upload('../../photo.png', BytesIO(data), len(data))
    entry = result['assets'][0]
    assert entry['license'] == 'unknown' and '..' not in entry['path']
    assert library.path(entry['id']).read_bytes() == data
    for name, body, length in [('file.py',b'code',4), ('fake.png',b'not image',9), ('short.png',data,len(data)+1)]:
        with pytest.raises(ValueError): library.upload(name,BytesIO(body),length)
    assert len(library.snapshot()['assets']) == 1


def test_scene_edit_requests_require_matching_manifest_and_explicit_action():
    request = SceneEdit(video_id='V1', scene_id='S1', manifest_version=2, action='regenerate_audio',
                        narration='Lời đọc mới', request_id='request1')
    assert request.manifest_version == 2
    for change in ({'manifest_version':True}, {'action':'execute'}, {'request_id':'../bad'}):
        with pytest.raises(ValueError): SceneEdit.model_validate({**request.model_dump(), **change})


def test_manual_scene_edit_invalidates_gate_and_preserves_other_locked_assets(tmp_path, monkeypatch):
    from studio.factory import Factory
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path / 'policy.key'))
    cfg = MediaConfig(output_dir=tmp_path / 'out', production={'automation':'production'})
    factory = Factory(tmp_path / 'studio.sqlite',cfg,demo=True)
    try:
        factory.command('create',{'title':'Sửa cảnh','target_minutes':.2})
        for _ in range(50):
            factory.cycle()
            if any(g['kind']=='publish' for g in factory.snapshot()['gates']): break
        project = factory.snapshot()['projects'][0]; vid = project['id']; manifest = factory.orch.manifest(vid)
        scene = manifest.scenes[0]
        original_script = factory.orch.latest('scripts',vid).payload
        request = {'video_id':vid,'scene_id':scene.scene_id,'manifest_version':manifest.version,
                   'action':'regenerate_audio','narration':'Lời đọc đã được người dùng sửa.','request_id':'one'}
        from studio.editing import apply_edit
        factory.library.root.mkdir(parents=True,exist_ok=True)
        (factory.library.root/'music.wav').write_bytes(b'fixture')
        factory.library.save(0,{'id':'music','kind':'music','path':'music.wav','license':'owned'})
        with pytest.raises(ValueError):apply_edit(factory.orch,factory.library,{**request,'action':'replace_asset','narration':None,'asset_id':'music'},'wrong-kind')
        from studio.events import AuditLog, Envelope, ReviewResult
        from studio.orchestrator import StepResult
        approval=Envelope(topic='audit-log',key='PUB-'+vid,actor='human:editor',payload=AuditLog(actor='human:editor',action='gate.decide',evidence=json.dumps({'subject_id':'PUB-'+vid,'decision':'approve','by':'human:editor'})).model_dump())
        first = factory.command('scene',request); assert factory.command('scene',request) == first
        factory.execute(next(row for row in factory.commands.pending() if row['id']==first))
        assert 'PUB-'+vid not in factory.orch.gate.pending
        assert not factory.orch.desk.ready_for_publish(vid)
        result=StepResult('old','audit-log',vid)
        factory.orch._on_gate_decision(approval,result)
        assert result.actions==['untrusted:human:editor']
        failure=Envelope(topic='review-results',key=vid,actor='quality-reviewer',payload=ReviewResult(video_id=vid,source='quality',verdict='fail',findings=[]).model_dump())
        held=StepResult('review','review-results',vid)
        factory.orch._rework(failure,held)
        assert held.actions==['review:manual_hold']
        invalid=Envelope(topic='audit-log',key=vid,actor='console:editor',payload=AuditLog(actor='console:editor',action='gate.invalidate',evidence='bad json').model_dump())
        factory.orch.gate.apply(invalid)

        for _ in range(50): factory.cycle()
        updated = factory.orch.manifest(vid)
        assert updated.version > manifest.version
        assert updated.scenes[0].narration == request['narration']
        edited_script = factory.orch.latest('scripts',vid).payload
        assert edited_script['sections'][0]['heading'] == original_script['sections'][0]['heading']
        assert edited_script['sections'][0]['claim_ids'] == original_script['sections'][0]['claim_ids']
        assert edited_script['sections'][1:] == original_script['sections'][1:]
        assert updated.script_version == edited_script['version']
        assert 'PUB-'+vid in factory.orch.gate.pending
        assert factory.orch.gate.pending['PUB-'+vid].created_at > factory.orch.gate.invalidated_at['PUB-'+vid]
        with pytest.raises(ValueError): factory.command('scene',{**request,'request_id':'stale'})
    finally: factory.close()


def test_library_probe_media_types_and_rolls_back_failed_catalog_commit(tmp_path,monkeypatch):
    from types import SimpleNamespace

    import studio.editing as editing
    library=MediaLibrary(MediaConfig(upload_dir=tmp_path))
    assert library.path('absent') is None
    for streams,kind in [(['audio'],'music'),(['video','audio'],'video')]:
        monkeypatch.setattr(editing.subprocess,'run',lambda *a,_streams=streams,**kw:SimpleNamespace(stdout=json.dumps({'streams':[{'codec_type':s} for s in _streams]})))
        result=library.upload('file.mp4',BytesIO(b'fixture'),7)
        assert any(a['kind']==kind for a in result['assets'])
    monkeypatch.setattr(editing.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='{"streams":[]}'))
    with pytest.raises(ValueError):library.upload('empty.mp4',BytesIO(b'fixture'),7)
    files=set(tmp_path.glob('*.png'))
    monkeypatch.setattr(library,'save',lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    image=BytesIO();Image.new('RGB',(4,4),'red').save(image,format='PNG');body=image.getvalue()
    with pytest.raises(OSError):library.upload('photo.png',BytesIO(body),len(body))
    assert set(tmp_path.glob('*.png'))==files


def test_registered_library_provenance_and_checksum_are_validated_before_generation(tmp_path):
    from studio.events import Scene, SceneManifest
    from studio.media import MediaError
    from studio.production import registered_asset, selections
    cfg=MediaConfig(upload_dir=tmp_path,production={'scenes':{'V1':{'S1':'a'}}})
    path=tmp_path/'photo.png';Image.new('RGB',(4,4),'red').save(path)
    library=MediaLibrary(cfg);library.save(0,{'id':'a','kind':'image','path':'photo.png','license':'owned'})
    assert registered_asset(cfg,tmp_path,path).license=='owned'
    manifest=SceneManifest(video_id='V1',scenes=[Scene(scene_id='S1',order=0,narration='Lời',visual_prompt='Hình')])
    assert selections(cfg,tmp_path,manifest)[0]['S1'].checksum
    path.write_bytes(b'changed')
    assert registered_asset(cfg,tmp_path,path) is None
    with pytest.raises(MediaError):selections(cfg,tmp_path,manifest)
    import yaml
    (tmp_path/'library.yaml').write_text(yaml.safe_dump({'assets':{'a':{'kind':'image','path':'photo.png','license':'cc-by'}}}))
    assert registered_asset(cfg,tmp_path,path) is None
    (tmp_path/'library.yaml').write_text(yaml.safe_dump({'assets':{'a':{'kind':'image','path':'photo.png','license':'unknown'}}}))
    with pytest.raises(MediaError):selections(cfg,tmp_path,manifest)
    (tmp_path/'library.yaml').write_text('assets: {}')
    with pytest.raises(MediaError,match='invalid production library'):selections(cfg,tmp_path,manifest)


def test_scene_lock_unlock_and_catalog_replacement_preserve_other_scenes(tmp_path,monkeypatch):
    from studio.factory import Factory
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    factory=Factory(tmp_path/'db',MediaConfig(output_dir=tmp_path/'out',production={'automation':'production'}),demo=True)
    try:
        factory.command('create',{'title':'Thay tư liệu','target_minutes':.2})
        for _ in range(40):factory.cycle()
        project=factory.snapshot()['projects'][0];vid=project['id'];sid=project['scenes'][0]['id']
        others={s.scene_id:dict(s.asset_refs) for s in factory.orch.manifest(vid).scenes if s.scene_id!=sid}
        def edit(action,id,**fields):
            return {'video_id':vid,'scene_id':sid,'manifest_version':factory.orch.manifest(vid).version,'action':action,'request_id':id,**fields}
        lock=edit('lock','lock');factory.command('scene',lock)
        with pytest.raises(ValueError):factory.command('scene',{**lock,'action':'unlock'})
        for _ in range(40):factory.cycle()
        assert factory.orch.manifest(vid).scenes[0].locked
        with pytest.raises(ValueError):factory.command('scene',edit('regenerate_audio','locked',narration='x'))
        factory.command('scene',edit('unlock','unlock'))
        for _ in range(40):factory.cycle()
        image=BytesIO();Image.new('RGB',(20,20),'blue').save(image,format='PNG');data=image.getvalue()
        entry=factory.library.upload('photo.png',BytesIO(data),len(data))['assets'][0]
        snapshot=factory.library.snapshot();factory.library.save(snapshot['revision'],{**entry,'license':'owned'})
        factory.command('scene',edit('replace_asset','replace',asset_id=entry['id']))
        for _ in range(40):factory.cycle()
        updated=factory.orch.manifest(vid)
        assert updated.scenes[0].locked and all(s.asset_refs==others[s.scene_id] for s in updated.scenes if s.scene_id!=sid)
        latest=next(e for e in reversed(list(factory.bus.replay('media-assets',vid))) if e.payload.get('scene_id')==sid and e.payload['kind']=='scene_image')
        assert latest.payload['provenance']['license']=='owned'
    finally:factory.close()
