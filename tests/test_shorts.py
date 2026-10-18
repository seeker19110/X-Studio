import json
import shutil
import subprocess

import pytest

from studio.media import MediaConfig
from studio.shorts import ShortsRequest, render_short


def request(**changes):
    return ShortsRequest(video_id='V1',manifest_version=1,source_checksum='abc',title='Clip dọc',
                         start_s=.5,end_s=2.5,request_id='test',fit=changes.pop('fit','contain'),**changes)


def subtitles(path):
    path.write_text('1\n00:00:00,000 --> 00:00:01,000\nMột câu Việt.\n\n2\n00:00:01,000 --> 00:00:03,000\nGiữ trọn câu thứ hai.\n',encoding='utf-8')


def test_short_aligns_to_captions_and_fake_media_is_explicit(tmp_path):
    source = tmp_path / 'source.mp4'; source.write_bytes(b'fake')
    captions = tmp_path / 'source.srt'; subtitles(captions)
    cfg = MediaConfig(output_dir=tmp_path)
    result = render_short(source,captions,tmp_path / 'clip',request(),cfg,simulated=True)
    assert result['start_s'] == 0 and result['end_s'] == 3 and result['duration_s'] == 3
    assert 'Một câu Việt.' in (tmp_path / 'clip' / 'captions_v1.srt').read_text(encoding='utf-8')
    assert json.loads((tmp_path / 'clip' / 'final_v1.mp4').read_text(encoding='utf-8'))['simulated']
    with pytest.raises(ValueError): ShortsRequest.model_validate({**request().model_dump(),'end_s':.2})
    with pytest.raises(ValueError): ShortsRequest.model_validate({**request().model_dump(),'end_s':181})


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),reason='requires real FFmpeg')
def test_real_short_has_portrait_picture_audio_and_aligned_subtitles(tmp_path):
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=320x180:r=25','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
                    '-t','4','-c:v','libx264','-threads','2','-c:a','aac',str(source)],check=True)
    captions = tmp_path / 'source.srt'; subtitles(captions)
    cfg = MediaConfig(output_dir=tmp_path,video={'provider':'ffmpeg','resolution':'320x180','fps':25})
    result = render_short(source,captions,tmp_path / 'clip',request(),cfg)
    data = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(tmp_path / 'clip' / 'final_v1.mp4')]))
    video = next(s for s in data['streams'] if s['codec_type']=='video')
    assert (video['width'],video['height']) == (180,320) and abs(float(data['format']['duration'])-3)<.1
    assert result['simulated'] is False
    from studio.events import MediaAsset, Provenance, Scene, SceneManifest
    from studio.inspection import inspect_project
    rendered=tmp_path/'clip'/'final_v1.mp4'
    manifest=SceneManifest(video_id='V1',scenes=[Scene(scene_id='S1',order=0,narration='Lời đọc',visual_prompt='Mẫu',asset_refs={'scene_video':str(rendered),'scene_audio':str(rendered)})])
    final=MediaAsset(video_id='V1',kind='final_video',path=str(rendered),provider='ffmpeg',provenance=Provenance(generated_by='test'))
    report=inspect_project(manifest,final,cfg)
    assert report['sampled_scenes']==1 and report['findings']==[]



@pytest.mark.parametrize('real',[False,True])
def test_derived_project_has_own_reviews_and_publish_gate_and_idempotent_command(tmp_path,monkeypatch,real):
    from studio.factory import Factory
    if real and (not shutil.which('ffmpeg') or not shutil.which('ffprobe')):pytest.skip('requires real FFmpeg')
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path / 'policy.key'))
    video={'provider':'ffmpeg','resolution':'320x180','fps':25} if real else {'provider':'fake'}
    factory = Factory(tmp_path / 'studio.sqlite',MediaConfig(output_dir=tmp_path / 'out',video=video,production={'automation':'production'}),demo=True)
    try:
        factory.command('create',{'title':'Nguồn dài','target_minutes':.2})
        for _ in range(40): factory.cycle()
        parent = factory.snapshot()['projects'][0]
        final = next(a for a in reversed(parent['media']) if a['kind']=='final_video')
        body = {'video_id':parent['id'],'manifest_version':parent['manifest_version'],'source_checksum':final['checksum'],
                'title':'Clip dọc mới','start_s':0,'end_s':1,'request_id':'derived1'}
        factory.running=False
        first = factory.command('shorts',body); assert factory.command('shorts',body) == first
        factory.cycle();assert factory.commands.get(first)['status']=='pending'
        factory.running=True
        for _ in range(40): factory.cycle()
        child = next(p for p in factory.snapshot()['projects'] if p['id']!=parent['id'])
        assert child['title']=='Clip dọc mới' and child['format']=='short'
        assert child['origin']['source_video_id']==parent['id']
        assert child['request']['title']==child['title'] and child['request']['format']=='short'
        assert factory.orch.manifest(child['id']).aspect=='9:16'
        from studio.orchestrator import _with_provenance
        trigger=next(e for e in reversed(list(factory.bus.replay('media-assets',child['id']))) if e.payload['kind']=='final_video')
        data=_with_provenance(trigger,factory.orch)
        derived=data['derivation'];assert derived['source_manifest_version']==parent['manifest_version']
        assert derived['source_sha256'] in trigger.payload['provenance']['prompt_ref']
        assert trigger.payload['provenance']['generated_by']==('ffmpeg:shorts-excerpt' if real else 'fake:shorts-excerpt')
        poster=next(a for a in data['assets'] if a['kind']=='thumbnail')
        assert poster['provenance']['generated_by']=='studio:thumbnail-reuse'
        assert derived['output_sha256'] in poster['provenance']['prompt_ref']
        assert any(a['kind']=='final_video' and a['checksum']==derived['source_sha256'][:16] for a in data['derivation_sources'])
        legacy={k:v for k,v in derived.items() if k not in {'source_manifest_version','source_caption_sha256','output_sha256'}}
        factory.orch._audit('clip.derived',legacy,video_id=child['id'])
        assert _with_provenance(trigger,factory.orch)['source_manifest']['version']==parent['manifest_version']
        assert 'PUB-'+parent['id'] in factory.orch.gate.pending and 'PUB-'+child['id'] in factory.orch.gate.pending
        assert len(factory.snapshot()['projects'])==2
        with pytest.raises(ValueError):factory.command('shorts',{**body,'title':'different'})
        count=len(list(factory.bus.replay('media-assets')))
        from studio.shorts import create_short
        create_short(factory.orch,body,first)
        assert len(list(factory.bus.replay('media-assets')))==count
    finally: factory.close()


def test_failed_derived_review_holds_selected_excerpt_without_rewriting_it(tmp_path,monkeypatch):
    from studio.factory import Factory
    from studio.fakes import make_scripted_client
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    f=Factory(tmp_path/'db',MediaConfig(output_dir=tmp_path/'out',production={'automation':'production'}),demo=True)
    try:
        f.command('create',{'title':'Parent','target_minutes':.2})
        for _ in range(40):f.cycle()
        parent=f.snapshot()['projects'][0];asset=next(a for a in reversed(parent['media']) if a['kind']=='final_video')
        f.orch.runner.client=make_scripted_client(rights_verdict='block')
        f.command('shorts',{'video_id':parent['id'],'manifest_version':parent['manifest_version'],'source_checksum':asset['checksum'],
            'title':'Selected excerpt','start_s':0,'end_s':1,'request_id':'hold'})
        for _ in range(40):f.cycle()
        child=next(p for p in f.snapshot()['projects'] if p['id']!=parent['id'])
        assert child['state']=='in_review' and child['reviews']['rights']['verdict']=='block'
        assert len(list(f.bus.replay('video-briefs',child['id'])))==1
        assert len(list(f.bus.replay('scripts',child['id'])))==1
        assert 'PUB-'+child['id'] not in f.orch.gate.pending
    finally:f.close()


def test_short_receipt_reuse_and_invalid_caption_ranges(tmp_path):
    from studio.shorts import read_captions
    source=tmp_path/'source';source.write_bytes(b'fixture');captions=tmp_path/'captions';subtitles(captions)
    cfg=MediaConfig(output_dir=tmp_path);destination=tmp_path/'clip'
    first=render_short(source,captions,destination,request(),cfg,simulated=True)
    assert render_short(source,captions,destination,request(),cfg,simulated=True)==first
    (destination/'final_v1.mp4').write_bytes(b'corrupt')
    assert render_short(source,captions,destination,request(),cfg,simulated=True)==first
    captions.write_text('invalid')
    with pytest.raises(ValueError):read_captions(captions)
    subtitles(captions)
    later=ShortsRequest.model_validate({**request().model_dump(),'start_s':5,'end_s':6})
    with pytest.raises(ValueError):render_short(source,captions,destination,later,cfg,simulated=True)
    captions.write_text('1\n00:00:00,000 --> 00:04:00,000\nLời quá dài\n',encoding='utf-8')
    with pytest.raises(ValueError):render_short(source,captions,destination,request(),cfg,simulated=True)


def test_short_render_failures_leave_existing_final(tmp_path,monkeypatch):
    from types import SimpleNamespace

    import studio.shorts as module
    source=tmp_path/'source';source.write_bytes(b'fixture');captions=tmp_path/'captions';subtitles(captions)
    cfg=MediaConfig(output_dir=tmp_path);destination=tmp_path/'clip';destination.mkdir()
    final=destination/'final_v1.mp4';final.write_bytes(b'keep')
    def invalid(argv,**kwargs):
        if 'ffprobe' in argv[0]:return SimpleNamespace(stdout=b'{"streams":[],"format":{"duration":3}}')
        return SimpleNamespace()
    monkeypatch.setattr(module.subprocess,'run',invalid)
    with pytest.raises(ValueError):render_short(source,captions,destination,request(fit='cover'),cfg)
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(OSError('ffmpeg unavailable')))
    with pytest.raises(ValueError):render_short(source,captions,destination,request(),cfg)
    assert final.read_bytes()==b'keep'


def test_short_source_changed_during_render_never_replaces_final(tmp_path,monkeypatch):
    import studio.shorts as module
    source=tmp_path/'source';source.write_bytes(b'fixture');captions=tmp_path/'captions';subtitles(captions)
    original=module.digest;calls=0
    def digest(path):
        nonlocal calls
        if path==source:
            calls+=1
            if calls>1:return 'source changed'
        return original(path)
    monkeypatch.setattr(module,'digest',digest)
    destination=tmp_path/'clip'
    with pytest.raises(ValueError,match='thay đổi'):render_short(source,captions,destination,request(),MediaConfig(output_dir=tmp_path),simulated=True)
    assert not (destination/'final_v1.mp4').exists()
