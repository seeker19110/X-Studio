"""Graphic theo cảnh (ADR-0017): production-manager CHỌN cảnh nào là đồ họa dữ liệu (title/quote/bars/list/stat) dưới
dạng DỮ LIỆU có schema; code kiểm số liệu với claim đã có nguồn, xếp chữ vừa khung chừa vùng phụ đề, rồi FFmpeg dựng.
Học từ HyperFrames/Remotion/ManimGX (kho `stored` mục 22): video giải thích phải có chuyển động mang thông tin, không
phải chuỗi ảnh Ken Burns — nhưng model không viết code chạy được, nó chỉ điền template."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from studio import motion
from studio.bus import BusError, InMemoryBus
from studio.events import SceneManifest
from studio.media import MediaConfig, MediaError, make_media, probe_duration
from studio.motion import MotionSpec, numbers_in, plan, safe_bottom, verify_graphics
from studio.renderer import Renderer

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
STAT = {"template": "stat", "title": "Người mới bỏ cuộc", "values": [42], "unit": "%", "source": "example.org/report"}
SCRIPT = {"video_id": "V", "working_title": "t", "hook": "h", "sections": [],
          "claims": [{"claim_id": "C1", "text": "42% người mới bỏ cuộc sau 3 video", "source": "https://example.org/report"},
                     {"claim_id": "C2", "text": "Doanh thu tăng 1.234,5 triệu", "source": None}]}


def _manifest(graphic: dict | None = None, vid: str = "V") -> dict:
    return {"video_id": vid, "scenes": [
        {"scene_id": "S1", "order": 0, "narration": "Bốn mươi hai phần trăm người mới bỏ cuộc.", "visual_prompt": "bàn",
         "duration_s": 2.0, "graphic": graphic},
        {"scene_id": "S2", "order": 1, "narration": "Đừng là một trong số đó.", "visual_prompt": "người cười", "duration_s": 2.0}]}


def test_scene_graphic_is_validated_data_at_the_bus_so_bad_model_output_is_refused():
    bus = InMemoryBus()
    bus.validate("scene-manifests", _manifest(STAT))
    bus.validate("scene-manifests", _manifest(None))
    assert SceneManifest.model_validate(_manifest(STAT)).scenes[0].graphic == MotionSpec.model_validate(STAT)
    for bad in ({**STAT, "template": "html"}, {**STAT, "code": "<script>"}, {"template": "stat", "title": "x", "values": [1, 2],
                                                                              "source": "s"}):
        with pytest.raises(BusError):
            bus.validate("scene-manifests", _manifest(bad))


def test_new_templates_have_their_own_rules():
    MotionSpec(template="list", title="Ba bước", labels=["Viết", "Dựng", "Đăng"])
    with pytest.raises(ValidationError): MotionSpec(template="list", title="Một ý", labels=["Chỉ một"])
    with pytest.raises(ValidationError): MotionSpec(template="list", title="Dài", labels=["x" * 81, "y"])
    with pytest.raises(ValidationError): MotionSpec(template="stat", title="Không nguồn", values=[42])
    with pytest.raises(ValidationError): MotionSpec(template="stat", title="Âm", values=[-1], source="s")
    with pytest.raises(ValidationError): MotionSpec(template="bars", title="Nhãn dài", labels=["x" * 41], values=[1], source="s")
    with pytest.raises(ValidationError): MotionSpec(template="bars", title="Lệch", labels=["A"], values=[1, 2], source="s")


def test_numbers_are_read_the_way_people_write_them():
    assert {42.0} <= numbers_in("42% người mới")
    assert 1234.5 in numbers_in("1.234,5 triệu") and 1234.5 in numbers_in("1,234.5 million")
    assert {1.5, 15.0} <= numbers_in("1,5 lần") and 30.0 in numbers_in("còn 30 phút")
    assert numbers_in("không có số") == set()


def test_chart_numbers_must_come_from_a_sourced_claim_of_the_fact_checked_script():
    """Bất biến 'số liệu thật': model chỉ được vẽ con số có trong claim CÓ NGUỒN mà fact-checker đã cho qua. Số lạ
    → bỏ graphic (cảnh quay về ảnh), không chặn cả video; title/quote/list không có số thì không bị đụng."""
    p = _manifest(STAT)
    p["scenes"][1]["graphic"] = {"template": "bars", "title": "Doanh thu", "labels": ["A", "B"], "values": [1234.5, 7],
                                 "source": "báo cáo"}
    out, dropped = verify_graphics(p, SCRIPT)
    assert out["scenes"][0]["graphic"] == STAT
    assert out["scenes"][1]["graphic"] is None
    assert dropped == [{"scene_id": "S2", "template": "bars", "unverified": [1234.5, 7.0]}], \
        "1.234,5 chỉ nằm trong claim KHÔNG nguồn — không tính"
    assert p["scenes"][1]["graphic"] is not None, "không sửa payload đầu vào tại chỗ"
    out, dropped = verify_graphics(_manifest(STAT), None)
    assert out["scenes"][0]["graphic"] is None and dropped[0]["unverified"] == [42.0]
    title = {"template": "title", "title": "Chương 2", "subtitle": "Vì sao bỏ cuộc"}
    assert verify_graphics(_manifest(title), None) == (_manifest(title), [])


@pytest.mark.parametrize("template", ["title", "quote", "bars", "list", "stat"])
@pytest.mark.parametrize("resolution", ["1280x720", "1080x1920", "320x180"])
@pytest.mark.parametrize("captions", [False, True])
def test_every_template_fits_inside_the_safe_area(template, resolution, captions):
    """Chữ dài nhất schema cho phép vẫn phải nằm trong khung và trên vùng phụ đề/giao diện Shorts — layout là code đo,
    không để model đoán cỡ chữ."""
    w, h = map(int, resolution.split("x"))
    long = "Người xem rời đi rất sớm khi phần mở đầu không hứa hẹn điều gì cụ thể cho chính họ " * 2
    spec = MotionSpec(template=template, title=long[:160], subtitle=long[:240],
                      labels=[long[:40 if template == "bars" else 80]] * (6 if template != "stat" else 1), values=[123456] * (6 if template != "stat" else 1),
                      unit="%", source=long[:200])
    bottom = safe_bottom(w, h, captions)
    texts, boxes = plan(spec, w, h, 6.0, bottom)
    assert texts
    for t in texts:
        assert t.size >= motion.MIN_TEXT * min(w, h) - 1e-6
        assert t.x >= 0 and t.x + t.width <= w + 0.5, f"{t.name} tràn ngang"
        assert t.y >= 0 and t.y + t.height <= bottom + 0.5, f"{t.name} lấn vùng phụ đề"
        assert all(len(line) <= t.chars for line in t.text.split("\n"))
    for b in boxes:
        assert b.y + b.h <= bottom + 0.5 and b.x + b.w <= w + 0.5


def test_safe_area_reserves_captions_and_the_shorts_interface():
    assert safe_bottom(1280, 720, False) > safe_bottom(1280, 720, True)
    assert safe_bottom(1080, 1920, False) == safe_bottom(1080, 1920, True) <= 1920 * 0.8


def test_text_that_cannot_fit_is_refused_instead_of_cut():
    spec = MotionSpec(template="list", title="x" * 160, labels=["y" * 80] * 6)
    with pytest.raises(MediaError, match="không vừa khung"):
        plan(spec, 1280, 720, 6.0, 100)  # vùng an toàn chỉ còn ~40 px dưới mép trên
    rows = MotionSpec(template="bars", title="t", labels=["a"] * 6, values=[1] * 6, source="s")
    with pytest.raises(MediaError, match="6 cột không vừa khung"):
        plan(rows, 1280, 720, 6.0, 200)  # tiêu đề và nguồn vừa, sáu hàng thì không
    with pytest.raises(MediaError, match="con số không vừa khung"):
        plan(MotionSpec(template="stat", title="t", values=[1e300], source="s"), 1280, 720, 6.0, 600)


def test_list_reveals_items_in_order_and_stat_counts_up_only_whole_numbers():
    texts, _ = plan(MotionSpec(template="list", title="Ba bước", labels=["Viết", "Dựng", "Đăng"]), 1280, 720, 6.0, 600)
    starts = [t.start for t in texts if t.name.startswith("item")]
    assert len(starts) == 3 and starts == sorted(starts) and starts[0] > 0 and starts[-1] < 6.0 * 0.6
    whole, _ = plan(MotionSpec.model_validate(STAT), 1280, 720, 6.0, 600)
    number = next(t for t in whole if t.name == "number")
    assert number.count_to == 42 and number.text == "42"
    unit = next(t for t in whole if t.name == "unit")
    assert unit.text == "%" and unit.x > number.x
    frac, _ = plan(MotionSpec(template="stat", title="Lần", values=[1.5], source="s"), 1280, 720, 6.0, 600)
    assert next(t for t in frac if t.name == "number").count_to is None, "số lẻ không đếm, hiện thẳng"
    assert [t.text for t in frac if t.name == "number"] == ["1.5"]
    big, _ = plan(MotionSpec(template="stat", title="Lượt xem", values=[123456789.5], source="s"), 1280, 720, 6.0, 600)
    assert [t.text for t in big if t.name == "number"] == ["123456789.5"], "không làm tròn kiểu %g (1.23457e+08)"


@pytest.mark.skipif(not HAS_FFMPEG, reason="cần ffmpeg + ffprobe")
@pytest.mark.parametrize("template", ["list", "stat"])
def test_new_templates_render_real_video(tmp_path, template):
    from studio.media import FFmpegAssembler
    spec = MotionSpec(template=template, title="ĐỒNG HÀNH", labels=["Hôm qua", "Hôm nay"] if template == "list" else [],
                      values=[42] if template == "stat" else [], unit="%", source="nguồn: báo cáo")
    out = tmp_path / "m.mp4"
    motion.render_motion(spec, out, FFmpegAssembler(threads=1), 1.2, 10, "320x180", bottom=140)
    assert out.stat().st_size > 1000 and abs((probe_duration(out) or 0) - 1.2) < .15


def _renderer(tmp_path, **video) -> Renderer:
    cfg = MediaConfig(output_dir=tmp_path, video=video or {}, render={"mode": "bounded", "threads": 1})
    return Renderer(InMemoryBus(), make_media(cfg), tmp_path)


@pytest.mark.skipif(not HAS_FFMPEG, reason="cần ffmpeg + ffprobe")
def test_renderer_draws_the_scene_graphic_chosen_by_the_model(tmp_path):
    r = _renderer(tmp_path, provider="ffmpeg", resolution="160x90", fps=10)
    m = SceneManifest.model_validate(_manifest(STAT))
    assets = r.render(m)
    clip = next(a for a in assets if a.kind == "scene_video" and a.scene_id == "S1")
    assert clip.provenance.prompt_ref == "motion:S1" and Path(clip.path).is_file()
    assert "scene_image" in m.scenes[1].asset_refs and "scene_video" not in m.scenes[1].asset_refs


def test_without_ffmpeg_a_model_graphic_falls_back_to_the_image_but_an_operator_graphic_still_fails(tmp_path):
    r = _renderer(tmp_path)
    m = SceneManifest.model_validate(_manifest(STAT))
    r.render(m)
    assert "scene_image" in m.scenes[0].asset_refs
    skipped = [json.loads(e.payload["evidence"]) for e in r.bus.replay("audit-log")
               if e.payload["action"] == "render.graphic_skipped"]
    assert skipped == [{"scene_id": "S1", "template": "stat", "reason": "video.provider không phải ffmpeg"}]
    r.media.cfg.production["graphics"] = {"V": {"S2": {"template": "title", "title": "CHÀO"}}}
    with pytest.raises(MediaError, match="ffmpeg"):
        r.render(SceneManifest.model_validate(_manifest(None)))


def test_operator_graphic_wins_and_cache_key_tracks_layout_and_safe_area(tmp_path, monkeypatch):
    r = _renderer(tmp_path, provider="ffmpeg", resolution="160x90", fps=10)
    seen: list[tuple[str, float]] = []

    def fake_render(spec, out, video, duration, fps, resolution, bottom=None):
        seen.append((spec.template, bottom)); out.write_bytes(b"mp4")
        from studio.media import MediaResult
        return MediaResult(out, "studio", f"motion-{spec.template}", duration)

    monkeypatch.setattr("studio.renderer.render_motion", fake_render)
    r.media.cfg.production["graphics"] = {"V": {"S1": {"template": "title", "title": "CHÀO"}}}
    m = SceneManifest.model_validate(_manifest(STAT))
    r.render_scene(m, m.scenes[0], audio=False)
    assert seen == [("title", safe_bottom(160, 90, False))]
    r.media.cfg.video["burn_captions"] = True
    r.render_scene(m, m.scenes[0], audio=False)
    assert seen[-1] == ("title", safe_bottom(160, 90, True)), "đổi vùng an toàn phải dựng lại, không trúng cache cũ"


def test_unverified_graphics_are_dropped_before_the_manifest_is_published(tmp_path):
    from test_orchestrator import _to_publish_gate
    bus, _ = _to_publish_gate(tmp_path, scene_graphics={
        "S1": STAT, "S2": {"template": "stat", "title": "Bịa", "values": [99], "source": "đâu đó"}})
    first = next(e for e in bus.replay("scene-manifests", "CH1-V1") if e.actor == "production-manager").payload
    assert first["scenes"][0]["graphic"]["values"] == [42] and first["scenes"][1]["graphic"] is None
    dropped = [json.loads(e.payload["evidence"]) for e in bus.replay("audit-log")
               if e.payload["action"] == "graphic.unverified"]
    assert dropped == [{"scene_id": "S2", "template": "stat", "unverified": [99.0]}]


def test_an_operator_library_pick_beats_the_model_graphic(tmp_path):
    """Người vận hành chọn footage/ảnh có license cho cảnh (`production.scenes`) là quyết định tường minh của người:
    graphic model tự chọn không được đè lên nó, kể cả khi có ffmpeg."""
    import yaml
    uploads = tmp_path / "uploads"; uploads.mkdir()
    (uploads / "clip.mp4").write_bytes(b"local-footage")
    (uploads / "library.yaml").write_text(yaml.safe_dump({"assets": {
        "sea": {"kind": "video", "path": "clip.mp4", "license": "owned", "attribution": "Studio crew"}}}), encoding="utf-8")
    r = _renderer(tmp_path, provider="ffmpeg", resolution="160x90", fps=10)
    r.media.cfg.production.update({"library": "library.yaml", "scenes": {"V": {"S1": "sea"}}})
    m = SceneManifest.model_validate(_manifest(STAT))
    assets = r.render_scene(m, m.scenes[0], audio=False)
    assert [a.provenance.license for a in assets] == ["owned"]
    skipped = [json.loads(e.payload["evidence"]) for e in r.bus.replay("audit-log")
               if e.payload["action"] == "render.graphic_skipped"]
    assert skipped == [{"scene_id": "S1", "template": "stat", "reason": "người vận hành đã chọn asset thư viện"}]
