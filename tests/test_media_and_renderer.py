import json
import shutil
from itertools import pairwise
from pathlib import Path

import pytest

from studio.bus import InMemoryBus
from studio.events import CutList, Repair, Scene, SceneManifest, ThumbnailSpec, ThumbnailVariant
from studio.media import FFmpegAssembler, MediaConfig, MediaError, make_media
from studio.renderer import Renderer
from studio.sandbox import SubprocessSandbox


def test_graph_render_commits_only_a_complete_output(tmp_path, monkeypatch):
    monkeypatch.setattr("studio.media.shutil.which", lambda _: "/fake/ffmpeg")
    monkeypatch.setattr("studio.media.probe_duration", lambda _: 1.0)
    engine = FFmpegAssembler()
    out = tmp_path / "final.mp4"
    out.write_bytes(b"approved video")

    def interrupted(args, **kwargs):
        Path(args[-1]).write_bytes(b"incomplete replacement")
        raise MediaError("encoder interrupted")

    monkeypatch.setattr(engine, "_run", interrupted)
    with pytest.raises(MediaError, match="interrupted"):
        engine.assemble([(tmp_path / "image.png", tmp_path / "audio.wav", 1.0)], out, 30, "160x90")
    assert out.read_bytes() == b"approved video"
    assert not list(tmp_path.glob(".assemble-*"))

    monkeypatch.setattr(engine, "_run", lambda args, **kwargs: Path(args[-1]).write_bytes(b"complete replacement"))
    result = engine.assemble([(tmp_path / "image.png", tmp_path / "audio.wav", 1.0)], out, 30, "160x90")
    assert result.path == out and out.read_bytes() == b"complete replacement"


@pytest.mark.parametrize("order", [["S1"], ["S1", "S1"], ["S1", "missing"]])
def test_renderer_rejects_incomplete_or_duplicate_scene_order(tmp_path, order):
    r = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path)))
    m = _manifest(); r.render(m)
    with pytest.raises(MediaError, match="order"):
        r.finalize(m, order)
    with pytest.raises(MediaError, match="order"):
        r.apply_cutlist(m, CutList(video_id=m.video_id, manifest_version=m.version,
                                  decision="repair", order=order))


def test_duplicate_scene_ids_fail_before_media_generation(tmp_path):
    from unittest.mock import Mock

    r = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path)))
    m = _manifest(); m.scenes[1].scene_id = m.scenes[0].scene_id
    r.media.tts.synthesize = Mock(side_effect=AssertionError("duplicate manifest paid for audio"))
    with pytest.raises(MediaError, match="scene"):
        r.render(m)


@pytest.mark.parametrize("scenes,fps,duration", [(False, 30, 1), (True, 0, 1),
                                                (True, 30, float("nan")), (True, 30, float("inf")),
                                                (True, 30, -1)])
def test_graph_invalid_timing_fails_before_encoding(tmp_path, monkeypatch, scenes, fps, duration):
    monkeypatch.setattr("studio.media.shutil.which", lambda _: "/fake/ffmpeg")
    monkeypatch.setattr("studio.media.probe_duration", lambda _: None)
    engine = FFmpegAssembler()
    monkeypatch.setattr(engine, "_run", lambda *args, **kwargs: pytest.fail("invalid timing reached ffmpeg"))
    segments = [(tmp_path / "image.png", tmp_path / "audio.wav", duration)] if scenes else []
    with pytest.raises(MediaError, match="requires"):
        engine.assemble(segments, tmp_path / "final.mp4", fps, "160x90")


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
    # lệnh đi qua Sandbox: bơm `runner=` giả vào SubprocessSandbox, KHÔNG vá `subprocess.run` — nếu assembler
    # lén gọi thẳng subprocess thì `runs` rỗng và test đỏ ngay.
    def run(argv, **kwargs):
        runs.append(argv); Path(argv[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(argv, 0, "", "")
    sb = SubprocessSandbox(runner=run)
    segs = [(tmp_path / f"i{i}.png", tmp_path / f"a{i}.wav", 3.0) for i in range(3)]
    r = FFmpegAssembler(sandbox=sb).assemble(segs, tmp_path / "o.mp4", 30, "1920x1080")
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
    # lệnh đi qua Sandbox: bơm `runner=` giả vào SubprocessSandbox, KHÔNG vá `subprocess.run` — nếu assembler
    # lén gọi thẳng subprocess thì `runs` rỗng và test đỏ ngay.
    def run(argv, **kwargs):
        runs.append(argv); Path(argv[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(argv, 0, "", "")
    sb = SubprocessSandbox(runner=run)
    one = [(tmp_path / "i.png", tmp_path / "a.wav", 2.0)]
    FFmpegAssembler(sandbox=sb).assemble(one, tmp_path / "one.mp4", 30, "1920x1080")
    g1 = runs[-1][runs[-1].index("-filter_complex") + 1]
    assert "xfade" not in g1 and "concat=" not in g1 and "[v0]" in g1
    two = [*one, (tmp_path / "i2.png", tmp_path / "a2.wav", 2.0)]
    FFmpegAssembler(transition_s=0, sandbox=sb).assemble(two, tmp_path / "cut.mp4", 30, "1920x1080")
    g2 = runs[-1][runs[-1].index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in g2 and "xfade" not in g2
    # tắt chuyển động và chuẩn hoá âm lượng
    FFmpegAssembler(motion="none", loudness_lufs=None, sandbox=sb).assemble(two, tmp_path / "plain.mp4", 30, "1920x1080")
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
    """YouTube nhận thumbnail tới 50 MB từ 14/9/2026 (Data API revision history); 2 MB chỉ còn là MỤC TIÊU của
    renderer (QC warn), không phải lý do adapter từ chối."""
    from studio.media import PLATFORM_THUMBNAIL_MAX_BYTES, THUMBNAIL_MAX_BYTES
    from studio.platform import PlatformError, Tokens, TokenStore, YouTubePlatform
    assert PLATFORM_THUMBNAIL_MAX_BYTES == 50_000_000 and THUMBNAIL_MAX_BYTES == 2_000_000
    st = TokenStore(tmp_path / "t.json")
    st.save(Tokens(access_token="a", refresh_token="r", client_id="c", client_secret="s",
                   expiry="2099-01-01T00:00:00+00:00", scopes=["a"]))
    big = tmp_path / "big.jpg"; big.write_bytes(b"x" * (PLATFORM_THUMBNAIL_MAX_BYTES + 1))
    def boom(*a, **k): raise AssertionError("không được gọi API khi file đã quá nặng")
    with pytest.raises(PlatformError, match=r"50\.0 MB.*≤ 50 MB"):
        YouTubePlatform(st, fetcher=boom).set_thumbnail("vid1", big)
    ok = tmp_path / "ok.jpg"; ok.write_bytes(b"x" * (THUMBNAIL_MAX_BYTES + 1))
    sent = {}
    def fetch(method, url, headers, body):
        sent["bytes"] = len(body); return 200, {}, b"{}"
    assert json.loads(YouTubePlatform(st, fetcher=fetch).set_thumbnail("vid1", ok))["bytes"] == THUMBNAIL_MAX_BYTES + 1


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


@pytest.mark.parametrize('stage', ['render', 'repair', 'final', 'thumbnails'])
@pytest.mark.parametrize('crash_after', [False, True])
def test_prepared_output_recovers_partial_publication(tmp_path, monkeypatch, stage, crash_after):
    from studio.sqlite_bus import SQLiteBus

    path = tmp_path / 'journal.sqlite'
    bus = SQLiteBus(path)
    r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    m = _manifest()
    if stage != 'render':
        r.render(m)
    cut = CutList(video_id='V1', manifest_version=1, decision='repair', repairs=[
        Repair(scene_id='S1', action='regenerate_image', reason='fix')])
    spec = ThumbnailSpec(video_id='V1', variants=[ThumbnailVariant(variant_id='A', prompt='desk', overlay_text='Hi')])

    def invoke(renderer):
        renderer.operation_id = 'operation-1'
        if stage == 'render': return renderer.render(m.model_copy(deep=True))
        if stage == 'repair': return renderer.apply_cutlist(m, cut)
        if stage == 'final': return renderer.finalize(m)
        return renderer.thumbnails(spec)

    publish = bus.publish
    target_kind = {'render': 'draft_video', 'repair': 'draft_video', 'final': 'final_video', 'thumbnails': 'thumbnail'}[stage]

    def interrupted(env):
        if env.topic == 'media-assets' and env.payload['kind'] == target_kind:
            if crash_after: publish(env)
            raise RuntimeError('power loss')
        return publish(env)

    monkeypatch.setattr(bus, 'publish', interrupted)
    with pytest.raises(RuntimeError, match='power loss'): invoke(r)
    before = [e.event_id for e in bus.replay('media-assets')]
    bus.close()
    bus = SQLiteBus(path)
    resumed = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    def forbidden(*args, **kwargs): raise AssertionError('completed media generated twice')
    monkeypatch.setattr(resumed.media.video, 'assemble', forbidden)
    monkeypatch.setattr(resumed.media.image, 'generate', forbidden)
    invoke(resumed)
    invoke(resumed)
    outputs = list(bus.replay('media-assets'))
    assert all(sum(e.event_id == eid for e in outputs) == 1 for eid in before)
    expected = 2 if stage == 'repair' else 1
    assert sum(e.payload['kind'] == target_kind for e in outputs) == expected
    assert any(e.causation_id == 'operation-1' for e in outputs)
    if stage == 'repair':
        assert max(e.payload['version'] for e in bus.replay('scene-manifests')) == 2
    bus.close()


@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_prepared_output_refuses_damaged_artifact(tmp_path, monkeypatch, damage):
    from pathlib import Path

    bus = InMemoryBus()
    renderer = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    renderer.operation_id = 'damaged-operation'
    deliver = renderer._deliver
    def interrupted(events): raise RuntimeError('before delivery')
    monkeypatch.setattr(renderer, '_deliver', interrupted)
    with pytest.raises(RuntimeError, match='before delivery'): renderer.render(_manifest())
    journal = next(json.loads(e.payload['evidence']) for e in bus.replay('audit-log')
                   if e.payload['action'] == 'render.prepared')
    asset = next(e for e in journal['events'] if e['topic'] == 'media-assets')
    path = Path(asset['payload']['path'])
    if damage == 'missing': path.unlink()
    else: path.write_bytes(b'changed')
    monkeypatch.setattr(renderer, '_deliver', deliver)
    with pytest.raises(MediaError, match=r'prepared output.*restore'):
        renderer.render(_manifest())
    assert not list(bus.replay('media-assets'))


@pytest.mark.parametrize("operation", ["graph-image", "graph-video", "thumbnail", "finish-captions", "finish-music", "finish-both"])
def test_all_ffmpeg_stages_bound_each_decoder_and_filter(tmp_path, monkeypatch, operation):
    import subprocess

    from studio.production import finish

    monkeypatch.setattr("studio.media.shutil.which", lambda _: "/fake/ffmpeg")
    runs = []

    def encode(argv, **kwargs):
        runs.append(argv)
        Path(argv[-1]).write_bytes(b"complete encoded media")
        return subprocess.CompletedProcess(argv, 0, "", "")

    engine = FFmpegAssembler(threads=3, sandbox=SubprocessSandbox(runner=encode))
    if operation.startswith("graph"):
        visual = tmp_path / ("visual.mp4" if operation == "graph-video" else "visual.png")
        engine.assemble([(visual, tmp_path / "voice.wav", 1.0)] * 2, tmp_path / "out.mp4", 10, "160x90")
    elif operation == "thumbnail":
        engine.finish_thumbnail(tmp_path / "image.png", tmp_path / "out.jpg")
    else:
        video = tmp_path / "out.mp4"; video.write_bytes(b"previous media")
        captions = tmp_path / "captions.srt"; captions.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
        music = tmp_path / "music.wav"; music.write_bytes(b"music")
        finish(engine, video, captions if operation != "finish-music" else None,
               music if operation != "finish-captions" else None, "")
    assert len(runs) == 1
    argv = runs[0]
    previous = 0
    for index, arg in enumerate(argv):
        if arg != "-i": continue
        options = argv[previous:index]
        assert "-threads" in options, f"unbounded decoder: {argv[index + 1]}"
        assert options[options.index("-threads") + 1] == "3"
        previous = index + 2
    output_options = argv[previous:]
    assert output_options[output_options.index("-threads") + 1] == "3"
    for option in ("-filter_threads", "-filter_complex_threads"):
        assert argv[argv.index(option) + 1] == "3"


@pytest.mark.parametrize("failure_at", [1, 2, 3])
def test_thumbnail_encoder_failure_keeps_previous_file(tmp_path, monkeypatch, failure_at):
    monkeypatch.setattr("studio.media.shutil.which", lambda _: "/fake/ffmpeg")
    engine = FFmpegAssembler()
    out = tmp_path / "approved.jpg"; out.write_bytes(b"previous approved thumbnail")
    calls = []

    def interrupted(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"partial replacement" if len(calls) == failure_at else b"x" * 100)
        if len(calls) == failure_at: raise MediaError("thumbnail encoder interrupted")

    monkeypatch.setattr(engine, "_run", interrupted)
    with pytest.raises(MediaError, match="interrupted"):
        engine.finish_thumbnail(tmp_path / "source.png", out, max_bytes=16)
    assert len(calls) == failure_at
    assert out.read_bytes() == b"previous approved thumbnail"
    assert not list(tmp_path.glob(".studio-*"))


def test_thumbnail_batch_failure_preserves_published_variants_and_reuses_cache(tmp_path, monkeypatch):
    from unittest.mock import Mock

    bus = InMemoryBus()
    renderer = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    spec = ThumbnailSpec(video_id="V1", variants=[
        ThumbnailVariant(variant_id="A", prompt="old A", overlay_text="A"),
        ThumbnailVariant(variant_id="B", prompt="old B", overlay_text="B")])
    renderer.thumbnails(spec)
    prior = list(bus.replay("media-assets"))
    files = {e.payload["path"]: Path(e.payload["path"]).read_bytes() for e in prior}
    spec.variants[0].prompt = "new A"
    original = renderer.media.video.finish_thumbnail
    renderer.media.image.generate = Mock(wraps=renderer.media.image.generate)

    def fail_second(src, out, **kwargs):
        if out.name == "B.jpg":
            out.write_bytes(b"incomplete B")
            raise MediaError("second variant interrupted")
        return original(src, out, **kwargs)

    monkeypatch.setattr(renderer.media.video, "finish_thumbnail", fail_second)
    with pytest.raises(MediaError, match="second variant"):
        renderer.thumbnails(spec)
    assert all(Path(path).read_bytes() == content for path, content in files.items())
    assert list(bus.replay("media-assets")) == prior
    assert not list((tmp_path / "V1" / "thumbnails").glob(".thumbnails-*"))
    assert renderer.media.image.generate.call_count == 1
    renderer.media.image.generate.reset_mock()
    monkeypatch.setattr(renderer.media.video, "finish_thumbnail", original)
    completed = renderer.thumbnails(spec)
    assert len(completed) == 2 and all(Path(a.path).is_file() for a in completed)
    assert renderer.media.image.generate.call_count == 0


def test_word_times_from_tts_reach_the_captions_and_survive_the_cache(tmp_path):
    """Provider có mốc từng từ → renderer ghi `<cảnh>.words.json` cạnh audio → phụ đề dùng mốc thật. Cache trúng vẫn
    mang mốc (metadata cache lưu cả `words`)."""
    from unittest.mock import Mock

    from studio.media import MediaResult
    r = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path)))
    real = r.media.tts.synthesize

    def timed(text, voice, out):
        res = real(text, voice, out)
        n = len(text.split())
        return MediaResult(res.path, "fake", "fake-tts", res.duration_s,
                           words=[(0.2 * i, 0.2 * i + 0.15, w) for i, w in enumerate(text.split())] if n else None)
    r.media.tts.synthesize = Mock(side_effect=timed)
    m = _manifest(); r.render(m)
    sidecar = Path(m.scenes[0].asset_refs["scene_audio"]).parent / "S1.words.json"
    assert json.loads(sidecar.read_text())[0] == [0.0, 0.15, "Câu"]
    final = r.finalize(m)
    captions = next(e.payload for e in r.bus.replay("media-assets", "V1") if e.payload["kind"] == "captions")
    assert captions["provenance"]["generated_by"] == "studio:srt-word-times"
    assert Path(captions["path"]).read_text(encoding="utf-8").startswith("1\n00:00:00,000 --> 00:00:0")
    assert final.kind == "final_video"
    # lần dựng sau trúng cache: không gọi TTS, mốc vẫn còn
    r2 = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path)))
    r2.media.tts.synthesize = Mock(side_effect=AssertionError("cache phải trúng"))
    sidecar.unlink(); r2.render(_manifest())
    assert sidecar.exists()
    # provider không còn mốc (đổi provider) → sidecar cũ bị xoá, phụ đề quay về chia theo tỷ lệ
    r3 = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path, tts={"provider": "fake", "speed": 1})))
    r3.render(_manifest())
    assert not sidecar.exists()


def test_word_time_sidecars_that_are_missing_or_corrupt_fall_back_to_proportional_captions(tmp_path):
    r = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path)))
    m = _manifest(); r.render(m)
    audio = Path(m.scenes[0].asset_refs["scene_audio"])
    (audio.parent / "S1.words.json").write_text("{không phải json", encoding="utf-8")
    (audio.parent / "S2.words.json").write_text('[[0, 1]]', encoding="utf-8")
    assert r._word_times(m) == {}
    assert r._word_times(_manifest()) == {}  # manifest chưa dựng: không có scene_audio


def _timed_tts(r):
    from unittest.mock import Mock

    from studio.media import MediaResult
    real = r.media.tts.synthesize

    def timed(text, voice, out):
        res = real(text, voice, out)
        return MediaResult(res.path, "fake", "fake-tts", res.duration_s,
                           words=[(0.2 * i, 0.2 * i + 0.15, w) for i, w in enumerate(text.split())])
    r.media.tts.synthesize = Mock(side_effect=timed)


def test_karaoke_ass_is_burned_when_enabled_and_srt_stays_for_the_platform(tmp_path):
    """`video.karaoke: true` + `burn_captions` + mốc từng từ → đốt cứng ASS `\\k`; SRT vẫn là asset phụ đề đăng
    YouTube. Không có mốc thì đốt SRT như cũ (karaoke không có gì để tô)."""
    cfg = MediaConfig(output_dir=tmp_path, video={"burn_captions": True, "karaoke": True})
    r = Renderer(InMemoryBus(), make_media(cfg)); _timed_tts(r)
    m = _manifest(); r.render(m)
    final = r.finalize(m)
    burned = Path(json.loads(Path(final.path).read_text())["finishing"]["captions"])
    assert burned.name == "captions_v1.ass" and burned.is_file() and r"{\k" in burned.read_text(encoding="utf-8")
    kinds = [e.payload["kind"] for e in r.bus.replay("media-assets", "V1")]
    assert "captions" in kinds and not any(e.payload["path"].endswith(".ass") for e in r.bus.replay("media-assets", "V1"))
    plain = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path / "p",
                                                           video={"burn_captions": True, "karaoke": True})))
    m2 = _manifest(); plain.render(m2)
    final2 = plain.finalize(m2)
    assert json.loads(Path(final2.path).read_text())["finishing"]["captions"].endswith(".srt")
