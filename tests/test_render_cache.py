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


@pytest.mark.parametrize("reuse", ["unregistered", "other-video", "other-scene"])
def test_locked_assets_require_their_own_scene_provenance(tmp_path, reuse):
    r = renderer(tmp_path); m = manifest(); r.render(m)
    m.scenes[0].locked = True
    if reuse == "unregistered":
        unrelated = tmp_path / "unrelated.png"; unrelated.write_bytes(b"private local file")
        m.scenes[0].asset_refs["scene_image"] = str(unrelated)
    elif reuse == "other-video":
        m.video_id = "OTHER"
    else:
        m.scenes[0].scene_id = "OTHER"
    with pytest.raises(MediaError, match="provenance"):
        r.render(m)
    assert r.media.tts.synthesize.call_count == 1 and r.media.image.generate.call_count == 1


@pytest.mark.parametrize("change", [{"video_id": "OTHER"}, {"manifest_version": 2}])
def test_cutlist_cannot_repair_a_different_video_or_manifest_version(tmp_path, change):
    r = renderer(tmp_path); m = manifest(); r.render(m)
    params = {"video_id": m.video_id, "manifest_version": m.version, **change}
    with pytest.raises(MediaError, match="manifest"):
        r.apply_cutlist(m, CutList(**params, decision="repair", repairs=[
            Repair(scene_id="S1", action="regenerate_both", reason="fix")]))
    assert r.media.tts.synthesize.call_count == 1 and r.media.image.generate.call_count == 1


@pytest.mark.parametrize("repair", [
    {"action": "regenerate_image", "new_narration": "Changed speech"},
    {"action": "regenerate_audio", "new_visual_prompt": "Changed image"},
    {"action": "replace_asset"},
])
def test_cutlist_fields_cannot_desynchronize_manifest_and_assets(tmp_path, repair):
    r = renderer(tmp_path); m = manifest(); r.render(m)
    with pytest.raises(MediaError, match="repair"):
        r.apply_cutlist(m, CutList(video_id=m.video_id, manifest_version=m.version, decision="repair", repairs=[
            Repair(scene_id="S1", reason="fix", **repair)]))
    assert r.media.tts.synthesize.call_count == 1 and r.media.image.generate.call_count == 1


def test_immutable_cache_result_avoids_duplicate_and_repairs_corruption(tmp_path):
    from studio.media import MediaResult
    from studio.render_cache import RenderCache

    cache = RenderCache(tmp_path / 'cache')
    def generate(path):
        path.write_bytes(b'complete media')
        return MediaResult(path, 'fake', 'test')
    producer = Mock(side_effect=generate)
    requested = tmp_path / 'scene.mkv'
    first, hit = cache.produce('scene', requested, producer, materialize=False)
    assert not hit and first.path.parent == cache.root and not requested.exists()
    second, hit = cache.produce('scene', requested, producer, materialize=False)
    assert hit and second.path == first.path and producer.call_count == 1
    first.path.write_bytes(b'corrupt media')
    repaired, hit = cache.produce('scene', requested, producer, materialize=False)
    assert not hit and repaired.path.read_bytes() == b'complete media'
    assert producer.call_count == 2


def test_segment_budget_evicts_only_inactive_owned_blobs_oldest_first(tmp_path):
    import os

    from studio.media import MediaResult
    from studio.render_cache import RenderCache

    cache = RenderCache(tmp_path)
    def generate(path):
        path.write_bytes(b'x' * 10)
        return MediaResult(path, 'ffmpeg', 'bounded-scene')
    blobs = [cache.produce(key, tmp_path / 'unused.mkv', generate, materialize=False)[0].path
             for key in ('old', 'recent', 'active')]
    for i, path in enumerate(blobs): os.utime(path, (i + 1, i + 1))
    user = tmp_path / 'user.mkv'; user.write_bytes(b'user')
    (tmp_path / ('a' * 64 + '.mkv')).write_bytes(b'orphan')
    (tmp_path / ('b' * 64 + '.json')).write_text('{broken')
    (tmp_path / ('b' * 64 + '.mkv')).write_bytes(b'bad')
    foreign, _ = cache.produce('paid', tmp_path / 'unused.mkv',
        lambda p: (p.write_bytes(b'paid'), MediaResult(p, 'paid', 'bounded-scene'))[1], materialize=False)
    cache.prune_segments(20, {blobs[2]})
    assert not blobs[0].exists() and not blobs[0].with_suffix('.json').exists()
    assert all(p.exists() for p in blobs[1:]) and user.exists() and foreign.path.exists()
    with pytest.raises(MediaError, match=r'active.*budget'):
        cache.prune_segments(5, {blobs[2]})
    assert blobs[2].exists()
    cache.prune_segments(0, set())


@pytest.mark.parametrize('failed_suffix', ['.mkv', '.json'])
def test_segment_eviction_failure_is_actionable_media_error_and_preserves_active(tmp_path, monkeypatch, failed_suffix):
    from studio.media import MediaResult
    from studio.render_cache import RenderCache

    cache = RenderCache(tmp_path)
    def generate(path):
        path.write_bytes(b'x' * 10)
        return MediaResult(path, 'ffmpeg', 'bounded-scene')
    stale = cache.produce('stale', tmp_path / 'unused.mkv', generate, materialize=False)[0].path
    active = cache.produce('active', tmp_path / 'unused.mkv', generate, materialize=False)[0].path
    original_unlink = Path.unlink
    def fail_unlink(path, *args, **kwargs):
        if path == stale.with_suffix(failed_suffix):
            raise PermissionError('read-only checkpoint')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail_unlink)
    with pytest.raises(MediaError, match=r'evict.*permissions'):
        cache.prune_segments(10, {active})
    assert active.read_bytes() == b'x' * 10 and active.with_suffix('.json').exists()
    assert stale.exists() == (failed_suffix == '.mkv')
