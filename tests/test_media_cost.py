"""Sổ chi phí media (ADR-0016): giá niêm yết khai trong media.yaml × đơn vị CODE đếm được, chỉ cho lượt gọi provider
thật (cache trượt), trần ngân sách chặn TRƯỚC lượt gọi sẽ vượt — học từ cost tracker của OpenMontage."""
from __future__ import annotations

import json
import math

import pytest

from studio import media_cost
from studio.bus import InMemoryBus
from studio.events import Envelope, Scene, SceneManifest, ThumbnailSpec, ThumbnailVariant, VideoBrief
from studio.fakes import make_scripted_client
from studio.media import MediaConfig, MediaError, MediaResult, make_media
from studio.media_cost import MediaBudgetError
from studio.orchestrator import Orchestrator, StepResult
from studio.renderer import Renderer

PRICES = {"tts": {"price_per_1k_chars": 0.015}, "image": {"price_per_image": 0.04},
          "scene_video": {"price_per_second": 0.1}}


def test_console_production_prices_are_visible_to_the_provider_ledger(tmp_path):
    cfg=MediaConfig(output_dir=tmp_path,production={'pricing':{
        'tts_per_1000_chars':.02,'image_per_request':.03,'video_per_second':.04}})
    cfg.tts['provider']='command';cfg.image['provider']='command';cfg.scene_video['provider']='comfyui'
    assert media_cost.priced(cfg)
    assert media_cost.quote(cfg,'tts',1000)==pytest.approx(.02)
    assert media_cost.quote(cfg,'image',2)==pytest.approx(.06)
    assert media_cost.quote(cfg,'scene_video',3)==pytest.approx(.12)


def _paid(tmp_path, budget: float | None = None, **video) -> Renderer:
    """Renderer với adapter giả nhưng cấu hình như provider trả phí: sổ chỉ bỏ qua provider tên `fake`."""
    cfg = MediaConfig(output_dir=tmp_path, production={"media_budget_usd": budget} if budget is not None else {})
    r = Renderer(InMemoryBus(), make_media(cfg), tmp_path)
    for section, rate in PRICES.items():
        getattr(r.media.cfg, section).update(rate)
    r.media.cfg.tts["provider"] = "openai"; r.media.cfg.image["provider"] = "openai"
    return r


def _manifest(vid: str = "V", n: int = 2) -> SceneManifest:
    return SceneManifest(video_id=vid, scenes=[
        Scene(scene_id=f"S{i + 1}", order=i, narration=str(i) + "x" * 499, visual_prompt=f"cảnh {i + 1}", duration_s=2.0)
        for i in range(n)])


def _costs(bus, vid="V"):
    return [json.loads(e.payload["evidence"]) for e in bus.replay("audit-log")
            if e.payload["action"] == "media.cost" and e.payload["video_id"] == vid]


def test_rates_are_validated_and_unpriced_config_has_no_ledger():
    cfg = MediaConfig()
    assert media_cost.priced(cfg) is False and media_cost.budget(cfg) == 0.0
    assert media_cost.quote(cfg, "tts", 1000) == 0.0
    cfg.tts.update(PRICES["tts"]); cfg.tts["provider"] = "openai"
    assert media_cost.priced(cfg) and media_cost.quote(cfg, "tts", 2000) == pytest.approx(0.03)
    cfg.tts["provider"] = "fake"
    assert media_cost.quote(cfg, "tts", 2000) == 0.0, "provider fake không tiêu tiền thật"
    for bad in (-1, math.nan, "rẻ", True):
        cfg.image["price_per_image"] = bad
        with pytest.raises(MediaError, match="price_per_image"): media_cost.rate(cfg, "image")
    cfg.production["media_budget_usd"] = -5
    with pytest.raises(MediaError, match="media_budget_usd"): media_cost.budget(cfg)


def test_only_real_provider_calls_are_charged_and_cache_hits_are_free(tmp_path):
    r = _paid(tmp_path)
    m = _manifest(); r.render(m)
    rows = _costs(r.bus)
    assert sorted((c["channel"], c["scene_id"]) for c in rows) == [("image", "S1"), ("image", "S2"),
                                                                     ("tts", "S1"), ("tts", "S2")]
    tts = next(c for c in rows if c["channel"] == "tts")
    assert tts["units"] == 500 and tts["usd"] == pytest.approx(0.0075) and tts["provider"] == "openai"
    total = media_cost.spent(r.bus, "V")
    assert total == pytest.approx(2 * 0.0075 + 2 * 0.04)
    r.render(_manifest())  # cùng nội dung → cache trúng → không gọi provider, không tốn thêm
    assert media_cost.spent(r.bus, "V") == pytest.approx(total) and len(_costs(r.bus)) == 4


def test_budget_refuses_the_call_that_would_overspend_before_the_provider_runs(tmp_path, monkeypatch):
    r = _paid(tmp_path, budget=0.05)
    calls = []
    original = r.media.image.generate
    monkeypatch.setattr(r.media.image, "generate", lambda *a: calls.append(a) or original(*a))
    m = _manifest()
    with pytest.raises(MediaBudgetError, match="media_budget_usd") as err:
        r.render(m)
    assert isinstance(err.value, MediaError)
    # S1: tts 0.0075 + ảnh 0.04 = 0.0475 ≤ 0.05; S2 tts → 0.055 > 0.05 bị từ chối trước khi gọi provider
    assert len(calls) == 1 and media_cost.spent(r.bus, "V") == pytest.approx(0.0475)
    # trần nằm trong cấu hình: nâng trần thì đi tiếp, phần đã tạo được dùng lại từ cache
    r.media.cfg.production["media_budget_usd"] = 1.0
    r.render(m)
    assert len(calls) == 2 and media_cost.spent(r.bus, "V") == pytest.approx(0.095)


def test_ledger_survives_restart_and_counts_scene_video_and_thumbnails(tmp_path):
    from studio.film import CinemaSpec
    r = _paid(tmp_path)
    r.media.cfg.scene_video.update({"provider": "comfyui", "duration_s": 5, "license": "unknown"})
    r.media.scene_video = make_media(MediaConfig(scene_video={"provider": "fake", "duration_s": 5})).scene_video
    m = SceneManifest(video_id="F", cinema=CinemaSpec(visual_mode="generated_video"), scenes=[
        Scene(scene_id="S1", order=0, narration="abc", visual_prompt="rừng", duration_s=2.0)])
    r.render_scene(m, m.scenes[0])
    clip = next(c for c in _costs(r.bus, "F") if c["channel"] == "scene_video")
    assert clip["units"] == pytest.approx(clip["usd"] / 0.1) and clip["usd"] > 0
    r.thumbnails(ThumbnailSpec(video_id="F", variants=[ThumbnailVariant(variant_id="A", prompt="nền", overlay_text="")]))
    thumb = next(c for c in _costs(r.bus, "F") if c.get("variant_id") == "A")
    assert thumb["channel"] == "image" and thumb["usd"] == pytest.approx(0.04)
    before = media_cost.spent(r.bus, "F")
    again = Renderer(r.bus, r.media, tmp_path)  # tiến trình mới đọc sổ từ audit-log bền
    assert again.ledger.total("F") == pytest.approx(before)
    r.bus.publish(Envelope(topic="audit-log", key="renderer", actor="renderer", payload={
        "actor": "renderer", "action": "media.cost", "video_id": "F", "evidence": "{hỏng"}))
    assert media_cost.spent(r.bus, "F") == pytest.approx(before), "dòng sổ hỏng không làm sập tổng"


def test_budget_overrun_pauses_the_video_at_once_instead_of_retrying(tmp_path):
    o = Orchestrator(InMemoryBus(), make_scripted_client(), media=make_media(MediaConfig(output_dir=tmp_path)))
    brief = VideoBrief(video_id="V", channel_id="CH", working_title="x", audience="all", angle="x", pillar="education",
                       target_minutes=1, key_points=["x"], success_metric="retention", budget_tokens=1000)
    o.bus.publish(Envelope(topic="video-briefs", key="V", actor="desk", payload=brief.model_dump()))
    env = Envelope(topic="scene-manifests", key="V", actor="production-manager", payload={"video_id": "V"})
    res = StepResult(env.event_id, env.topic, env.key)
    o._media_failure(env, res, MediaBudgetError("ngân sách media 1 USD của V: hết"))
    assert not res.transient, "thử lại không làm rẻ đi: không được hẹn retry"
    pause = list(o.bus.replay("supervisor-actions", "V"))[-1].payload
    assert pause["action"] == "budget_cut" and pause["reason"].startswith("media budget: ngân sách media")
    assert "V" in o.paused and env.event_id in o.deferred


def test_publish_checklist_and_console_show_the_estimated_media_spend(tmp_path, monkeypatch):
    from test_orchestrator import _to_publish_gate
    bus, o = _to_publish_gate(tmp_path)
    assert not any(c.startswith("media_cost:") for c in o._publish_checklist("CH1-V1")), "chưa khai giá: không bịa 0 USD"
    o.renderer.media.cfg.image.update(provider="openai", price_per_image=0.04)
    o.renderer.media.cfg.production["media_budget_usd"] = 2
    bus.publish(Envelope(topic="audit-log", key="renderer", actor="renderer", payload={
        "actor": "renderer", "action": "media.cost", "video_id": "CH1-V1",
        "evidence": json.dumps({"channel": "image", "usd": 0.12, "units": 3})}))
    assert "media_cost:0.1200 USD ước tính / trần 2 USD" in o._publish_checklist("CH1-V1")
    o.renderer.media.cfg.production["media_budget_usd"] = 0.15   # 0.12 / 0.15 = 80 % → người duyệt thấy cảnh báo
    assert "media_cost:0.1200 USD ước tính / trần 0.15 USD — đã dùng ≥ 80 % trần" in o._publish_checklist("CH1-V1")

    from studio.factory import Factory
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    cfg = MediaConfig(output_dir=tmp_path / "f", image={"provider": "openai", "price_per_image": 0.04},
                      production={"media_budget_usd": 5})
    factory = Factory(tmp_path / "f.sqlite", cfg, demo=True)
    try:
        factory.command("pause", {}); factory.cycle()
        brief = VideoBrief(video_id="V1", channel_id="CH1", working_title="x", audience="a", angle="x", pillar="p",
                           key_points=["x"], estimate_tokens=10, budget_tokens=15)
        factory.orch.desk.dispatch([brief.model_dump()])
        factory.bus.publish(Envelope(topic="audit-log", key="renderer", actor="renderer", payload={
            "actor": "renderer", "action": "media.cost", "video_id": "V1",
            "evidence": json.dumps({"channel": "image", "usd": 0.5, "units": 1})}))
        factory.refresh()
        project = factory.snapshot()["projects"][0]
        assert project["media_cost"] == {"usd": 0.5, "budget_usd": 5.0, "priced": True, "warning": False}
        factory.bus.publish(Envelope(topic="audit-log", key="renderer", actor="renderer", payload={
            "actor": "renderer", "action": "media.cost", "video_id": "V1",
            "evidence": json.dumps({"channel": "image", "usd": 3.5, "units": 1})}))
        factory.refresh()
        assert factory.snapshot()["projects"][0]["media_cost"]["warning"] is True
    finally:
        factory.close()


def test_declaring_prices_never_invalidates_paid_cache_entries(tmp_path):
    """Khoá cache băm cấu hình provider: nếu giá nằm trong khoá, thêm `price_*` vào media.yaml làm mọi asset đã trả
    tiền bị sinh lại — chính bảng giá lại gây tốn tiền. Giá chỉ là sổ sách, không phải đầu vào của provider."""
    r = Renderer(InMemoryBus(), make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    m = _manifest(); r.render(m)
    for section, rate in PRICES.items():
        getattr(r.media.cfg, section).update(rate)
    r.render(_manifest())
    hits = [json.loads(e.payload["evidence"])["hits"] for e in r.bus.replay("audit-log")
            if e.payload["action"] == "render.cache"][-2:]
    assert hits == [{"audio": True, "image": True}] * 2


def test_comfyui_cache_identity_ignores_the_price(tmp_path):
    from studio.video_gen import ComfyUIVideo
    from test_video_gen import workflow
    plain = ComfyUIVideo(workflow(tmp_path)).cache_identity
    priced = workflow(tmp_path); priced.scene_video["price_per_second"] = 0.2
    assert ComfyUIVideo(priced).cache_identity == plain


def test_ledger_warns_once_when_a_video_passes_80_percent_of_its_budget(tmp_path):
    """Cảnh báo 80 % (rà 2026-10-14, học từ cost tracker): một dòng `media.cost_warning` đúng lúc vượt ngưỡng, không
    lặp mỗi lượt, không chặn — trần vẫn là việc của MediaBudgetError."""
    r = _paid(tmp_path, budget=0.1)
    r.render(_manifest())   # S1: 0.0475 (< 0.08); S2 tts: 0.055; S2 ảnh: 0.095 (≥ 0.08) → cảnh báo tại lượt ảnh S2
    warnings = [e.payload for e in r.bus.replay("audit-log") if e.payload["action"] == "media.cost_warning"]
    assert len(warnings) == 1 and warnings[0]["video_id"] == "V"
    data = json.loads(warnings[0]["evidence"])
    assert data["spent_usd"] == pytest.approx(0.095) and data["cap_usd"] == 0.1 and data["pct"] == 95
    assert data["channel"] == "image" and data["scene_id"] == "S2"
    # một lượt trả tiền nữa còn trong trần (0.0965 ≤ 0.1) nhưng đã qua ngưỡng: không cảnh báo lại
    r.ledger.charge("V", "tts", 100, lambda: MediaResult(tmp_path / "x.mp3", "openai", "m", 1.0), scene_id="S9")
    assert len([e for e in r.bus.replay("audit-log") if e.payload["action"] == "media.cost_warning"]) == 1
    # không trần → không có ngưỡng
    r2 = _paid(tmp_path / "b"); r2.render(_manifest())
    assert not [e for e in r2.bus.replay("audit-log") if e.payload["action"] == "media.cost_warning"]


def test_tts_can_be_priced_per_second_of_audio(tmp_path):
    """Gemini TTS tính theo token audio (25 token/giây) → `tts.price_per_second`: trần kiểm bằng ước lượng
    (ký tự / 15 mỗi giây) TRƯỚC lượt gọi, sổ ghi theo thời lượng audio THẬT sau lượt gọi; cộng với giá theo ký tự nếu có."""
    cfg = MediaConfig(output_dir=tmp_path, tts={"provider": "gemini", "price_per_second": 0.01})
    assert media_cost.priced(cfg) and media_cost.quote(cfg, "tts", 300) == pytest.approx(0.2)   # 300 ký tự ≈ 20 s
    assert media_cost.quote(cfg, "tts", 300, seconds=12.5) == pytest.approx(0.125)
    cfg.tts["price_per_1k_chars"] = 0.02
    assert media_cost.quote(cfg, "tts", 300, seconds=10) == pytest.approx(0.006 + 0.1)
    assert "price_per_second" not in media_cost.without_prices(cfg.tts)
    rows: list = []
    ledger = media_cost.Ledger(InMemoryBus(), cfg, lambda action, vid, data: rows.append((action, data)))
    ledger.charge("V", "tts", 300, lambda: MediaResult(tmp_path / "a.wav", "gemini", "m", 10.0), scene_id="S1")
    assert rows[0][1]["usd"] == pytest.approx(0.106) and rows[0][1]["seconds"] == 10.0
    cfg.production["media_budget_usd"] = 0.15
    with pytest.raises(MediaBudgetError):   # ước lượng 0.006 + 0.2 vượt phần còn lại trước khi gọi
        ledger.charge("V", "tts", 300, lambda: (_ for _ in ()).throw(AssertionError("không được gọi provider")))
    with pytest.raises(MediaError, match=r"tts\.price_per_second"):
        media_cost.quote(MediaConfig(tts={"provider": "gemini", "price_per_second": -1}), "tts", 10)
