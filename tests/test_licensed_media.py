"""Local footage and music enter the same provenance/review path as generated media."""
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from studio.bus import InMemoryBus
from studio.events import Scene, SceneManifest
from studio.media import MediaConfig, MediaError, make_media
from studio.renderer import Renderer


def setup(tmp_path):
    uploads = tmp_path / "uploads"; uploads.mkdir()
    (uploads / "clip.mp4").write_bytes(b"local-footage")
    (uploads / "music.wav").write_bytes(b"local-music")
    catalog = {"assets": {
        "sea": {"kind": "video", "path": "clip.mp4", "license": "owned", "attribution": "Studio crew"},
        "bed": {"kind": "music", "path": "music.wav", "license": "cc-by", "attribution": "Artist / CC BY 4.0",
                "source_url": "https://example.org/track"}}}
    (uploads / "library.yaml").write_text(yaml.safe_dump(catalog), encoding="utf-8")
    cfg = MediaConfig(output_dir=tmp_path, production={"library": "library.yaml", "scenes": {"V1": {"S1": "sea"}},
                                                   "music": {"V1": "bed"}})
    suite = make_media(cfg); suite.image.generate = Mock(wraps=suite.image.generate)
    m = SceneManifest(video_id="V1", scenes=[Scene(scene_id="S1", order=0, narration="Chào bạn.", visual_prompt="sea")])
    return Renderer(InMemoryBus(), suite, tmp_path), m, catalog


def test_footage_skips_generation_and_copies_with_provenance(tmp_path):
    r, m, _ = setup(tmp_path); assets = r.render(m)
    visual = next(a for a in assets if a.kind == "scene_video")
    assert r.media.image.generate.call_count == 0
    assert visual.provenance.license == "owned" and visual.provenance.attribution == "Studio crew"
    assert Path(visual.path).is_relative_to(tmp_path / "V1")
    assert m.scenes[0].asset_refs["scene_video"] == visual.path
    final = r.finalize(m)
    assert final.kind == "final_video"
    music = [e.payload for e in r.bus.replay("media-assets") if e.payload["kind"] == "music"]
    assert music[0]["provenance"]["license"] == "cc-by"
    assert music[0]["provenance"]["source_url"] == "https://example.org/track"
    assert "Artist" in Path(final.path).read_text()


@pytest.mark.parametrize("change", [
    {"path": "../outside.mp4"}, {"license": "unknown"}, {"kind": "music"},
    {"license": "cc-by", "attribution": ""}, {"path": "missing.mp4"},
])
def test_invalid_library_entry_fails_before_paid_work(tmp_path, change):
    r, m, catalog = setup(tmp_path)
    catalog["assets"]["sea"].update(change)
    (tmp_path / "uploads" / "library.yaml").write_text(yaml.safe_dump(catalog), encoding="utf-8")
    r.media.tts.synthesize = Mock(side_effect=AssertionError("no paid work before validation"))
    with pytest.raises(MediaError): r.render(m)


def test_catalog_and_symlink_cannot_escape_upload_root(tmp_path):
    r, m, _ = setup(tmp_path)
    r.media.cfg.production["library"] = "../outside.yaml"
    with pytest.raises(MediaError, match="upload"):
        r.render(m)


def test_postprocessing_settings_cannot_silently_disable_captions(tmp_path):
    r, m, _ = setup(tmp_path); r.media.cfg.video["burn_captions"] = True
    r.render(m); final = r.finalize(m)
    assert "captions" in Path(final.path).read_text()


def test_finishing_failure_preserves_the_entire_published_final_package(tmp_path, monkeypatch):
    r, m, _ = setup(tmp_path); r.media.cfg.video["burn_captions"] = True
    r.render(m); previous = r.finalize(m)
    records = list(r.bus.replay("media-assets"))
    old_files = {e.payload["path"]: Path(e.payload["path"]).read_bytes() for e in records
                 if e.payload["kind"] in {"final_video", "captions", "music"}}
    m.scenes[0].narration = "A changed caption that must not replace the published subtitle."
    m.scenes[0].duration_s = 9
    (tmp_path / "uploads" / "music.wav").write_bytes(b"new soundtrack")
    monkeypatch.setattr("studio.renderer.finish", Mock(side_effect=MediaError("finishing interrupted")))
    with pytest.raises(MediaError, match="interrupted"):
        r.finalize(m)
    assert all(Path(path).read_bytes() == content for path, content in old_files.items())
    assert len(list(r.bus.replay("media-assets"))) == len(records)
    assert previous.checksum
    assert not list((tmp_path / m.video_id).glob(".finalize-*"))


def test_invalid_finishing_and_unknown_scene_fail_loudly(tmp_path):
    from studio.production import finish
    r,m,_=setup(tmp_path)
    r.media.cfg.production['scenes']['V1']={'missing':'sea'}
    with pytest.raises(MediaError,match='unknown scenes'): r.render(m)
    with pytest.raises(MediaError,match='music_volume'): finish(r.media.video,tmp_path/'v.mp4',None,tmp_path/'m.wav','',2)
    with pytest.raises(MediaError,match='does not support'): finish(object(),tmp_path/'v.mp4',None,tmp_path/'m.wav','')


def test_cc_by_credit_is_added_to_publish_description_once(tmp_path):
    from studio.events import Envelope, MetadataPackage
    from studio.fakes import make_scripted_client
    from studio.orchestrator import Orchestrator, StepResult
    r,m,_=setup(tmp_path)
    o=Orchestrator(r.bus,make_scripted_client(),media=r.media)
    r.render(m);final=r.finalize(m)
    metadata=MetadataPackage(video_id='V1',title='Video',description='A story')
    env=Envelope(topic='metadata-packages',key='V1',actor='seo-optimizer',payload=metadata.model_dump())
    o.bus.publish(env)
    o._after_final(m,None,final,StepResult('test','cut-lists','V1'))
    latest=o.bus.latest('metadata-packages','V1')
    assert latest.payload['description'].count('Artist / CC BY 4.0')==1
    assert 'https://example.org/track' in latest.payload['description']
    o._preflight(env,StepResult('test','metadata-packages','V1'))
    assert o.bus.latest('metadata-packages','V1').payload['description'].count('Artist / CC BY 4.0')==1
