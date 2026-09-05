import json
import shutil
from itertools import pairwise

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


def test_ffmpeg_builds_one_graph_with_motion_transition_and_loudness(tmp_path, monkeypatch):
    """Cả video là MỘT lệnh ffmpeg: mỗi cảnh có chuyển động, các cảnh nối bằng xfade/acrossfade, âm lượng chuẩn hoá.
    Không `-t` cắt theo thời lượng ước lượng cho phần TIẾNG (chỉ đặt độ dài cho ảnh tĩnh), và không để lại file tạm."""
    import subprocess

    from studio import media
    runs: list[list[str]] = []
    monkeypatch.setattr(media.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(media.subprocess, "run",
                        lambda argv, *a, **k: (runs.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1])
    segs = [(tmp_path / f"i{i}.png", tmp_path / f"a{i}.wav", 3.0) for i in range(3)]
    r = FFmpegAssembler().assemble(segs, tmp_path / "o.mp4", 30, "1920x1080")
    argv = next(a for a in runs if "-filter_complex" in a)
    graph = argv[argv.index("-filter_complex") + 1]
    assert argv.count("-i") == 6  # mỗi cảnh hai đầu vào: ảnh + giọng đọc
    assert graph.count("xfade=transition=fade:duration=0.3") == 2 and graph.count("acrossfade=d=0.3") == 2
    assert "offset=3.050" in graph and "offset=6.100" in graph  # 3.35-0.3 rồi (3.35+3.35-0.3)-0.3
    assert "zoompan=" in graph and "crop=1920:1080:x=" in graph  # cảnh 0 zoom, cảnh 1 pan: hai cảnh liền kề khác kiểu
    assert "loudnorm=I=-14.0:TP=-1.0:LRA=11" in graph and "apad=pad_dur=0.35" in graph
    assert r.duration_s == 9.45  # 3×(3+0.35) − 2×0.3
    assert list(tmp_path.glob(".studio-*")) == [] and not list(tmp_path.glob("*concat*"))


def test_ffmpeg_one_scene_or_no_transition_falls_back_to_concat(tmp_path, monkeypatch):
    import subprocess

    from studio import media
    runs: list[list[str]] = []
    monkeypatch.setattr(media.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(media.subprocess, "run",
                        lambda argv, *a, **k: (runs.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1])
    one = [(tmp_path / "i.png", tmp_path / "a.wav", 2.0)]
    FFmpegAssembler().assemble(one, tmp_path / "one.mp4", 30, "1920x1080")
    g1 = runs[-1][runs[-1].index("-filter_complex") + 1]
    assert "xfade" not in g1 and "concat=" not in g1 and "[v0]" in g1
    two = [*one, (tmp_path / "i2.png", tmp_path / "a2.wav", 2.0)]
    FFmpegAssembler(transition_s=0).assemble(two, tmp_path / "cut.mp4", 30, "1920x1080")
    g2 = runs[-1][runs[-1].index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in g2 and "xfade" not in g2
    # tắt chuyển động và chuẩn hoá âm lượng
    FFmpegAssembler(motion="none", loudness_lufs=None).assemble(two, tmp_path / "plain.mp4", 30, "1920x1080")
    g3 = runs[-1][runs[-1].index("-filter_complex") + 1]
    assert "zoompan" not in g3 and "loudnorm" not in g3


def test_motion_rotates_so_neighbours_differ():
    from studio.media import motion_for
    kinds = [motion_for(i) for i in range(5)]
    assert all(a != b for a, b in pairwise(kinds)) and kinds[0] == kinds[4]
    assert motion_for(3, "none") == "static" and motion_for(1, "zoom_in") == "zoom_in"


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


# ---------- P0: khung hình, kích thước ảnh theo model, thời lượng thật, thumbnail chuẩn nền tảng ----------

def test_image_size_valid_for_model_and_aspect():
    """gpt-image-1 KHÔNG nhận 1792x1024 (đó là của dall-e-3): kích thước sai = HTTP 400 ngay lượt render thật đầu tiên,
    nên cấu hình sai được thay bằng kích thước hợp lệ cùng tỷ lệ."""
    from studio.media import image_size
    gpt = MediaConfig(image={"provider": "openai", "model": "gpt-image-1"})
    assert image_size(gpt, "16:9") == "1536x1024" and image_size(gpt, "9:16") == "1024x1536"
    gpt_bad = MediaConfig(image={"provider": "openai", "model": "gpt-image-1", "size": "1792x1024"})
    assert image_size(gpt_bad, "16:9") == "1536x1024" and image_size(gpt_bad, "9:16") == "1024x1536"
    assert image_size(MediaConfig(image={"model": "gpt-image-1", "size": "1024x1024"}), "16:9") == "1024x1024"  # hợp lệ → giữ
    d3 = MediaConfig(image={"provider": "openai", "model": "dall-e-3"})
    assert image_size(d3, "16:9") == "1792x1024" and image_size(d3, "9:16") == "1024x1792"
    assert image_size(MediaConfig(image={"model": "dall-e-2"}), "9:16") == "1024x1024"
    # provider nhận tỷ lệ (gemini/stability/replicate): tôn trọng cấu hình, mặc định hợp lệ cho mọi nhà
    assert image_size(MediaConfig(image={"provider": "gemini", "model": "imagen-4.0-generate-001", "size": "2048x1152"})) == "2048x1152"
    assert image_size(MediaConfig(image={"provider": "gemini", "model": "gemini-2.5-flash-image"}), "9:16") == "1024x1536"


def test_frame_size_follows_manifest_aspect():
    from studio.media import frame_size
    v = {"resolution": "1920x1080"}
    assert frame_size(v, "16:9") == "1920x1080" and frame_size(v, "9:16") == "1080x1920"  # Shorts không bị pad hai dải đen
    assert frame_size({"resolution": "1080x1920"}, "16:9") == "1920x1080"
    assert frame_size({}, "9:16") == "1080x1920" and frame_size({"resolution": "rác"}, "16:9") == "1920x1080"


def test_wrap_overlay_uppercases_and_reports_every_line():
    from studio.media import wrap_overlay
    assert wrap_overlay("6 giờ → 30 phút") == ["6 GIỜ → 30 PHÚT"]
    assert wrap_overlay("  ") == [] and wrap_overlay("một-từ-rất-dài-không-ngắt-được") == ["MỘT-TỪ-RẤT-DÀI-KHÔNG-NGẮT-ĐƯỢC"]
    assert len(wrap_overlay("ai dựng video cho người mới bắt đầu ngay hôm nay")) == 4  # trả đủ, người gọi cắt và ghi lại


def test_renderer_asks_for_valid_size_never_puts_text_in_scene_prompt(tmp_path):
    """Ảnh cảnh: kích thước theo model + tỷ lệ manifest; prompt luôn kèm điều cấm chữ (model ảnh viết sai dấu tiếng Việt)."""
    cfg = MediaConfig(output_dir=tmp_path, image={"provider": "fake", "model": "gpt-image-1"},
                      video={"provider": "fake", "resolution": "1920x1080"})
    suite = make_media(cfg); seen = []
    gen = suite.image.generate
    suite.image.generate = lambda prompt, size, out: (seen.append((prompt, size)), gen(prompt, size, out))[1]
    asm = []
    assemble = suite.video.assemble
    suite.video.assemble = lambda segs, out, fps, res: (asm.append((fps, res)), assemble(segs, out, fps, res))[1]
    bus = InMemoryBus(); r = Renderer(bus, suite, tmp_path)
    r.render(SceneManifest(video_id="V1", aspect="9:16", scenes=[
        Scene(scene_id="S1", order=0, narration="Một câu.", visual_prompt="bàn làm việc")]))
    assert seen[0][1] == "1024x1536" and seen[0][0].startswith("bàn làm việc") and "Không chữ" in seen[0][0]
    assert asm[0] == (30, "1080x1920")  # short render đúng khung dọc


def test_thumbnail_is_finished_by_code_not_by_the_image_model(tmp_path):
    """Model ảnh chỉ vẽ NỀN (không chữ); code phủ chữ, thu về 1280x720 và nén — asset publish là bản hoàn thiện."""
    cfg = MediaConfig(output_dir=tmp_path, image={"provider": "fake", "model": "gpt-image-1"})
    suite = make_media(cfg); prompts = []
    gen = suite.image.generate
    suite.image.generate = lambda prompt, size, out: (prompts.append(prompt), gen(prompt, size, out))[1]
    bus = InMemoryBus(); r = Renderer(bus, suite, tmp_path)
    out = r.thumbnails(ThumbnailSpec(video_id="V1", chosen="A", variants=[
        ThumbnailVariant(variant_id="A", prompt="đồng hồ bấm giờ", overlay_text="6 GIỜ → 30 PHÚT")]))
    assert "6 GIỜ" not in prompts[0] and "Không chữ" in prompts[0]  # chữ phủ KHÔNG đi vào prompt ảnh
    a = out[0]
    assert a.path.endswith("A.jpg") and a.kind == "thumbnail" and a.variant_id == "A"
    assert a.provenance.generated_by == "fake:fake-image" and a.checksum  # provenance vẫn là của ảnh nền
    assert (tmp_path / "V1" / "thumbnails" / "A_base.png").is_file()
    note = next(json.loads(e.payload["evidence"]) for e in bus.replay("audit-log") if e.payload["action"] == "thumbnail.finish")
    assert "fake" in note["A"]


def test_youtube_rejects_thumbnail_over_platform_limit(tmp_path):
    from studio.media import THUMBNAIL_MAX_BYTES
    from studio.platform import PlatformError, Tokens, TokenStore, YouTubePlatform
    st = TokenStore(tmp_path / "t.json")
    st.save(Tokens(access_token="a", refresh_token="r", client_id="c", client_secret="s",
                   expiry="2099-01-01T00:00:00+00:00", scopes=["a"]))
    big = tmp_path / "big.jpg"; big.write_bytes(b"x" * (THUMBNAIL_MAX_BYTES + 1))
    def boom(*a, **k): raise AssertionError("không được gọi API khi file đã quá nặng")
    with pytest.raises(PlatformError, match=r"2\.0 MB.*≤ 2 MB"):
        YouTubePlatform(st, fetcher=boom).set_thumbnail("vid1", big)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="cần ffmpeg + ffprobe")
def test_ffmpeg_never_cuts_the_narration_short(tmp_path):
    """Bảo hiểm cho lỗi tệ nhất của lớp render: giọng đọc dài hơn thời lượng ước lượng thì câu bị cụt giữa chừng.
    Cảnh phải dài bằng audio thật (5 s) + đệm, không phải bằng con số 1 s truyền vào."""
    import subprocess

    from studio.media import FakeImage, MediaConfig, write_wav
    img = FakeImage(MediaConfig()).generate("nền", "64x64", tmp_path / "i.png").path
    wav = tmp_path / "a.wav"; write_wav(wav, b"\x00\x00" * 8000 * 5, 8000)  # 5 giây thật
    out = tmp_path / "seg.mp4"
    FFmpegAssembler().assemble([(img, wav, 1.0)], out, 24, "640x360")       # ước lượng sai: 1 giây
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                                str(out)], capture_output=True, text=True).stdout)
    assert 5.3 < dur < 5.6  # 5 s giọng đọc + 0,35 s đệm cuối cảnh


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="cần ffmpeg + ffprobe")
def test_ffmpeg_thumbnail_is_1280x720_jpeg_with_vietnamese_overlay(tmp_path):
    import subprocess

    from studio.media import THUMBNAIL_MAX_BYTES, FakeImage, MediaConfig, find_font
    src = FakeImage(MediaConfig()).generate("nền", "64x64", tmp_path / "b.png").path
    out = tmp_path / "A.jpg"
    r = FFmpegAssembler().finish_thumbnail(src, out, text="6 giờ → 30 phút")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert probe.startswith("mjpeg,1280,720") and out.stat().st_size <= THUMBNAIL_MAX_BYTES
    assert "1280x720" in r.notes and ("chữ=1 dòng" in r.notes if find_font() else "KHÔNG phủ được chữ" in r.notes)
    assert list(tmp_path.glob(".studio-*")) == []  # thư mục tạm đã dọn
