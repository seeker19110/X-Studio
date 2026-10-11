"""Paid work survives restarts and repairs invalidate only the requested component."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from studio.bus import InMemoryBus
from studio.events import CutList, Repair, Scene, SceneManifest
from studio.media import MediaConfig, MediaError, make_media
from studio.renderer import Renderer


def manifest():
    return SceneManifest(video_id="CACHE", scenes=[
        Scene(scene_id="S1", order=0, narration="Xin chào các bạn.", visual_prompt="biển xanh")])


def renderer(tmp_path, bus=None):
    suite = make_media(MediaConfig(output_dir=tmp_path))
    suite.tts.synthesize = Mock(wraps=suite.tts.synthesize)
    suite.image.generate = Mock(wraps=suite.image.generate)
    return Renderer(bus or InMemoryBus(), suite, tmp_path)


def test_restart_reuses_paid_work_and_restores_missing_output(tmp_path):
    r = renderer(tmp_path); m = manifest(); r.render(m)
    Path(m.scenes[0].asset_refs["scene_audio"]).unlink()
    again = renderer(tmp_path, r.bus); again.render(manifest())
    assert again.media.tts.synthesize.call_count == 0
    assert again.media.image.generate.call_count == 0
    assert Path(m.scenes[0].asset_refs["scene_audio"]).is_file()
    assert any(e.payload["action"] == "render.cache" for e in r.bus.replay("audit-log"))


def test_resume_after_partial_failure_keeps_successful_audio(tmp_path):
    r = renderer(tmp_path); r.media.image.generate.side_effect = MediaError("provider unavailable")
    with pytest.raises(MediaError): r.render(manifest())
    again = renderer(tmp_path); again.render(manifest())
    assert again.media.tts.synthesize.call_count == 0
    assert again.media.image.generate.call_count == 1


def test_narration_and_provider_changes_invalidate_precisely(tmp_path):
    r = renderer(tmp_path); r.render(manifest())
    m = manifest(); m.scenes[0].narration = "Lời đọc hoàn toàn mới."
    r.render(m)
    assert r.media.tts.synthesize.call_count == 2
    assert r.media.image.generate.call_count == 1
    r.media.cfg.image["model"] = "another-model"; r.render(m)
    assert r.media.image.generate.call_count == 2
    assert r.media.tts.synthesize.call_count == 2


def test_explicit_repair_bypasses_cache_even_when_prompt_unchanged(tmp_path):
    r = renderer(tmp_path); m = manifest(); r.render(m)
    new = r.apply_cutlist(m, CutList(video_id=m.video_id, manifest_version=1, decision="repair",
                                   repairs=[Repair(scene_id="S1", action="regenerate_image", reason="artefact")]))
    assert r.media.image.generate.call_count == 2
    assert r.media.tts.synthesize.call_count == 1
    assert new.version == 2 and "v2" in new.scenes[0].asset_refs["scene_image"]


def test_locked_missing_asset_fails_instead_of_silently_rerendering(tmp_path):
    r = renderer(tmp_path); m = manifest(); r.render(m); m.scenes[0].locked = True
    Path(m.scenes[0].asset_refs["scene_image"]).unlink()
    with pytest.raises(MediaError, match="locked"):
        r.render(m)
    assert r.media.image.generate.call_count == 1


def test_cache_corruption_is_a_miss_and_secrets_are_not_persisted(tmp_path):
    r = renderer(tmp_path); r.media.cfg.tts["api_key"] = "private-key-test"; r.render(manifest())
    entries = list((tmp_path / ".cache" / "media").glob("*.json"))
    assert len(entries) == 2
    assert all("private-key-test" not in p.read_text() for p in entries)
    for p in entries: p.write_text("{broken", encoding="utf-8")
    r.render(manifest())
    assert r.media.tts.synthesize.call_count == 2 and r.media.image.generate.call_count == 2


def test_failed_repair_resumes_without_mutating_source_or_paying_twice(tmp_path):
    r = renderer(tmp_path); m = manifest(); r.render(m)
    original = m.model_dump()
    cut = CutList(video_id=m.video_id, manifest_version=1, decision="repair", repairs=[
        Repair(scene_id="S1", action="regenerate_both", reason="fix", new_narration="New narration")])
    r.media.image.generate.side_effect = MediaError("offline")
    with pytest.raises(MediaError): r.apply_cutlist(m, cut)
    assert m.model_dump() == original
    r.media.image.generate.side_effect = None
    r.apply_cutlist(m, cut)
    assert r.media.tts.synthesize.call_count == 2


def test_healthy_locked_scene_skips_paid_generation(tmp_path):
    r=renderer(tmp_path);m=manifest();r.render(m);m.scenes[0].locked=True
    r.render(m)
    assert r.media.tts.synthesize.call_count==1 and r.media.image.generate.call_count==1
