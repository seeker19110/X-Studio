
import pytest

from studio.events import Scene, SceneManifest
from studio.media import MediaConfig


def test_derived_scene_timeline_uses_clip_positions_without_adding_padding():
    from studio.timeline import timeline
    manifest=SceneManifest(video_id='V',scenes=[
        Scene(scene_id='S1',order=0,narration='Câu đầu.',visual_prompt='Một',duration_s=2.4,clip_start_s=0),
        Scene(scene_id='S2',order=1,narration='Câu sau.',visual_prompt='Hai',duration_s=3.1,clip_start_s=2.4)])
    assert [(c.start,c.end) for c in timeline(manifest,pad=.2,transition=.1)]==[(0,2.4),(2.4,5.5)]


def test_derived_project_preserves_sections_claims_voice_and_full_thumbnail(tmp_path,monkeypatch):
    from studio.events import Envelope, Script
    from studio.factory import Factory
    from studio.media import find_font
    from studio.presentation import font_evidence
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    f=Factory(tmp_path/'db',MediaConfig(output_dir=tmp_path/'out',production={'automation':'production'}),demo=True)
    try:
        f.command('create',{'title':'Kiểm tra bản xuất','target_minutes':.5})
        for _ in range(50):f.cycle()
        parent=f.snapshot()['projects'][0];vid=parent['id']
        script=Script.model_validate(f.orch.latest('scripts',vid).payload)
        for section in script.sections:section.claim_ids=['C1']
        f.bus.publish(Envelope(topic='scripts',key=vid,actor='console:editor',payload=script.model_dump()))
        source_manifest=f.orch.manifest(vid)
        from studio.events import MediaAsset
        audio=next(MediaAsset.model_validate(e.payload) for e in f.bus.replay('media-assets',vid) if e.payload['kind']=='scene_audio')
        audio.provenance.license='cc-by';audio.provenance.attribution='VAIS1000; CC BY 4.0; synthesized speech'
        audio.provenance.source_url='https://example.org/voice'
        f.bus.publish(Envelope(topic='media-assets',key=vid,actor='renderer',payload=audio.model_dump()))
        source_manifest.voice.update(resolved_provider='fake',resolved_voice_id='resolved-test-voice')
        f.bus.publish(Envelope(topic='scene-manifests',key=vid,actor='renderer',payload=source_manifest.model_dump()))
        final=next(a for a in reversed(parent['media']) if a['kind']=='final_video')
        f.command('shorts',{'video_id':vid,'manifest_version':parent['manifest_version'],'source_checksum':final['checksum'],
             'start_s':0,'end_s':final['duration_s'],'title':parent['title'],'request_id':'followup'})
        for _ in range(50):f.cycle()
        child=next(p for p in f.snapshot()['projects'] if p['origin'])
        manifest=f.orch.manifest(child['id']);source=f.orch.manifest(vid)
        assert len(manifest.scenes)==len(source.scenes)>1
        assert len(child['script']['sections'])==len(manifest.scenes)
        assert all(s['claim_ids']==['C1'] for s in child['script']['sections'])
        assert all(s.clip_start_s is not None for s in manifest.scenes)
        assert manifest.voice['voice_id']=='resolved-test-voice'
        assert 'VAIS1000; CC BY 4.0' in child['metadata']['description']
        assert next(a for a in child['media'] if a['kind']=='final_video')['provenance']['license']=='cc-by'
        seed_meta=next(e.payload for e in f.bus.replay('metadata-packages',child['id']) if e.actor=='chapters')
        assert seed_meta['alt_titles']==[]
        thumbnail=next(a for a in child['media'] if a['kind']=='thumbnail')
        assert thumbnail['provenance']['generated_by']=='studio:thumbnail-reuse'
        original=next(a for a in parent['media'] if a['kind']=='thumbnail' and a['provenance']['prompt_ref']=='thumbnail:A')
        assert thumbnail['checksum']==original['checksum']
        assert font_evidence(find_font())['license'] in {'bitstream-vera','unknown'}
        assert len(font_evidence(find_font())['sha256'])==64
    finally:f.close()


def test_metadata_quality_contract_separates_runtime_and_claimed_task_speed():
    from studio.presentation import QUALITY_DIRECTION
    assert any('thời lượng video' in item and 'thao tác' in item for item in QUALITY_DIRECTION)
    assert any('Thumbnail' in item and 'dấu chấm câu' in item and 'tiêu đề' in item for item in QUALITY_DIRECTION)


def test_unknown_font_is_not_covered_by_another_fonts_license(tmp_path):
    from studio.presentation import font_evidence
    file=tmp_path/'unverified.ttf';file.write_bytes(b'different font')
    assert font_evidence(file)['license']=='unknown'
    assert font_evidence(None)['license']=='unknown'


def test_clip_inspection_seeks_each_source_range_and_edit_uses_parent(tmp_path,monkeypatch):
    from types import SimpleNamespace

    from PIL import Image

    from studio.editing import validate_edit
    from studio.events import MediaAsset, Provenance
    from studio.inspection import inspect_project
    video=tmp_path/'clip.mp4';video.write_bytes(b'fixture');positions=[]
    def capture(argv,**kwargs):
        positions.append(float(argv[argv.index('-ss')+1]));Image.new('RGB',(180,320),'#aa7733').save(argv[-1])
    monkeypatch.setattr('studio.inspection.subprocess.run',capture)
    m=SceneManifest(video_id='V',scenes=[Scene(scene_id='S'+str(i),order=i,narration='Câu mẫu.',visual_prompt='Hình',
        clip_start_s=i*2,duration_s=2,asset_refs={'scene_video':str(video),'scene_audio':str(video)}) for i in range(2)])
    final=MediaAsset(video_id='V',kind='final_video',path=str(video),provider='ffmpeg',provenance=Provenance(generated_by='test'))
    report=inspect_project(m,final,MediaConfig(output_dir=tmp_path))
    assert report['sampled_scenes']==2 and positions==[1.,3.]
    o=SimpleNamespace(manifest=lambda _:m,desk=SimpleNamespace(state={'V':'in_review'},repair_rounds={'V':0}),_prior_upload=lambda _:None)
    with pytest.raises(ValueError,match='nguồn'):
        validate_edit(o,{'video_id':'V','scene_id':'S0','manifest_version':1,'action':'regenerate_audio','request_id':'edit'})
    from studio.bus import InMemoryBus
    from studio.media import MediaError, make_media
    from studio.renderer import Renderer
    with pytest.raises(MediaError,match='nguồn'):
        Renderer(InMemoryBus(),make_media(MediaConfig(output_dir=tmp_path)))._segments(m)


def test_clip_qc_measures_only_the_actual_scene_range(tmp_path):
    import shutil
    import subprocess

    from studio.qc import qc_scenes
    if not shutil.which('ffmpeg'):pytest.skip('requires FFmpeg')
    file=tmp_path/'range.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=320x180:r=25','-f','lavfi',
        '-i','sine=frequency=440:sample_rate=48000','-t','3','-c:v','libx264','-threads','1','-c:a','aac',str(file)],check=True)
    m=SceneManifest(video_id='V',scenes=[Scene(scene_id='S',order=0,narration='Câu ngắn.',visual_prompt='Mẫu',
        duration_s=1,clip_start_s=1,asset_refs={'scene_video':str(file),'scene_audio':str(file)})])
    result=qc_scenes(m)['S']
    assert result['metrics']['clip_start_s']==1 and result['metrics']['audio']['duration_s']==1
    assert not any('lệch manifest' in f['text'] for f in result['findings'])


def test_private_dossier_cannot_be_auto_published(tmp_path,monkeypatch):
    from studio.factory import Factory
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    f=Factory(tmp_path/'db',MediaConfig(output_dir=tmp_path/'out',production={'automation':'production'}),demo=True)
    try:
        f.command('create',{'title':'Riêng tư','rights_dossier':{'scope':'private_local_validation'}})
        for _ in range(50):f.cycle()
        f.orch.renderer.media.cfg.production['automation']='publish'
        sid=next(s for s in f.orch.gate.pending if s.startswith('PUB-'))
        assert 'cục bộ' in f.autopilot.assess(sid)
    finally:f.close()


def test_editor_package_keeps_current_refs_from_older_asset_versions(tmp_path):
    from types import SimpleNamespace

    from studio.bus import InMemoryBus
    from studio.events import Envelope
    from studio.media import make_media
    from studio.orchestrator import _with_manifest_assets
    from studio.renderer import Renderer
    renderer=Renderer(InMemoryBus(),make_media(MediaConfig(output_dir=tmp_path)))
    m=SceneManifest(video_id='V',scenes=[Scene(scene_id='S',order=0,narration='Xin chào.',visual_prompt='Hình')])
    renderer.render(m);m.version=2
    owner=SimpleNamespace(bus=renderer.bus,manifest=lambda _:m,desk=SimpleNamespace(repair_rounds={'V':1}))
    package=_with_manifest_assets(Envelope(topic='media-assets',key='V',actor='renderer',payload={'video_id':'V'}),owner)
    assert {'scene_audio','scene_image'}<={a['kind'] for a in package['scene_assets']}


def test_speech_license_applies_to_raw_and_trimmed_audio_and_replaces_stale_refs(tmp_path):
    from studio.bus import InMemoryBus
    from studio.media import MediaResult, make_media
    from studio.renderer import Renderer
    cfg=MediaConfig(output_dir=tmp_path,tts={'provider':'fake','license':'cc-by','source_url':'https://example.org/voice',
        'attribution':'VAIS1000; CC BY 4.0; synthesized speech'})
    renderer=Renderer(InMemoryBus(),make_media(cfg))
    m=SceneManifest(video_id='V',scenes=[Scene(scene_id='S',order=0,narration='Xin chào.',visual_prompt='Hình',
        asset_refs={'scene_audio_source':'old.wav','scene_timing':'old.json'})])
    assets=renderer.render_scene(m,m.scenes[0],image=False)
    assert 'scene_audio_source' not in m.scenes[0].asset_refs and 'scene_timing' not in m.scenes[0].asset_refs
    assert m.voice['resolved_voice_id']=='fake-tts'
    assert assets[0].provenance.license=='cc-by' and assets[0].provenance.source_url=='https://example.org/voice'
    raw=tmp_path/'raw.wav';raw.write_bytes(b'fixture')
    trimmed=renderer._speech_asset(m, m.scenes[0],MediaResult(raw,'ffmpeg','speech-tail-trim'), 'trim')
    assert trimmed.provenance.attribution==assets[0].provenance.attribution
