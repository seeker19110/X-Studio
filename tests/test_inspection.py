import json
import sys
from pathlib import Path

from PIL import Image

from studio.events import MediaAsset, Provenance, Scene, SceneManifest
from studio.inspection import inspect_project
from studio.media import MediaConfig


def test_contact_sheet_and_review_command_are_bound_to_current_media(tmp_path):
    image = tmp_path / 'scene.png'; Image.new('RGB',(640,360),'#bb8833').save(image)
    video = tmp_path / 'final.mp4'; video.write_bytes(b'media fixture')
    manifest = SceneManifest(video_id='V1',scenes=[Scene(scene_id='S1',order=0,narration='Lời đọc',visual_prompt='Ảnh',asset_refs={'scene_image':str(image)})])
    final = MediaAsset(video_id='V1',kind='final_video',path=str(video),provider='ffmpeg',provenance=Provenance(generated_by='test'))
    script = "import json,sys; p=json.load(sys.stdin); print(json.dumps({'verdict':'block','findings':[{'level':'block','text':'Chữ không khớp','location':'S1@0.5s'}]}))"
    cfg = MediaConfig(output_dir=tmp_path,production={'review_command':[sys.executable,'-c',script]})
    result = inspect_project(manifest,final,cfg)
    assert Path(result['contact_sheet']).is_file() and result['manifest_version'] == 1
    assert result['findings'][0]['level'] == 'block'
    original = result['fingerprint']; video.write_bytes(b'changed media')
    changed = inspect_project(manifest,final,cfg)
    assert changed['fingerprint'] != original
    assert 'review_command' not in json.dumps(changed)


def test_invalid_review_output_blocks_automation_and_fake_is_explicit(tmp_path):
    manifest = SceneManifest(video_id='V1',scenes=[])
    video = tmp_path / 'final.mp4'; video.write_bytes(b'fake')
    final = MediaAsset(video_id='V1',kind='final_video',path=str(video),provider='fake',provenance=Provenance(generated_by='fake'))
    assert inspect_project(manifest,final,MediaConfig(output_dir=tmp_path))['simulated']
    final.provider = 'ffmpeg'
    cfg = MediaConfig(output_dir=tmp_path,production={'review_command':[sys.executable,'-c',"print('invalid json')"]})
    result = inspect_project(manifest,final,cfg)
    assert any(f['level']=='block' for f in result['findings'])


def test_review_requirement_change_invalidates_cached_report(tmp_path):
    manifest=SceneManifest(video_id='V1',scenes=[])
    video=tmp_path/'final.mp4';video.write_bytes(b'fixture')
    final=MediaAsset(video_id='V1',kind='final_video',path=str(video),provider='ffmpeg',provenance=Provenance(generated_by='test'))
    cfg=MediaConfig(output_dir=tmp_path)
    assert not inspect_project(manifest,final,cfg)['findings']
    cfg.production['review_required']=True
    assert any(f['level']=='block' for f in inspect_project(manifest,final,cfg)['findings'])


def test_replay_ignores_incomplete_inspection_evidence(tmp_path,monkeypatch):
    from studio.events import AuditLog, Envelope
    from studio.factory import Factory
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    cfg=MediaConfig(output_dir=tmp_path/'out')
    factory=Factory(tmp_path/'db',cfg,demo=True)
    audit=AuditLog(actor='orchestrator',action='inspection',video_id='V1',evidence=json.dumps({'video_id':'V1','available':True,'manifest_version':1}))
    factory.bus.publish(Envelope(topic='audit-log',key='V1',actor='orchestrator',payload=audit.model_dump()))
    invalid=AuditLog(actor='orchestrator',action='inspection',video_id='V2',evidence=json.dumps({'video_id':'V2','available':False,'manifest_version':1,'findings':[{}]}))
    factory.bus.publish(Envelope(topic='audit-log',key='V2',actor='orchestrator',payload=invalid.model_dump()))
    factory.close()
    reopened=Factory(tmp_path/'db',cfg,demo=True)
    try: assert not reopened.orch.inspections
    finally: reopened.close()


def test_missing_scene_preview_and_required_adapter_produce_findings(tmp_path,monkeypatch):
    import subprocess

    import studio.inspection as module
    video=tmp_path/'final.mp4';video.write_bytes(b'fixture')
    manifest=SceneManifest(video_id='V1',scenes=[Scene(scene_id='S1',order=0,narration='Lời',visual_prompt='Hình',asset_refs={'scene_video':str(video)})])
    final=MediaAsset(video_id='V1',kind='final_video',path=str(video),provider='ffmpeg',provenance=Provenance(generated_by='test'))
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(subprocess.TimeoutExpired('ffmpeg',30)))
    result=inspect_project(manifest,final,MediaConfig(output_dir=tmp_path,production={'review_required':True}))
    assert [f['level'] for f in result['findings']]==['warn','block']
    manifest.scenes=[]
    cfg=MediaConfig(output_dir=tmp_path,production={'review_command':[sys.executable,'-c',"print('{\"verdict\":\"block\"}')"]})
    monkeypatch.undo()
    assert inspect_project(manifest,final,cfg)['findings'][0]['level']=='block'
    cfg.production['review_timeout_s']=301
    assert inspect_project(manifest,final,cfg)['review_status']=='unavailable'
