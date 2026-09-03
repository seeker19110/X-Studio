"""Nhánh còn thiếu của orchestrator.py: CLI main(), watch(), lỗi render/apply_cut, plan thất bại (LLMError)."""

from __future__ import annotations

import json

import pytest

from studio.bus import InMemoryBus
from studio.events import Envelope
from studio.fakes import _inputs, make_scripted_client, scripted
from studio.llm import FakeClient, LLMError
from studio.media import MediaConfig, MediaError, make_media
from studio.orchestrator import Orchestrator
from studio.runner import RunnerError
from studio.sqlite_bus import SQLiteBus

CHANNEL = {"channel_id": "CH1", "goals": ["1000 sub"], "audience": "người mới", "pillars": ["hướng dẫn"], "cadence": "2/tuần",
           "boundaries": ["không hứa thu nhập"]}


def _orch(bus, tmp_path, **opts):
    return Orchestrator(bus, make_scripted_client(**opts), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)


def _audit_actions(bus, actor=None):
    return [e.payload["action"] for e in bus.replay("audit-log") if actor is None or e.actor == actor]


def _to_scene_manifest(tmp_path):
    """Chạy tới khi có scene-manifests đầu tiên (trước khi renderer publish v1 có asset), để test lỗi render."""
    bus = InMemoryBus(); o = _orch(bus, tmp_path, plan_size=1, repairs=0)
    bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human", payload=CHANNEL))
    o.run()
    o.gate.decide("PLAN-CH1-1", "approve", by="human:owner")
    return bus, o


# ---------- watch() ----------

def test_watch_calls_tick_max_ticks_times(monkeypatch, tmp_path):
    bus = InMemoryBus(); o = _orch(bus, tmp_path)
    calls = {"tick": 0, "sleep": []}
    monkeypatch.setattr(o, "tick", lambda now=None: calls.__setitem__("tick", calls["tick"] + 1) or [])
    monkeypatch.setattr("studio.orchestrator.time.sleep", lambda s: calls["sleep"].append(s))
    o.watch(interval=2.5, max_ticks=3)
    assert calls["tick"] == 3
    assert calls["sleep"] == [2.5, 2.5, 2.5]


# ---------- lỗi render / apply_cut ----------

def test_render_error_is_audited_and_action_marked_failed(tmp_path, monkeypatch):
    bus, o = _to_scene_manifest(tmp_path)

    def boom(m):
        raise MediaError("ffmpeg không có trên PATH")

    monkeypatch.setattr(o.renderer, "render", boom)
    o.run()
    assert "render_failed" in _audit_actions(bus, "orchestrator")


def test_apply_cut_render_error_is_audited(tmp_path, monkeypatch):
    bus, o = _to_scene_manifest(tmp_path)
    o.run()  # renderer render v1 thật, có asset
    manifest_env = next(e for e in reversed(list(bus.replay("scene-manifests", "CH1-V1"))))
    from studio.events import CutList
    cut = CutList(video_id="CH1-V1", manifest_version=manifest_env.payload["version"], decision="approve", order=[])

    def boom(m, order=None):
        raise MediaError("ghép video lỗi")

    monkeypatch.setattr(o.renderer, "finalize", boom)
    bus.publish(Envelope(topic="cut-lists", key="CH1-V1", actor="editor", payload=cut.model_dump()))
    o.run()
    assert "render_failed" in _audit_actions(bus, "orchestrator")


# ---------- plan thất bại (LLMError) ----------

def test_plan_llm_error_is_audited_as_agent_failed(tmp_path):
    bus = InMemoryBus()
    # 1 câu trả lời cho trend-researcher (channel-briefs → trend-reports); rồi hết câu trả lời khi tới channel-strategist (_plan)
    trend_report = {"channel_id": "CH1", "trends": [{"topic": "x", "momentum": "rising", "evidence": "e"}],
                    "opportunities": ["a"], "sources": ["https://example.org"]}
    o = Orchestrator(bus, FakeClient(responses=[trend_report]), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human", payload=CHANNEL))
    o.run()
    assert "agent_failed" in _audit_actions(bus, "orchestrator")
    assert any(e.payload["action"] == "agent_failed" and json.loads(e.payload["evidence"])["agent"] == "channel-strategist"
              for e in bus.replay("audit-log") if e.actor == "orchestrator")
    assert not o.plans


def test_plan_rejected_when_check_plan_finds_errors(tmp_path):
    bus = InMemoryBus()
    trend_report = {"channel_id": "CH1", "trends": [{"topic": "x", "momentum": "rising", "evidence": "e"}],
                    "opportunities": ["a"], "sources": ["https://example.org"]}
    # brief thiếu estimate_tokens → desk.check_plan trả lỗi → nhánh "plan.rejected"
    bad_brief = {"video_id": "CH1-V1", "channel_id": "CH1", "working_title": "t", "pillar": "p", "angle": "a",
                "audience": "u", "estimate_tokens": None, "budget_tokens": 1000}
    o = Orchestrator(bus, FakeClient(responses=[trend_report, {"items": [bad_brief], "context_writes": []}]),
                     media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human", payload=CHANNEL))
    o.run()
    assert "plan.rejected" in _audit_actions(bus, "orchestrator")
    assert not o.plans
    assert list(o.gate.pending) == []


# ---------- CLI main() ----------

def test_cli_status_and_report(tmp_path, monkeypatch, capsys):
    from studio.orchestrator import main

    db = tmp_path / "s.sqlite"
    monkeypatch.setattr("studio.llm.make_client", lambda: FakeClient())
    rc = main(["--db", str(db), "status"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and "videos" in out and "gates" in out

    rc = main(["--db", str(db), "report"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and "videos" in out


def test_cli_publish_rejects_non_human_topic(tmp_path, capsys):
    from studio.orchestrator import main

    db = tmp_path / "s.sqlite"
    p = tmp_path / "payload.json"
    p.write_text("{}", encoding="utf-8")
    rc = main(["--db", str(db), "publish", "audit-log", str(p)])
    assert rc == 2
    assert "không nạp tay được" in capsys.readouterr().err


def test_cli_publish_accepts_channel_briefs(tmp_path, capsys):
    from studio.orchestrator import main

    db = tmp_path / "s.sqlite"
    p = tmp_path / "payload.json"
    p.write_text(json.dumps(CHANNEL), encoding="utf-8")
    rc = main(["--db", str(db), "publish", "channel-briefs", str(p), "--actor", "human:owner"])
    out = capsys.readouterr().out
    assert rc == 0 and "published channel-briefs" in out
    b = SQLiteBus(db)
    assert list(b.replay("channel-briefs", "CH1"))
    b.close()


def test_cli_run_without_watch_prints_steps_and_status(tmp_path, monkeypatch, capsys):
    from studio.orchestrator import main

    db = tmp_path / "s.sqlite"
    bus = SQLiteBus(db)
    bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human:owner", payload=CHANNEL))
    bus.close()
    monkeypatch.setattr("studio.llm.make_client", lambda: make_scripted_client(plan_size=1))
    rc = main(["--db", str(db), "run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "channel-briefs" in out
    assert '"videos"' in out and '"gates"' in out


# ---------- _decide / _emit / _publish_video / _post_reply lỗi (unit trực tiếp, bỏ qua toàn bộ pipeline) ----------

def test_decide_returns_none_on_llm_error(tmp_path):
    from studio.orchestrator import PUBLISH_ROUTE, StepResult

    bus = InMemoryBus()
    o = Orchestrator(bus, FakeClient(responses=[]), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    env = Envelope(topic="metadata-packages", key="V1", actor="seo-optimizer", payload={"video_id": "V1", "title": "t", "description": "d"})
    res = StepResult(env.event_id, env.topic, env.key)
    p, g = o._decide(PUBLISH_ROUTE, env, res, {"approved_by": "human:editor", "gate_reason": "ok"})
    assert p is None and g is None
    assert any(a.startswith("publisher!") for a in res.actions)
    assert "agent_failed" in _audit_actions(bus, "orchestrator")


def test_emit_appends_failure_action_when_bus_rejects_payload(tmp_path):
    from studio.orchestrator import PUBLISH_ROUTE, StepResult

    bus = InMemoryBus()
    o = Orchestrator(bus, FakeClient(), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    env = Envelope(topic="metadata-packages", key="V1", actor="seo-optimizer", payload={"video_id": "V1", "title": "t", "description": "d"})
    res = StepResult(env.event_id, env.topic, env.key)

    class _FakeGenerated:
        tokens = 0
        model = "m"
        context_writes = []
        cache_hit_ratio = 0.0

    o._emit(PUBLISH_ROUTE, env, {"khong-hop-le": True}, _FakeGenerated(), res)
    assert any(a.startswith("publisher!") for a in res.actions)


def test_publish_video_emits_without_touching_platform_when_model_says_not_ready(tmp_path):
    from studio.orchestrator import StepResult

    bus = InMemoryBus()
    draft = {"video_id": "V1", "kind": "video", "status": "failed", "evidence": "chưa đủ điều kiện đăng"}
    o = Orchestrator(bus, FakeClient(responses=[draft]), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    env = Envelope(topic="metadata-packages", key="V1", actor="seo-optimizer", payload={"video_id": "V1", "title": "t", "description": "d"})
    res = StepResult(env.event_id, env.topic, env.key)
    o._publish_video(env, res, "human:editor", "duyệt")
    assert o.platform.calls == []
    ev = next(e.payload for e in bus.replay("publish-events", "V1"))
    assert ev["status"] == "failed"


def test_post_reply_emits_without_touching_platform_when_model_says_not_ready(tmp_path):
    from studio.orchestrator import StepResult

    bus = InMemoryBus()
    draft = {"video_id": "V1", "status": "failed", "evidence": "chờ duyệt thêm"}
    o = Orchestrator(bus, FakeClient(responses=[draft]), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    env = Envelope(topic="reply-drafts", key="V1", actor="community-manager", payload={
        "video_id": "V1", "comment_id": "C1", "reply": "cam on ban"})
    res = StepResult(env.event_id, env.topic, env.key)
    o._post_reply(env, res, "human:editor")
    assert o.platform.calls == []
    ev = next(e.payload for e in bus.replay("publish-events", "V1"))
    assert ev["status"] == "failed"


# ---------- _audit once-dedup, _thumbs lỗi, repair limit ----------

def test_audit_once_key_is_deduplicated(tmp_path):
    bus = InMemoryBus()
    o = Orchestrator(bus, FakeClient(), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    o._audit("test.action", {"a": 1}, once="dedup-key")
    o._audit("test.action", {"a": 2}, once="dedup-key")  # bị bỏ qua: cùng khoá once
    matches = [e for e in bus.replay("audit-log") if e.payload["action"] == "test.action"]
    assert len(matches) == 1


def test_thumbs_media_error_is_audited(tmp_path, monkeypatch):
    from studio.orchestrator import StepResult

    bus = InMemoryBus()
    o = Orchestrator(bus, FakeClient(), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)

    def boom(spec):
        raise MediaError("tao anh loi")

    monkeypatch.setattr(o.renderer, "thumbnails", boom)
    env = Envelope(topic="thumbnail-specs", key="V1", actor="thumbnail-designer", payload={
        "video_id": "V1", "variants": [{"variant_id": "A", "prompt": "p", "overlay_text": "t"}]})
    res = StepResult(env.event_id, env.topic, env.key)
    o._thumbs(env, res)
    assert "render_failed" in _audit_actions(bus, "orchestrator")


def test_apply_cut_repair_over_limit_audits_and_finalizes(tmp_path):
    bus, o = _to_scene_manifest(tmp_path)
    o.run()
    from studio.events import CutList
    manifest_env = next(e for e in reversed(list(bus.replay("scene-manifests", "CH1-V1"))))
    for _ in range(4):  # MAX_REPAIR_ROUNDS=3: vòng thứ 4 vượt giới hạn → desk.repair_allowed() = False
        cut = CutList(video_id="CH1-V1", manifest_version=manifest_env.payload["version"], decision="repair",
                      repairs=[{"scene_id": manifest_env.payload["scenes"][0]["scene_id"], "action": "regenerate_image", "reason": "chua dep"}])
        bus.publish(Envelope(topic="cut-lists", key="CH1-V1", actor="editor", payload=cut.model_dump()))
        o.run()
        manifest_env = next(e for e in reversed(list(bus.replay("scene-manifests", "CH1-V1"))))
    assert "repair.limit" in _audit_actions(bus, "orchestrator")


# ---------- tick(): gate remind/overdue, review tái phân công, thí nghiệm thumbnail (retention) ----------

def test_tick_reminds_gate(tmp_path):
    from datetime import timedelta

    bus, o = _to_scene_manifest(tmp_path)
    from studio.gates import GateRequest
    o.gate.request(GateRequest(kind="publish", subject_id="PUB-REMIND", created_by="desk", checklist=[]))
    created = o.gate.pending["PUB-REMIND"].created_at
    o.tick(created + timedelta(hours=13))  # > remind_at (12h), < timeout (24h)
    assert any(e.payload["action"] == "gate.remind" for e in bus.replay("audit-log") if e.actor == "orchestrator")


def test_tick_marks_gate_overdue(tmp_path):
    from datetime import timedelta

    bus, o = _to_scene_manifest(tmp_path)
    from studio.gates import GateRequest
    o.gate.request(GateRequest(kind="publish", subject_id="PUB-OVERDUE", created_by="desk", checklist=[]))
    created = o.gate.pending["PUB-OVERDUE"].created_at
    o.tick(created + timedelta(hours=25))  # > timeout (24h): thẳng tới overdue, chưa từng remind (khoá `once` riêng theo sid)
    assert any(e.payload["action"] == "gate.overdue" for e in bus.replay("audit-log") if e.actor == "orchestrator")


def test_tick_reassigns_overdue_review(tmp_path):
    from datetime import UTC, datetime, timedelta

    from studio.events import Envelope as Env
    from studio.events import Provenance, ReviewResult, VideoBrief

    bus, o = _to_scene_manifest(tmp_path)
    vid = "CH1-V1"
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    # dựng thẳng trạng thái "in_review" quá hạn, thiếu đúng review "quality". fact được seed sẵn = pass, đúng như
    # luồng thật (fact luôn pass trước khi vào production) — ROUTES không có tuyến fact-checker đọc media-assets,
    # nên nếu "fact" cũng rơi vào "missing" ở đây thì việc gán lại sẽ StopIteration (trạng thái không xảy ra thật).
    o.desk.briefs[vid] = VideoBrief(video_id=vid, channel_id="CH1", working_title="t", pillar="p", angle="a", audience="u")
    o.desk.state[vid] = "in_review"
    o.desk.reviews[vid]["fact"] = ReviewResult(video_id=vid, source="fact", verdict="pass")
    final_asset = Env(topic="media-assets", key=vid, actor="renderer", payload={
        "video_id": vid, "kind": "final_video", "path": "final.mp4",
        "provenance": Provenance(generated_by="fake:x").model_dump()})
    bus.publish(final_asset)
    o.run()  # rights-checker chạy thật qua route (final_video → rights pass)
    o.desk.reviews[vid].pop("quality", None)  # giả lập quality vẫn còn thiếu, đã quá hạn từ lâu
    o.desk.review_since[vid] = long_ago

    o.tick(long_ago + timedelta(hours=3))  # > review_timeout mặc định (2h)
    assert any(e.payload["action"] == "review.reassign" for e in bus.replay("audit-log") if e.actor == "orchestrator")
    assert o.desk.reviews[vid]["quality"].verdict == "pass"  # gọi lại quality-reviewer, review thiếu được lấp


def test_tick_overdue_review_without_route_is_skipped_not_crashed(tmp_path):
    """`ROUTES` không có tuyến `fact-checker` đọc `media-assets` — nếu "fact" từng lọt vào danh sách
    missing (trạng thái không xảy ra qua luồng thật), `tick()` phải bỏ qua nguồn đó và audit
    `review.reassign_skipped` thay vì `StopIteration` làm crash cả tick."""
    from datetime import UTC, datetime, timedelta

    from studio.events import Envelope as Env
    from studio.events import Provenance, VideoBrief

    bus, o = _to_scene_manifest(tmp_path)
    vid = "CH1-V1"
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    o.desk.briefs[vid] = VideoBrief(video_id=vid, channel_id="CH1", working_title="t", pillar="p", angle="a", audience="u")
    o.desk.state[vid] = "in_review"
    final_asset = Env(topic="media-assets", key=vid, actor="renderer", payload={
        "video_id": vid, "kind": "final_video", "path": "final.mp4",
        "provenance": Provenance(generated_by="fake:x").model_dump()})
    bus.publish(final_asset)
    o.run()  # video "CH1-V1" đã đi qua fixture trước đó nên fact/quality/rights đều đã pass sẵn
    o.desk.reviews[vid].pop("fact", None)  # giả lập "fact" bị rơi mất -> missing, dù luồng thật không tạo ra trạng thái này
    o.desk.review_since[vid] = long_ago

    o.tick(long_ago + timedelta(hours=3))  # không được ném StopIteration
    import json as _json

    audits = [
        (e.payload["action"], _json.loads(e.payload["evidence"]))
        for e in bus.replay("audit-log")
        if e.actor == "orchestrator"
    ]
    assert any(action == "review.reassign_skipped" and data.get("source") == "fact" for action, data in audits)
    assert not any(action == "review.reassign" and data.get("source") == "fact" for action, data in audits)


def test_with_retention_includes_experiment_when_prior_variant_snapshot_exists(tmp_path):
    bus, o = _to_scene_manifest(tmp_path)
    o.run()
    snap_a = Envelope(topic="performance-snapshots", key="CH1-V1", actor="human", payload={
        "video_id": "CH1-V1", "channel_id": "CH1", "variant_id": "A", "views": 100, "impressions": 1000, "ctr": 0.1,
        "avg_view_duration_s": 5, "retention_curve": [{"t": 0, "pct": 100}, {"t": 3, "pct": 90}]})
    bus.publish(snap_a); o.run()
    snap_b = Envelope(topic="performance-snapshots", key="CH1-V1", actor="human", payload={
        "video_id": "CH1-V1", "channel_id": "CH1", "variant_id": "B", "views": 200, "impressions": 1000, "ctr": 0.2,
        "avg_view_duration_s": 6, "retention_curve": [{"t": 0, "pct": 100}, {"t": 3, "pct": 95}]})
    bus.publish(snap_b); o.run()
    rep = [e.payload for e in bus.replay("analytics-reports", "CH1-V1")]
    assert any(r.get("experiments") for r in rep)


def test_cli_run_with_watch_calls_orchestrator_watch(tmp_path, monkeypatch):
    from studio import orchestrator as orch_mod

    db = tmp_path / "s.sqlite"
    monkeypatch.setattr("studio.llm.make_client", lambda: FakeClient())
    called = {}
    monkeypatch.setattr(orch_mod.Orchestrator, "watch", lambda self, interval: called.__setitem__("interval", interval))
    rc = orch_mod.main(["--db", str(db), "run", "--watch", "3.5"])
    assert rc == 0 and called["interval"] == 3.5
