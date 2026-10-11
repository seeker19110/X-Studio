"""Fault injection at production boundaries, without paid APIs or external publication."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from studio.autopilot import Autopilot
from studio.events import Envelope, Finding, Scene, SceneManifest
from studio.media import MediaConfig, MediaError, MediaResult, make_media
from studio.qc import QCReport
from studio.render_cache import RenderCache, digest


def publish_fixture(tmp_path, monkeypatch):
    file = tmp_path / 'final.mp4'; file.write_bytes(b'unit-test-media')
    prov = {'license':'cc-by', 'attribution':'Artist CC BY', 'source_url':'https://example.org/art'}
    assets = [Envelope(topic='media-assets', key='V', actor='renderer', payload={
        'kind':kind, 'path':str(file), 'provider':'local', 'checksum':digest(file)[:16],
        'duration_s':60, 'manifest_version':1, 'provenance':prov}) for kind in ['final_video','music']]
    m = SceneManifest(video_id='V', scenes=[Scene(scene_id='S', order=0, narration='Hello', visual_prompt='x',
                                                asset_refs={'scene_video':str(file)})])
    o = Mock(); o.renderer.media.cfg = MediaConfig(production={'automation':'publish'})
    o.gate.pending={'PUB-V':SimpleNamespace(kind='publish')}; o.gate.approvers=[]
    o.desk.ready_for_publish.return_value=True; o.desk.briefs={'V':SimpleNamespace(target_minutes=1)}
    o.bus.replay.return_value=assets; o.bus.latest.return_value=SimpleNamespace(payload={'description':'Artist CC BY'})
    o.manifest.return_value=m; o._chosen_thumbnail.return_value=str(file); o._captions_of.return_value=None
    o.preflights={'V':SimpleNamespace(findings=[])}; o.qcs={}
    report=QCReport(available=True, metrics={'duration_s':60})
    qc=Mock(return_value=report); monkeypatch.setattr('studio.autopilot.qc_video',qc)
    return Autopilot(o), o, file, assets, report


def test_publish_policy_accepts_measured_licensed_complete_package(tmp_path, monkeypatch):
    auto,o,_,_,report=publish_fixture(tmp_path,monkeypatch)
    assert auto.assess('PUB-V') == ''
    assert o.qcs['V'] is report
    report.findings=[Finding(level='warn',text='quiet')]
    assert auto.assess('PUB-V') == 'quiet'
    o.renderer.media.cfg.production['allow_qc_warnings']=True
    assert auto.assess('PUB-V') == ''
    report.findings=[Finding(level='block',text='bad')]
    assert auto.assess('PUB-V') == 'bad'


@pytest.mark.parametrize('fault,phrase', [
    ('not_ready','ba review'), ('manifest','manifest'), ('license','bản quyền'), ('changed','thay đổi'),
    ('preflight','preflight'), ('credit','CC-BY'), ('unmeasured','chưa đo'), ('too_long','giới hạn thời lượng'),
    ('target','lệch'), ('size','256 GB')])
def test_publish_policy_holds_bad_packages(tmp_path,monkeypatch,fault,phrase):
    auto,o,file,assets,report=publish_fixture(tmp_path,monkeypatch)
    if fault=='not_ready': o.desk.ready_for_publish.return_value=False
    if fault=='manifest': o.manifest.return_value=None
    if fault=='license': assets[0].payload['provenance']['license']='unknown'
    if fault=='changed': file.write_bytes(b'tampered')
    if fault=='preflight': o.preflights={}
    if fault=='credit': o.bus.latest.return_value=None
    if fault=='unmeasured': report.metrics={}
    if fault=='too_long': report.metrics={'duration_s':43201}
    if fault=='target': report.metrics={'duration_s':40}
    if fault=='size': monkeypatch.setattr(Path,'stat',lambda *_args,**_kw: SimpleNamespace(st_size=257*1024**3,st_mode=0o100644))
    assert phrase in auto.assess('PUB-V')


def test_render_rejects_empty_outputs_and_invalid_controls(tmp_path):
    with pytest.raises(MediaError,match='empty'):
        RenderCache(tmp_path/'cache').produce('x',tmp_path/'out.wav',lambda p: MediaResult(p,'test','test'))
    with pytest.raises(ValueError):
        from studio.timeline import caption_pages
        caption_pages([],max_chars=0)
    with pytest.raises(MediaError): make_media(MediaConfig(video={'provider':'ffmpeg'},render={'mode':'invalid'}))
    from studio.long_render import BoundedAssembler
    with pytest.raises(MediaError): BoundedAssembler().assemble([],tmp_path/'x.mp4',30,'100x100')


def test_locked_checksum_is_verified_and_assetless_scenes_fail(tmp_path):
    from studio.bus import InMemoryBus
    from studio.renderer import Renderer
    r=Renderer(InMemoryBus(),make_media(MediaConfig(output_dir=tmp_path)))
    m=SceneManifest(video_id='V',scenes=[Scene(scene_id='S',order=0,narration='Hello',visual_prompt='x')])
    with pytest.raises(ValueError,match='asset'): r._segments(m)
    r.render(m); m.scenes[0].locked=True
    Path(m.scenes[0].asset_refs['scene_audio']).write_bytes(b'tampered')
    with pytest.raises(MediaError,match='checksum'): r.render(m)


def test_motion_engine_is_integrated_and_missing_font_is_explicit(tmp_path,monkeypatch):
    from studio.bus import InMemoryBus
    from studio.media import FFmpegAssembler
    from studio.motion import MotionSpec, render_motion
    from studio.renderer import Renderer
    cfg=MediaConfig(output_dir=tmp_path,production={'graphics':{'V':{'S':{'template':'title','title':'CHÀO BẠN'}}}})
    m=SceneManifest(video_id='V',scenes=[Scene(scene_id='S',order=0,narration='Hello',visual_prompt='x')])
    r=Renderer(InMemoryBus(),make_media(cfg))
    with pytest.raises(MediaError,match='ffmpeg'): r.render(m)
    cfg.video={'provider':'ffmpeg','resolution':'160x90','fps':10};cfg.render={'mode':'bounded','threads':1}
    r=Renderer(InMemoryBus(),make_media(cfg)); assets=r.render(m)
    assert any(a.kind=='scene_video' for a in assets)
    assert 'scene_video' in m.scenes[0].asset_refs
    monkeypatch.setattr('studio.motion.find_font',lambda *_args:None)
    with pytest.raises(MediaError,match='font'): render_motion(MotionSpec(template='title',title='a'),tmp_path/'bad.mp4',FFmpegAssembler(),1,10,'100x100')


def test_policy_receipt_malformed_is_ignored():
    from studio.gate_cli import trusted_decision
    e=Envelope(topic='audit-log',key='policy:studio',actor='policy:studio',payload={'action':'gate.decide','evidence':'{broken'})
    assert trusted_decision(e) is None


def test_optional_web_cache_prunes_old_entries_and_ignores_storage_errors(tmp_path, monkeypatch):
    from studio.tools import WebTools
    monkeypatch.setattr('studio.tools._resolve', lambda _host: ['8.8.8.8'])
    fetch=Mock(return_value=(200,'text/plain','https://example.org/',b'hello'))
    cache=tmp_path/'cache';cache.mkdir()
    for n in range(257): (cache/f'{n}.json').write_text('{}')
    web=WebTools(fetcher=fetch,cache_dir=cache)
    assert 'hello' in web.web_fetch('https://example.org/')
    assert len(list(cache.glob('*.json'))) == 256
    blocked=tmp_path/'file';blocked.write_text('not a directory')
    web=WebTools(fetcher=fetch,cache_dir=blocked)
    assert 'hello' in web.web_fetch('https://example.org/')


def test_factory_recovers_lease_after_constructor_failure(tmp_path,monkeypatch):
    from studio.factory import Factory
    monkeypatch.setattr('studio.factory.make_client',Mock(side_effect=ValueError('missing provider')))
    cfg=MediaConfig(output_dir=tmp_path/'out');db=tmp_path/'db.sqlite'
    with pytest.raises(ValueError): Factory(db,cfg)
    f=Factory(db,cfg,demo=True);f.close()


def test_factory_errors_gates_stream_boundary_and_worker(tmp_path,monkeypatch):
    from studio.factory import Factory
    from studio.gates import GateRequest
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    f=Factory(tmp_path/'db.sqlite',MediaConfig(output_dir=tmp_path/'out'),demo=True)
    with pytest.raises(ValueError): f.command('unknown',{})
    with pytest.raises(ValueError): f.command('gate',{'decision':'unknown'})
    f.orch.gate.request(GateRequest(kind='escalation',subject_id='ESC-V',created_by='supervisor',checklist=[]))
    f.command('gate',{'subject_id':'ESC-V','decision':'hold','request_at':'stale'});f.cycle()
    assert f.commands.recent()[0]['status']=='error'
    f.command('gate',{'subject_id':'ESC-V','decision':'hold'});f.cycle()
    outside=tmp_path/'outside.mp4';outside.write_bytes(b'a')
    event=Envelope(topic='media-assets',key='V',actor='renderer',payload={'video_id':'V','kind':'final_video','path':str(outside),'provenance':{'generated_by':'test'}})
    f.bus.publish(event);assert f.media_path(event.event_id) is None
    inside=tmp_path/'out'/'safe.mp4';inside.parent.mkdir(exist_ok=True);inside.write_bytes(b'a')
    event=event.model_copy(update={'event_id':'inside','payload':{**event.payload,'path':str(inside)}})
    f.bus.publish(event);assert f.media_path('inside')==inside
    def fail():
        f.stop.set();raise ValueError('worker error')
    f.cycle=fail;f.work()
    assert 'worker error' in f.snapshot()['worker_error']
    f.close()
