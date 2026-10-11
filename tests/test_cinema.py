"""Film briefs persist into storyboard rendering, cache and scene repairs."""
import json
import shutil
import subprocess
import sys

import pytest
from pydantic import ValidationError

from studio.bus import InMemoryBus
from studio.events import CutList, Repair, Scene, SceneManifest
from studio.factory import Factory, ProjectRequest
from studio.media import MediaConfig, MediaError, load_media_config, make_media
from studio.renderer import Renderer


def test_cinema_validation_and_legacy_defaults():
    from studio.film import CinemaSpec

    assert ProjectRequest(title="Old project").production_type == "explainer"
    assert SceneManifest(video_id="V", scenes=[]).cinema == CinemaSpec()
    for values in ({"production_type": "unknown"}, {"visual_mode": "unknown"}, {"seed": -1},
                   {"seed": 2147483648}, {"character_bible": "x" * 3001}):
        with pytest.raises(ValidationError): ProjectRequest(title="Film", **values)
    assert CinemaSpec().direction() == []
    animated = CinemaSpec(production_type="animation", visual_style="stop motion", logline="A fox returns home",
                          character_bible="Fox: blue scarf")
    assert "Fox: blue scarf" in " ".join(animated.direction())
    assert "stop motion" in animated.prompt("forest", "global style")
    assert animated.seed_for("S1") == animated.seed_for("S1") != animated.seed_for("S2")
    assert "forest" in CinemaSpec().prompt("forest", "")


@pytest.mark.parametrize("kind", ["film", "animation"])
@pytest.mark.parametrize("mode", ["images", "generated_video"])
def test_cinema_project_survives_restart_and_reaches_manifest(tmp_path, monkeypatch, kind, mode):
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    db = tmp_path / "studio.sqlite"
    cfg = MediaConfig(output_dir=tmp_path / "output", scene_video={"provider": "fake"},
                      production={"automation": "production"})
    factory = Factory(db, cfg, demo=True)
    captured = []
    original = factory.orch.runner.generate

    def capture(agent, *args, **kwargs):
        if agent in {"script-writer", "production-manager"}: captured.append((agent, kwargs.get("extra")))
        return original(agent, *args, **kwargs)

    monkeypatch.setattr(factory.orch.runner, "generate", capture)
    try:
        factory.command("create", {"title": "Cáo về nhà", "production_type": kind, "visual_mode": mode,
                                   "visual_style": "watercolor", "logline": "Cáo tìm đường về rừng",
                                   "character_bible": "Cáo: khăn xanh", "seed": 42, "format": "short",
                                   "target_minutes": .8})
        for _ in range(80):
            factory.cycle()
            if any(g.kind == "publish" for g in factory.orch.gate.pending.values()): break
        state = factory.snapshot(); project = state["projects"][0]
        assert state["scene_video_provider"] == "fake" and state["scene_video_ready"]
        assert project["cinema"]["character_bible"] == "Cáo: khăn xanh"
        assert project["cinema"]["production_type"] == kind
        assert project["scenes"][0]["visual_prompt"]
        manifest = factory.orch.manifest(project["id"])
        assert manifest.cinema.seed == 42 and manifest.aspect == "9:16"
        assert manifest.cinema.visual_mode == mode
        assert all(extra["cinema"]["production_type"] == kind for _, extra in captured)
        assert {a["kind"] for a in project["media"]} >= {"scene_video" if mode == "generated_video" else "scene_image", "final_video"}
        assert not factory.orch.platform.videos
    finally:
        factory.close()
    reopened = Factory(db, cfg, demo=True)
    try:
        assert reopened.snapshot()["projects"][0]["cinema"] == project["cinema"]
    finally:
        reopened.close()


def test_video_mode_requires_provider_before_enqueue_or_tts(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    factory = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True)
    try:
        with pytest.raises(ValueError, match="scene_video"):
            factory.command("create", {"title": "Film", "production_type": "film", "visual_mode": "generated_video"})
        assert not factory.commands.pending() and not factory.snapshot()["scene_video_ready"]
    finally:
        factory.close()
    from studio.film import CinemaSpec
    renderer = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path / "render")))
    monkeypatch.setattr(renderer.media.tts, "synthesize", lambda *args: pytest.fail("TTS charged before validating video mode"))
    manifest = SceneManifest(video_id="V", cinema=CinemaSpec(visual_mode="generated_video"),
                             scenes=[Scene(scene_id="S", order=0, narration="hello", visual_prompt="forest")])
    with pytest.raises(MediaError, match="scene_video"): renderer.render_scene(manifest, manifest.scenes[0])


def test_generated_clip_cache_and_repair_preserve_cinema_and_locked_scene(tmp_path, monkeypatch):
    from studio.film import CinemaSpec
    cfg = MediaConfig(output_dir=tmp_path, scene_video={"provider": "fake", "duration_s": 5, "license": "unknown"})
    renderer = Renderer(InMemoryBus(), make_media(cfg))
    manifest = SceneManifest(video_id="V", cinema=CinemaSpec(production_type="film", visual_mode="generated_video",
                             character_bible="Fox: blue scarf", visual_style="cinematic", seed=19), scenes=[
        Scene(scene_id="S1", order=0, narration="The fox wakes up in the forest.", visual_prompt="wide forest"),
        Scene(scene_id="S2", order=1, narration="The fox looks for the way home.", visual_prompt="close fox")])
    calls = []
    original = renderer.media.scene_video.generate

    def capture(prompt, **kwargs):
        calls.append((prompt, kwargs.copy()))
        return original(prompt, **kwargs)

    monkeypatch.setattr(renderer.media.scene_video, "generate", capture)
    renderer.render(manifest)
    assert len(calls) == 2 and all("Fox: blue scarf" in prompt for prompt, _ in calls)
    assets = list(renderer.bus.replay("media-assets"))
    assert all(e.payload["provenance"]["license"] == "unknown" for e in assets if e.payload["kind"] == "scene_video")
    renderer.render(manifest)
    assert len(calls) == 2
    old = dict(manifest.scenes[0].asset_refs)
    repaired = renderer.apply_cutlist(manifest, CutList(video_id="V", manifest_version=1, decision="repair", repairs=[
        Repair(scene_id="S1", action="lock", reason="good"),
        Repair(scene_id="S2", action="regenerate_image", reason="change shot", new_visual_prompt="fox at home")]))
    assert len(calls) == 3 and repaired.cinema == manifest.cinema
    assert repaired.scenes[0].asset_refs == old and repaired.scenes[0].locked
    assert calls[-1][1]["seed"] != calls[1][1]["seed"]
    assert "scene_video" in repaired.scenes[1].asset_refs and "scene_image" not in repaired.scenes[1].asset_refs
    assert calls[0][1]["fps"] == 24 and calls[0][1]["resolution"] == "832x480"
    assert calls[0][1]["duration_s"] <= 5
    assert renderer.finalize(repaired).kind == "final_video"


def test_explicit_regeneration_changes_seed_and_identical_retry_reuses_clip(tmp_path, monkeypatch):
    from studio.film import CinemaSpec
    renderer = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path, scene_video={"provider": "fake"})))
    manifest = SceneManifest(video_id="V", cinema=CinemaSpec(visual_mode="generated_video"),
                             scenes=[Scene(scene_id="S", order=0, narration="Hello fox", visual_prompt="forest")])
    calls = []
    original = renderer.media.scene_video.generate

    def capture(prompt, **kwargs):
        calls.append(kwargs["seed"])
        return original(prompt, **kwargs)

    monkeypatch.setattr(renderer.media.scene_video, "generate", capture)
    renderer.render_scene(manifest, manifest.scenes[0])
    renderer.render_scene(manifest, manifest.scenes[0], audio=False, revision="repair-generation-1")
    renderer.render_scene(manifest, manifest.scenes[0], audio=False, revision="repair-generation-1")
    assert len(calls) == 2 and calls[0] != calls[1]


def test_scene_video_configuration_loads_and_provider_is_separate(tmp_path, monkeypatch):
    config = tmp_path / "media.yaml"
    config.write_text("scene_video:\n  provider: fake\n  fps: 16\n", encoding="utf-8")
    loaded = load_media_config(config)
    assert loaded.scene_video["fps"] == 16
    assert make_media(loaded).scene_video is not None
    monkeypatch.setenv("STUDIO_MEDIA_SCENE_VIDEO_PROVIDER", "none")
    assert make_media(load_media_config(config)).scene_video is None


@pytest.mark.parametrize("values", [{"duration_s": 0}, {"duration_s": -1}, {"duration_s": float("nan")},
                                   {"duration_s": float("inf")}, {"duration_s": 61}, {"fps": 0},
                                   {"fps": 24.5}, {"resolution": "832×480"}])
def test_invalid_generation_settings_fail_before_speech(tmp_path, monkeypatch, values):
    from studio.film import CinemaSpec
    renderer = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path,
                        scene_video={"provider": "fake", **values})))
    manifest = SceneManifest(video_id="V", cinema=CinemaSpec(visual_mode="generated_video"),
                             scenes=[Scene(scene_id="S", order=0, narration="hi", visual_prompt="forest")])
    monkeypatch.setattr(renderer.media.tts, "synthesize", lambda *args: pytest.fail("invalid duration reached speech"))
    with pytest.raises(MediaError, match="scene_video"): renderer.render_scene(manifest, manifest.scenes[0])


def test_image_mode_ignores_dormant_clip_settings(tmp_path):
    renderer = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path,
                         scene_video={"provider": "none", "duration_s": "invalid"})))
    manifest = SceneManifest(video_id="V", scenes=[Scene(scene_id="S", order=0, narration="hello", visual_prompt="forest")])
    assert {a.kind for a in renderer.render_scene(manifest, manifest.scenes[0])} == {"scene_audio", "scene_image"}


def test_cinema_schemas_describe_persistent_contract():
    from studio.events import VideoBrief
    from studio.media import ROOT
    for topic, model in (("video-briefs", VideoBrief), ("scene-manifests", SceneManifest)):
        schema = json.loads((ROOT / "topics" / "schemas" / f"{topic}.json").read_text())
        fields = schema["properties"]["payload"]["properties"]
        assert "cinema" in fields if topic == "scene-manifests" else "production_type" in fields
        assert set(model.model_fields) <= set(fields)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="requires real FFmpeg/ffprobe")
@pytest.mark.parametrize("aspect", ["16:9", "9:16"])
def test_local_animation_command_builds_playable_film_and_reuses_shot(tmp_path, aspect):
    from studio.film import CinemaSpec
    from studio.media import probe_duration
    program = tmp_path / "animate.py"
    counter = tmp_path / "calls.txt"
    program.write_text("import json, subprocess, sys\nfrom pathlib import Path\n"
                       "data = json.load(sys.stdin)\n"
                       f"counter = Path({str(counter)!r})\n"
                       "counter.write_text(counter.read_text() + '1' if counter.exists() else '1')\n"
                       "subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', "
                       "f'testsrc2=size={data[\"resolution\"]}:rate={data[\"fps\"]}', "
                       "'-t', str(data['duration_s']), '-an', '-c:v', 'libx264', '-threads', '1', "
                       "'-pix_fmt', 'yuv420p', sys.argv[1]], check=True)\n", encoding="utf-8")
    cfg = MediaConfig(output_dir=tmp_path / "output", scene_video={"provider": "command",
                      "command": [sys.executable, str(program), "{output}"], "duration_s": .5,
                      "fps": 10, "resolution": "160x90", "license": "owned"},
                      video={"provider": "ffmpeg", "fps": 10, "resolution": "320x180", "transition_s": 0},
                      render={"mode": "bounded", "threads": 1, "min_free_disk_bytes": 0})
    renderer = Renderer(InMemoryBus(), make_media(cfg))
    manifest = SceneManifest(video_id="Film", aspect=aspect, cinema=CinemaSpec(production_type="animation",
                             visual_mode="generated_video"), scenes=[Scene(scene_id="S1", order=0,
                             narration="A fox follows the light back home.", visual_prompt="moving forest")])
    assets = renderer.render(manifest)
    clip = next(a for a in assets if a.kind == "scene_video")
    assert clip.provider == "command" and clip.provenance.license == "owned"
    assert .4 <= probe_duration(clip.path) <= .6
    renderer.render(manifest)
    assert counter.read_text() == "1"
    final = renderer.finalize(manifest)
    report = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", final.path],
                            capture_output=True, text=True, check=True)
    info = json.loads(report.stdout)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == ((320, 180) if aspect == "16:9" else (180, 320))
    assert video["codec_name"] == "h264" and any(s["codec_type"] == "audio" for s in info["streams"])
    assert float(info["format"]["duration"]) >= manifest.scenes[0].duration_s > clip.duration_s
