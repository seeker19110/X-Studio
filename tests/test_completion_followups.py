import pytest

from studio.bus import InMemoryBus
from studio.events import (
    Chapter,
    CutList,
    Envelope,
    MetadataPackage,
    Repair,
    Scene,
    SceneManifest,
    Script,
    ScriptSection,
)
from studio.media import MediaConfig, make_media
from studio.preflight import preflight
from studio.renderer import Renderer


@pytest.mark.parametrize('duration,warn', [(17., False), (29.99, False), (30., True), (None, True)])
def test_missing_chapters_advice_uses_measured_duration(duration, warn):
    meta = MetadataPackage(video_id='V', title='Một hướng dẫn', description='Ngữ cảnh. '*30, tags=['hướng dẫn'])
    report = preflight(meta, duration_s=duration)
    assert any(f.location=='chapters' for f in report.findings)==warn
    meta.chapters = [Chapter(time='00:05', label='Sai điểm bắt đầu')]
    assert preflight(meta, duration_s=17).blocked


@pytest.mark.parametrize('matching', [True, False])
def test_repair_links_only_the_script_actually_read_in_all_scenes(tmp_path, matching):
    renderer = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path)))
    manifest = SceneManifest(video_id='V', scenes=[Scene(scene_id='S1', order=0, narration='Câu cũ.', visual_prompt='Hình')])
    renderer.render(manifest)
    script = Script(video_id='V', version=4, working_title='Hướng dẫn', hook='Câu mới.',
                    sections=[ScriptSection(heading='Một ý', narration='Câu mới.' if matching else 'Câu khác.')],
                    word_count=2, estimated_minutes=.1)
    renderer.bus.publish(Envelope(topic='scripts', key='V', actor='console:editor', payload=script.model_dump()))
    updated = renderer.apply_cutlist(manifest, CutList(video_id='V', manifest_version=1, decision='repair',
        repairs=[Repair(scene_id='S1', action='regenerate_audio', reason='Sửa lời', new_narration='Câu mới.')]))
    assert updated.script_version==(4 if matching else 1)


@pytest.mark.parametrize('license', ['licensed', 'cc-by', 'unknown'])
def test_composite_exports_keep_current_component_rights(tmp_path, license):
    renderer = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path,
        tts={'provider':'fake', 'license':license, 'source_url':'https://example.org/voice', 'attribution':'CURRENT_VOICE'})))
    manifest = SceneManifest(video_id='V', scenes=[Scene(scene_id='S1', order=0, narration='Xin chào.', visual_prompt='Hình')])
    draft = renderer.render(manifest)[-1]
    assert draft.provenance.license==license
    assert 'CURRENT_VOICE' in draft.provenance.attribution
    historical = draft.model_copy(deep=True)
    historical.kind='scene_audio'; historical.path=str(tmp_path/'old.wav')
    historical.provenance.license='unknown'; historical.provenance.attribution='OBSOLETE_VOICE'
    renderer.bus.publish(Envelope(topic='media-assets',key='V',actor='renderer',payload=historical.model_dump()))
    final = renderer.finalize(manifest)
    assert final.provenance.license==license
    assert 'OBSOLETE_VOICE' not in final.provenance.attribution


def test_review_context_and_credit_use_current_assets_and_configured_voice(tmp_path, monkeypatch):
    # Use the factory's normal demo setup to exercise the same code as live runs.
    from studio.factory import Factory
    from studio.orchestrator import _with_provenance, _with_script
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    factory = Factory(tmp_path/'db', MediaConfig(output_dir=tmp_path/'out',
        tts={'provider':'fake', 'model':'licensed-voice', 'voice':'Ngọc Huyền', 'license':'licensed',
             'attribution':'CURRENT_VOICE', 'rights_assessment':{'publication_decision':'COMMERCIAL_VOICE_USE_ALLOWED'}},
        production={'automation':'production'}), demo=True)
    try:
        factory.command('create', {'title':'Hướng dẫn', 'target_minutes':.2})
        for _ in range(40): factory.cycle()
        project=factory.snapshot()['projects'][0]; vid=project['id']
        env=Envelope(topic='review-results',key=vid,actor='fact-checker',payload={'video_id':vid})
        context=_with_script(env,factory.orch)
        assert context['speech_profile']['voice']=='Ngọc Huyền'
        assert 'CURRENT_VOICE' in project['metadata']['description']
        manifest=factory.orch.manifest(vid)
        for scene in manifest.scenes: scene.duration_s=2
        factory.bus.publish(Envelope(topic='scene-manifests',key=vid,actor='renderer',payload=manifest.model_dump()))
        meta=MetadataPackage.model_validate(project['metadata']);meta.chapters=[]
        factory.bus.publish(Envelope(topic='metadata-packages',key=vid,actor='chapters',payload=meta.model_dump()))
        factory.cycle()
        assert all(f.location!='chapters' for f in factory.orch.preflights[vid].findings)
        # Snapshot media uses URLs, so use the persisted media record as the fixture.
        old={**next(e.payload for e in factory.bus.replay('media-assets',vid) if e.payload['kind']=='scene_audio'),
             'path':str(tmp_path/'obsolete.wav'),'manifest_version':0}
        factory.bus.publish(Envelope(topic='media-assets',key=vid,actor='renderer',payload=old))
        provenance=_with_provenance(env,factory.orch)
        assert provenance['metadata']['video_id']==vid
        assert provenance['metadata']==factory.orch.latest('metadata-packages',vid).payload
        assert all(a['usage']=='historical' for a in provenance['assets'] if a['path']==old['path'])
        assert any(a['usage']=='current' for a in provenance['assets'] if a['path']==manifest.scenes[0].asset_refs['scene_audio'])
        factory.orch._rehydrate()
        assert all(f.location!='chapters' for f in factory.orch.preflights[vid].findings)
    finally: factory.close()


def test_metadata_adds_only_missing_notices_from_a_composite_credit(tmp_path):
    from types import SimpleNamespace

    from studio.events import MediaAsset, Provenance
    from studio.orchestrator import Orchestrator
    bus=InMemoryBus()
    path=str(tmp_path/'speech.wav')
    manifest=SceneManifest(video_id='V',scenes=[Scene(scene_id='S',order=0,narration='Lời.',visual_prompt='Hình',
        asset_refs={'scene_audio':path})])
    asset=MediaAsset(video_id='V',kind='scene_audio',scene_id='S',path=path,provider='command',
        provenance=Provenance(generated_by='command:voice',license='licensed',
            attribution='EXACT_VOICE_NOTICE\nFONT_NOTICE',source_url='https://example.org/license'))
    bus.publish(Envelope(topic='media-assets',key='V',actor='renderer',payload=asset.model_dump()))
    owner=SimpleNamespace(bus=bus,manifest=lambda _:manifest)
    meta=MetadataPackage(video_id='V',title='Hướng dẫn',description='Ngữ cảnh.\nEXACT_VOICE_NOTICE',tags=[])
    Orchestrator._credit_metadata(owner,meta)
    Orchestrator._credit_metadata(owner,meta)
    assert meta.description.count('EXACT_VOICE_NOTICE')==1
    assert meta.description.count('FONT_NOTICE')==1
    assert meta.description.count('https://example.org/license')==1


@pytest.mark.parametrize('already_complete',[False,True])
def test_metadata_completes_or_removes_a_verbatim_partial_credit_paragraph(tmp_path,already_complete):
    from types import SimpleNamespace

    from studio.events import MediaAsset, Provenance
    from studio.orchestrator import Orchestrator
    bus=InMemoryBus();path=str(tmp_path/'voice.wav')
    prefix='Synthetic voice: Named Model, Named Speaker. Licensed use with attribution.'
    complete=prefix+' Newly synthesized, trimmed and normalized; portrait excerpt reframed.'
    manifest=SceneManifest(video_id='V',scenes=[Scene(scene_id='S',order=0,narration='Words.',visual_prompt='Art',asset_refs={'scene_audio':path})])
    asset=MediaAsset(video_id='V',kind='scene_audio',scene_id='S',path=path,provider='command',
        provenance=Provenance(generated_by='command:voice',license='licensed',attribution=complete))
    bus.publish(Envelope(topic='media-assets',key='V',actor='renderer',payload=asset.model_dump()))
    owner=SimpleNamespace(bus=bus,manifest=lambda _:manifest)
    meta=MetadataPackage(video_id='V',title='Example',description='Context.\n\n'+prefix+('\n\nCredit: '+complete if already_complete else ''))
    Orchestrator._credit_metadata(owner,meta);Orchestrator._credit_metadata(owner,meta)
    assert meta.description.count(prefix)==meta.description.count(complete)==1
    assert 'Context.' in meta.description


def test_completed_export_waiting_for_owner_is_not_an_idle_production(tmp_path,monkeypatch):
    from datetime import UTC, datetime, timedelta

    from studio.factory import Factory
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    f=Factory(tmp_path/'db',MediaConfig(output_dir=tmp_path/'out',production={'automation':'production'}),demo=True)
    try:
        f.command('create',{'title':'Hướng dẫn','target_minutes':.2})
        for _ in range(40):f.cycle()
        vid=f.snapshot()['projects'][0]['id']
        assert f.orch.desk.ready_for_publish(vid)
        f.orch.tick(now=datetime.now(UTC)+timedelta(hours=7),max_steps=0)
        assert vid not in f.orch.paused
        assert f.orch.desk.state[vid]=='in_review'
        assert 'PUB-'+vid in f.orch.gate.pending
        assert not list(f.bus.replay('publish-events'))
    finally:f.close()


@pytest.mark.parametrize('reason,paused',[('không hoạt động > 6:00:00',False),('cùng lỗi lặp ≥ 2 lần',True)])
def test_replay_ignores_only_obsolete_idle_hold_on_completed_export(tmp_path,monkeypatch,reason,paused):
    from studio.factory import Factory
    from studio.orchestrator import Orchestrator
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    f=Factory(tmp_path/'db',MediaConfig(output_dir=tmp_path/'out',production={'automation':'production'}),demo=True)
    try:
        f.command('create',{'title':'Hướng dẫn','target_minutes':.2})
        for _ in range(40):f.cycle()
        vid=f.snapshot()['projects'][0]['id']
        f.bus.publish(Envelope(topic='supervisor-actions',key=vid,actor='supervisor',
            payload={'target':vid,'action':'escalate','reason':reason}))
        assert (vid in f.orch.paused)==paused
        replayed=Orchestrator(f.bus,f.orch.runner.client,media=f.orch.renderer.media)
        assert (vid in replayed.paused)==paused
        assert replayed.desk.state[vid]==('escalated' if paused else 'in_review')
        assert 'PUB-'+vid in replayed.gate.pending
        assert not list(f.bus.replay('publish-events'))
    finally:f.close()
