from studio.analytics import judge_experiment, retention_drops, scene_at
from studio.events import Chapter, MetadataPackage, PerformanceSnapshot, RetentionPoint, Scene, SceneManifest
from studio.film import CinemaSpec
from studio.preflight import preflight, synthetic_media_of


def _meta(**kw):
    base = dict(video_id="V1", title="AI dựng video cho người mới: 6 giờ xuống 30 phút", description="AI dựng video " * 20,
                tags=["ai dựng video", "youtube"], primary_keyword="AI dựng video",
                chapters=[Chapter(time="00:00", label="a"), Chapter(time="00:15", label="b"), Chapter(time="00:40", label="c")])
    base.update(kw); return MetadataPackage(**base)


def test_clean_metadata_passes():
    rep = preflight(_meta())
    assert not rep.blocked, [f.text for f in rep.findings]


def test_platform_limits_block():
    rep = preflight(_meta(title="X" * 120))
    assert rep.blocked and any(f.location == "title" for f in rep.findings)
    rep = preflight(_meta(tags=["t" * 300, "u" * 300]))
    assert rep.blocked
    rep = preflight(_meta(description="cam kết lợi nhuận " * 20))
    assert rep.blocked


def test_chapters_rules():
    rep = preflight(_meta(chapters=[Chapter(time="00:05", label="a"), Chapter(time="00:15", label="b"), Chapter(time="00:40", label="c")]))
    assert rep.blocked
    rep = preflight(_meta(chapters=[Chapter(time="00:00", label="a"), Chapter(time="00:15", label="b")]))
    assert not rep.blocked and any("chapter" in f.text for f in rep.findings)
    assert not preflight(_meta(chapters=[]), format="short").findings or all(f.location != "chapters" for f in preflight(_meta(chapters=[]), format="short").findings)


def test_advisory_warns_do_not_block():
    rep = preflight(_meta(title="AI DỰNG VIDEO CHO NGƯỜI MỚI 6 GIỜ XUỐNG 30 PHÚT", description="ngắn"))
    assert not rep.blocked and len(rep.findings) >= 2
    assert rep.checklist()


def test_synthetic_media_flag_is_advisory_until_decided():
    """ADR-0018: YouTube tự gắn nhãn AI (5/2026) nếu kênh không khai `containsSyntheticMedia`. Chưa quyết → warn
    cho người duyệt; đã quyết (True/False) → không còn finding."""
    rep = preflight(_meta())
    assert MetadataPackage.model_fields["synthetic_media"].default is None
    assert [f.level for f in rep.findings if f.location == "synthetic_media"] == ["warn"] and not rep.blocked
    for decided in (True, False):
        assert all(f.location != "synthetic_media" for f in preflight(_meta(synthetic_media=decided)).findings)


def _asset(path: str, license: str = "generated", by: str = "gemini:veo") -> dict:
    return {"path": path, "kind": "scene_video", "provenance": {"generated_by": by, "license": license}}


def test_synthetic_media_of_is_decided_by_code_from_manifest_and_provenance():
    film = SceneManifest(video_id="V1", cinema=CinemaSpec(production_type="film", visual_mode="generated_video"),
                         scenes=[Scene(scene_id="S1", order=0, narration="a", visual_prompt="p", duration_s=5, asset_refs={"scene_video": "/o/S1.mp4"})])
    assert synthetic_media_of(film, [_asset("/o/S1.mp4")]) is True            # clip AI thực tế → phải khai
    assert synthetic_media_of(film, [_asset("/o/S1.mp4", "licensed", "human-upload")]) is None  # footage có bản quyền, không sinh
    anim = film.model_copy(update={"cinema": CinemaSpec(production_type="animation", visual_mode="generated_video")})
    assert synthetic_media_of(anim, [_asset("/o/S1.mp4")]) is None           # hoạt hình: YouTube không đòi nhãn nổi bật → người quyết
    expl = SceneManifest(video_id="V1", scenes=[Scene(scene_id="S1", order=0, narration="a", visual_prompt="p", duration_s=5, asset_refs={"scene_image": "/o/S1.png"})])
    assert synthetic_media_of(expl, [_asset("/o/S1.png", by="openai:gpt-image-1")]) is None  # minh hoạ explainer: code không đoán
    cine_img = SceneManifest(video_id="V1", cinema=CinemaSpec(production_type="film"),
                             scenes=[Scene(scene_id="S1", order=0, narration="a", visual_prompt="p", duration_s=5, asset_refs={"scene_image": "/o/S1.png"})])
    assert synthetic_media_of(cine_img, [_asset("/o/S1.png", by="openai:gpt-image-1")]) is True  # phim bằng ảnh AI điện ảnh
    assert synthetic_media_of(film, [_asset("/o/S1.mp4")], policy=False) is False  # chính sách kênh khai tường minh thắng
    assert synthetic_media_of(expl, [], policy=True) is True


def _manifest():
    return SceneManifest(video_id="V1", scenes=[Scene(scene_id="S1", order=0, narration="a", visual_prompt="p", duration_s=5),
                                                 Scene(scene_id="S2", order=1, narration="b", visual_prompt="p", duration_s=5),
                                                 Scene(scene_id="S3", order=2, narration="c", visual_prompt="p", duration_s=5)])


def test_retention_drops_map_to_scenes():
    m = _manifest()
    assert scene_at(m, 0) == "S1" and scene_at(m, 6) == "S2" and scene_at(m, 14.9) == "S3" and scene_at(m, 99) == "S3"
    curve = [RetentionPoint(t=0, pct=100), RetentionPoint(t=3, pct=97), RetentionPoint(t=6, pct=85), RetentionPoint(t=12, pct=83)]
    drops = retention_drops(curve, m)
    assert [(d.scene_id, d.t, d.drop_pct) for d in drops] == [("S2", 6, 12.0)]
    assert retention_drops(curve, None)[0].scene_id is None


def test_experiment_needs_confidence_and_retention_guard():
    a = PerformanceSnapshot(video_id="V1", channel_id="C", impressions=5000, ctr=0.05, avg_view_duration_s=7, variant_id="A")
    b = PerformanceSnapshot(video_id="V1", channel_id="C", impressions=5000, ctr=0.07, avg_view_duration_s=7.5, variant_id="B")
    e = judge_experiment("E1", "thumbnail", a, b)
    assert e.winner == "B" and e.confidence >= 0.95 and e.retention_guard_ok
    # CTR thắng nhưng khán giả bỏ sớm → không thắng (clickbait guard)
    c = b.model_copy(update={"avg_view_duration_s": 4.0})
    assert judge_experiment("E2", "thumbnail", a, c).winner is None
    # mẫu nhỏ → không kết luận
    small_a, small_b = a.model_copy(update={"impressions": 100}), b.model_copy(update={"impressions": 100})
    assert judge_experiment("E3", "thumbnail", small_a, small_b).winner is None
