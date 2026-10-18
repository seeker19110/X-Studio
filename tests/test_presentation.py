import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from studio.media import MediaConfig, MediaResult, make_media
from studio.speech import caption_chunks
from studio.timeline import Cue


def test_caption_pages_balance_lines_without_losing_words_or_splitting_vietnamese_phrases():
    for text,width in [('Sửa video mà không mất bản gốc?',26),
                       ('Hai: sao lưu sang thư mục mới, giữ bản gốc.',38),
                       ('Bốn: xem bản cuối và phụ đề rồi mới duyệt.',26)]:
        pages=caption_chunks(text,width)
        assert ' '.join(' '.join(p.split()) for p in pages)==text
        assert all(len(line)<=width for p in pages for line in p.splitlines())
        assert all(len(p.splitlines())!=2 or len(p.splitlines()[-1].split())>1 for p in pages)
        assert all(not (a.endswith('phụ') and b.startswith('đề')) for p in pages for a,b in zip(p.splitlines(),p.splitlines()[1:],strict=False))


def test_default_project_keeps_operator_boundaries_and_adds_quality_direction(tmp_path,monkeypatch):
    from studio.factory import Factory
    from studio.media import load_media_config
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    cfg=load_media_config(tmp_path/'no-config.yaml')
    assert cfg.video['resolution']=='1920x1080' and cfg.video['layout']=='editorial'
    f=Factory(tmp_path/'db',cfg,demo=True)
    try:
        f.command('create',{'title':'Chủ đề mới','boundaries':['Chỉ dùng tư liệu đã rõ nguồn']})
        f.cycle()
        event=list(f.bus.replay('channel-briefs'))[-1]
        assert 'Chỉ dùng tư liệu đã rõ nguồn' in event.payload['boundaries']
        assert any('câu ngắn' in text for text in event.payload['boundaries'])
    finally:f.close()


def test_native_layout_and_subtitles_use_each_aspect_and_model_text(tmp_path,monkeypatch):
    from studio.presentation import compose_frame, write_captions
    source=tmp_path/'source.png';Image.new('RGB',(600,400),'#227799').save(source)
    for size in ['640x360','360x640']:
        frame=tmp_path/(size+'.png')
        result=compose_frame(source,frame,size,'Một chủ đề mới','Nội dung mới có dấu.',1,4)
        assert result.provider=='studio'
        with Image.open(frame) as image:assert image.size==tuple(map(int,size.split('x')))
        out=tmp_path/(size+'.ass')
        write_captions([Cue('S',.15,1.5,'Phụ đề đúng dấu.\nDòng thứ hai.')],size,out)
        text=out.read_text(encoding='utf-8')
        assert 'PlayResX: '+size.split('x')[0] in text and r'Phụ đề đúng dấu.\NDòng thứ hai.' in text
    monkeypatch.setattr('studio.presentation.find_font',lambda *_args:None)
    with pytest.raises(ValueError,match='font'):
        compose_frame(source,tmp_path/'bad.png','640x360','title','heading',0,1)
    with pytest.raises(ValueError,match='font'):
        write_captions([], '640x360',tmp_path/'bad.ass')


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='requires FFmpeg')
def test_renderer_defaults_trim_timed_speech_and_keep_source_assets_for_portrait(tmp_path,monkeypatch):
    from studio.bus import InMemoryBus
    from studio.events import Scene, SceneManifest
    from studio.renderer import Renderer
    cfg=MediaConfig(output_dir=tmp_path,video={'provider':'ffmpeg','resolution':'320x180','fps':25},
                    render={'mode':'bounded'})
    suite=make_media(cfg)
    def speak(text,voice,path):
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
                        '-t','2','-threads','1',str(path)],check=True)
        return MediaResult(path,'test','timed',2.,words=[{'text':'Xin chào','start':.1,'duration':.5}],timing_source='provider')
    monkeypatch.setattr(suite.tts,'synthesize',speak)
    renderer=Renderer(InMemoryBus(),suite)
    m=SceneManifest(video_id='V',scenes=[Scene(scene_id='S',order=0,narration='Xin chào.',visual_prompt='Hình mới')])
    renderer.render(m);scene=m.scenes[0]
    layouts=[e.payload for e in renderer.bus.replay('media-assets','V') if e.payload['kind']=='scene_layout']
    assert layouts and layouts[0]['provenance']['generated_by']=='studio:editorial-layout-v1'
    assert layouts[0]['checksum']
    assert scene.duration_s<1.1 and Path(scene.asset_refs['scene_audio_source']).is_file()
    assert Path(scene.asset_refs['scene_layout']).is_file()
    with Image.open(scene.asset_refs['scene_layout']) as image:assert image.size==(320,180)
    portrait=renderer.presentation_frame(m,scene,'9:16')
    renderer.presentation_frame(m,scene,'9:16')
    registered=[e.payload for e in renderer.bus.replay('media-assets','V') if e.payload['kind']=='scene_layout']
    assert len(registered)==2
    assert all(a['provenance']['license']=='generated' for a in registered)
    assert all('source' in a['provenance']['prompt_ref'] for a in registered)
    with Image.open(portrait) as image:assert image.size==(180,320)
    final=renderer.finalize(m)
    assert Path(final.path).is_file()
    assert (tmp_path/'V'/'captions_v1.ass').is_file()
    assert any(e.payload.get('action')=='render.presentation' for e in renderer.bus.replay('audit-log'))
    from types import SimpleNamespace

    from studio.events import MediaAsset
    from studio.shorts import ShortsRequest, reflow_context
    cap=MediaAsset.model_validate(next(e.payload for e in renderer.bus.replay('media-assets','V') if e.payload['kind']=='captions'))
    request=ShortsRequest(video_id='V',manifest_version=1,source_checksum=final.checksum,title='Bản dọc',
                          start_s=0,end_s=1,request_id='timed')
    owner=SimpleNamespace(bus=renderer.bus,renderer=renderer,manifest=lambda _vid:m)
    context=reflow_context(owner,request,cap)
    assert context['captions'][0]['text']=='Xin chào.'
    assert context['captions'][0]['start']==0
    timing=Path(scene.asset_refs['scene_timing']);data=json.loads(timing.read_text());data['sha256']='changed'
    timing.write_text(json.dumps(data),encoding='utf-8')
    assert reflow_context(owner,request,cap)['captions']


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='requires FFmpeg')
def test_short_default_reflows_registered_scene_assets_and_preserves_source(tmp_path,monkeypatch):
    from studio.factory import Factory
    from studio.render_cache import digest
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    cfg=MediaConfig(output_dir=tmp_path/'out',video={'provider':'ffmpeg','resolution':'320x180','fps':25},
                    render={'mode':'bounded'},production={'automation':'production'})
    f=Factory(tmp_path/'db',cfg,demo=True)
    try:
        f.command('create',{'title':'Một câu chuyện mới','target_minutes':.1})
        for _ in range(40):f.cycle()
        parent=f.snapshot()['projects'][0];vid=parent['id']
        asset=next(e.payload for e in reversed(list(f.bus.replay('media-assets',vid))) if e.payload['kind']=='final_video')
        source_sha=digest(Path(asset['path']))
        from studio.events import Envelope, Script
        from studio.shorts import read_captions
        cap_path=next(e.payload['path'] for e in f.bus.replay('media-assets',vid) if e.payload['kind']=='captions')
        original_hook=' '.join(read_captions(Path(cap_path))[0].text.split())
        script=Script.model_validate(f.orch.latest('scripts',vid).payload)
        script.hook=original_hook
        f.bus.publish(Envelope(topic='scripts',key=vid,actor='console:editor',payload=script.model_dump()))
        command=f.command('shorts',{'video_id':vid,'manifest_version':parent['manifest_version'],
            'source_checksum':asset['checksum'],'title':'Bản dọc mới','start_s':0,'end_s':1,'request_id':'native'})
        for _ in range(40):f.cycle()
        assert f.commands.get(command)['status']=='done'
        child=next(p for p in f.snapshot()['projects'] if p['id']!=vid)
        assert child['origin']['layout']=='reflow'
        assert child['origin']['presentation_sources']
        from studio.events import Envelope
        from studio.orchestrator import _with_provenance
        evidence=_with_provenance(Envelope(topic='media-assets',key=child['id'],actor='renderer',payload={'video_id':child['id']}),f.orch)
        portrait_sources=[a for a in evidence['derivation_sources'] if a['kind']=='scene_layout' and '9x16' in a['path']]
        assert portrait_sources and all(a['checksum'] for a in portrait_sources)
        assert len(child['script']['hook'].split())<=15
        assert child['script']['hook']==original_hook
        assert digest(Path(asset['path']))==source_sha
        assert 'PUB-'+vid in f.orch.gate.pending and 'PUB-'+child['id'] in f.orch.gate.pending
        assert not list(f.bus.replay('publish-events'))
        output=cfg.output_dir/child['id']
        assert (output/'captions_v1.ass').is_file()
        receipt=json.loads((output/'derived.json').read_text())
        assert receipt['result']['layout']=='reflow'
        # Reflow must reject a changed raw source even when the registered final still matches.
        scene=f.orch.manifest(vid).scenes[0]
        Path(scene.asset_refs['scene_image']).write_bytes(b'changed')
        from studio.shorts import create_short
        with pytest.raises(ValueError,match='nguồn'):
            create_short(f.orch,{'video_id':vid,'manifest_version':parent['manifest_version'],
                'source_checksum':asset['checksum'],'title':'Invalid','start_s':0,'end_s':1,'request_id':'bad'},'bad')
        from studio.events import MediaAsset
        from studio.shorts import ShortsRequest, reflow_context, render_short
        cap=MediaAsset.model_validate(next(e.payload for e in f.bus.replay('media-assets',vid) if e.payload['kind']=='captions'))
        req=ShortsRequest(video_id=vid,manifest_version=parent['manifest_version'],source_checksum=asset['checksum'],
                         title='Empty',start_s=0,end_s=1,request_id='empty')
        assert reflow_context(f.orch,req.model_copy(update={'fit':'cover'}),cap) is None
        with pytest.raises(ValueError,match='tư liệu'):
            render_short(Path(asset['path']),Path(cap.path),tmp_path/'missing-sources',req,cfg)
        monkeypatch.setattr('studio.shorts.read_captions',lambda _path:[])
        with pytest.raises(ValueError,match='lời đọc'):reflow_context(f.orch,req,cap)
    finally:f.close()
