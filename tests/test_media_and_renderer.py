import shutil

import pytest

from studio.bus import InMemoryBus
from studio.events import CutList, Repair, Scene, SceneManifest, ThumbnailSpec, ThumbnailVariant
from studio.media import FFmpegAssembler, MediaConfig, MediaError, make_media
from studio.renderer import Renderer


def _manifest(vid="V1"):
    return SceneManifest(video_id=vid, scenes=[
        Scene(scene_id="S1", order=0, narration="Câu một có năm từ.", visual_prompt="bàn làm việc"),
        Scene(scene_id="S2", order=1, narration="Câu hai dài hơn một chút nữa.", visual_prompt="sơ đồ")])


def test_fake_media_renders_assets_with_provenance(tmp_path):
    bus = InMemoryBus(); r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    m = _manifest(); assets = r.render(m)
    kinds = sorted(a.kind for a in assets)
    assert kinds == ["draft_video", "scene_audio", "scene_audio", "scene_image", "scene_image"]
    for a in assets:
        assert a.checksum and a.provenance.generated_by.startswith("fake:") and (tmp_path / "V1").exists()
    assert all(len(s.asset_refs) == 2 for s in m.scenes)
    assert len(list(bus.replay("media-assets"))) == 5
    assert any(e.payload["action"] == "render.draft" for e in bus.replay("audit-log"))


def test_cutlist_repairs_only_touched_scene_and_respects_lock(tmp_path):
    bus = InMemoryBus(); r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    m = _manifest(); r.render(m)
    before = dict(m.scenes[0].asset_refs)
    cut = CutList(video_id="V1", manifest_version=1, decision="repair", repairs=[
        Repair(scene_id="S2", action="regenerate_image", reason="tối", new_visual_prompt="sơ đồ sáng"),
        Repair(scene_id="S1", action="lock", reason="ok")])
    new = r.apply_cutlist(m, cut)
    assert new.version == 2 and new.scenes[0].locked and new.scenes[0].asset_refs == before
    assert new.scenes[1].visual_prompt == "sơ đồ sáng" and "v2" in new.scenes[1].asset_refs["scene_image"]
    assert new.scenes[1].asset_refs["scene_audio"] == m.scenes[1].asset_refs["scene_audio"]  # audio không sinh lại
    # cảnh đã khoá không bị sinh lại dù được yêu cầu
    cut2 = CutList(video_id="V1", manifest_version=2, decision="repair",
                   repairs=[Repair(scene_id="S1", action="regenerate_both", reason="thử")])
    new2 = r.apply_cutlist(new, cut2)
    assert new2.scenes[0].asset_refs == before
    fin = r.finalize(new2, order=["S2", "S1"])
    assert fin.kind == "final_video" and fin.duration_s and fin.duration_s > 0


def test_thumbnails_and_unknown_provider(tmp_path):
    bus = InMemoryBus(); r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    out = r.thumbnails(ThumbnailSpec(video_id="V1", variants=[ThumbnailVariant(variant_id="A", prompt="p", overlay_text="X"),
                                                              ThumbnailVariant(variant_id="B", prompt="q", overlay_text="Y")]))
    assert [a.variant_id for a in out] == ["A", "B"] and all(a.kind == "thumbnail" for a in out)
    with pytest.raises(MediaError):
        make_media(MediaConfig(tts={"provider": "elevenlabs"}))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg trên PATH")
def test_ffmpeg_assembles_fake_assets(tmp_path):
    cfg = MediaConfig(output_dir=tmp_path, video={"provider": "ffmpeg", "fps": 24, "resolution": "320x180"})
    bus = InMemoryBus(); r = Renderer(bus, make_media(cfg), tmp_path)
    assets = r.render(_manifest("V9"))
    draft = next(a for a in assets if a.kind == "draft_video")
    assert draft.provider == "ffmpeg" and (tmp_path / "V9" / "draft_v1.mp4").stat().st_size > 1000
    FFmpegAssembler()  # có trên PATH


def test_replace_asset_only_accepts_files_inside_upload_dir(tmp_path):
    bus = InMemoryBus(); r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    assert r.upload_dir == tmp_path / "uploads"
    m = _manifest(); r.render(m); before = dict(m.scenes[1].asset_refs)
    outside = tmp_path / "secret.png"; outside.write_bytes(b"x")
    (tmp_path / "uploads").mkdir(); inside = tmp_path / "uploads" / "ok.png"; inside.write_bytes(b"y")
    cut = CutList(video_id="V1", manifest_version=1, decision="repair", repairs=[
        Repair(scene_id="S2", action="replace_asset", reason="x", replacement_path=str(outside)),
        Repair(scene_id="S2", action="replace_asset", reason="x", replacement_path="../secret.png"),
        Repair(scene_id="S2", action="replace_asset", reason="x", replacement_path="/etc/passwd")])
    new = r.apply_cutlist(m, cut)
    assert new.scenes[1].asset_refs == before and not new.scenes[1].locked
    rejected = [e.payload for e in bus.replay("audit-log") if e.payload["action"] == "replace_asset.rejected"]
    assert len(rejected) == 3
    new2 = r.apply_cutlist(new, CutList(video_id="V1", manifest_version=2, decision="repair", repairs=[
        Repair(scene_id="S2", action="replace_asset", reason="x", replacement_path="ok.png")]))
    assert new2.scenes[1].asset_refs["scene_image"] == str(inside.resolve()) and new2.scenes[1].locked


def test_ffmpeg_concat_list_escapes_single_quotes(tmp_path, monkeypatch):
    import subprocess

    from studio import media
    monkeypatch.setattr(media.shutil, "which", lambda b: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    out = tmp_path / "it's" / "o'k.mp4"
    FFmpegAssembler().assemble([(tmp_path / "i.png", tmp_path / "a.wav", 1.0)], out, 24, "320x180")
    lst = (out.parent / "o'k_concat.txt").read_text(encoding="utf-8")
    seg = out.parent / "o'k_seg000.mp4"
    assert lst == "file '" + seg.as_posix().replace("'", "'\\''") + "'\n"
    assert "it'\\''s" in lst and "o'\\''k_seg000" in lst


def test_ids_used_in_paths_must_be_safe():
    from pydantic import ValidationError

    from studio.events import ThumbnailVariant, VideoBrief
    for bad in ("../x", "a/b", "", "x" * 65, "ả"):
        with pytest.raises(ValidationError): SceneManifest(video_id=bad, scenes=[])
    with pytest.raises(ValidationError): Scene(scene_id="S 1", order=0, narration="n", visual_prompt="v")
    with pytest.raises(ValidationError): ThumbnailVariant(variant_id="A/..", prompt="p", overlay_text="t")
    with pytest.raises(ValidationError): VideoBrief(video_id="CH1/V1", channel_id="c", working_title="t", pillar="p", angle="a", audience="u")
    assert SceneManifest(video_id="CH1-V1_final", scenes=[]).video_id == "CH1-V1_final"


def test_openai_image_url_goes_through_url_boundary(tmp_path, monkeypatch):
    """URL trong phản hồi ảnh là dữ liệu không tin cậy: 127.0.0.1/169.254… bị chặn, không mở kết nối."""
    import json

    from studio.media import OpenAIImage
    monkeypatch.setattr("studio.media.urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("không được gọi")))
    gen = OpenAIImage(MediaConfig(api_key="k", image={"provider": "openai", "base_url": "https://api.example.org/v1"}))
    gen.http.post = lambda path, body: json.dumps({"data": [{"url": "http://169.254.169.254/latest/meta-data"}]}).encode()  # type: ignore[method-assign]
    with pytest.raises(MediaError, match="URL ảnh bị chặn"):
        gen.generate("p", "1024x1024", tmp_path / "a.png")
