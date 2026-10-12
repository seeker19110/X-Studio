"""Dashboard reads durable creative work without leaking adapter internals."""

import json

import pytest

from studio.events import Envelope, MediaAsset, Provenance, VideoBrief
from studio.factory import Factory
from studio.media import MediaConfig


@pytest.fixture
def dashboard_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    factory = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True)
    factory.command("pause", {}); factory.cycle()
    try:
        yield factory
    finally:
        factory.close()


def add_brief(factory):
    brief = VideoBrief(video_id="V1", channel_id="CH1", working_title="Một câu chuyện", pillar="Giáo dục",
                       angle="Góc nhìn thực tế", audience="Người mới", key_points=["Thông điệp chính"],
                       boundaries=["Không hứa kết quả"], estimate_tokens=10000, budget_tokens=15000)
    factory.orch.desk.dispatch([brief.model_dump()])
    return brief.model_dump(exclude={"hint"})


def test_dashboard_snapshot_marks_missing_artifacts_as_missing(dashboard_factory):
    expected_brief = add_brief(dashboard_factory)
    dashboard_factory.refresh()
    project = dashboard_factory.snapshot()["projects"][0]
    assert project["brief"] == expected_brief
    for field in ("request", "research", "script", "metadata", "publication", "performance"):
        assert project[field] is None


def test_dashboard_snapshot_shows_latest_work_and_strips_private_fields_after_restart(dashboard_factory):
    factory = dashboard_factory
    add_brief(factory)

    def publish(topic, actor, payload, key="V1"):
        return factory.bus.publish(Envelope(topic=topic, key=key, actor=actor, payload=payload))

    request = {"title": "Một câu chuyện", "audience": "Người mới", "target_minutes": 8, "format": "long",
               "language": "vi", "boundaries": ["Hook: chuyện gì xảy ra tiếp theo?", "CTA: thử một bước nhỏ"],
               "budget_tokens": 15000}
    publish("channel-briefs", "human:owner", {"channel_id": "CH1", "goals": ["Giáo dục"],
            "audience": "Người mới", "pillars": ["Học hỏi"], "requested_video": {**request, "api_key": "private"}}, "CH1")
    research = {"video_id": "V1", "topic": "Thói quen học hỏi", "sources": [{"title": "Nghiên cứu",
                "url": "https://example.org/research", "type": "paper", "accessed": "2026-10-11"}],
                "competitor_videos": [{"title": "Một ví dụ", "url": "https://example.org/video", "views": 42}],
                "evidence": ["Bằng chứng có nguồn"], "gaps": ["Một câu hỏi còn mở"], "assumptions": ["Giả định cần kiểm tra"]}
    publish("research-dossiers", "trend-researcher", {**research, "path": "/private/research.json",
            "sources": [{**research["sources"][0], "credentials": "private"}],
            "competitor_videos": [{**research["competitor_videos"][0], "cache_path": "/private/cache"}]})
    script = {"video_id": "V1", "version": 1, "working_title": "Bản đầu", "hook": "Điều gì thay đổi?",
              "sections": [{"heading": "Mở đầu", "narration": "Một câu chuyện", "visual_notes": "Cảnh bàn làm việc",
                            "claim_ids": ["C1"]}], "cta": "Thử một bước nhỏ", "claims": [{"claim_id": "C1",
              "text": "Một dữ kiện", "source": "https://example.org/research", "needs_verification": True}],
              "word_count": 30, "estimated_minutes": 1}
    publish("scripts", "script-writer", script)
    script.update(version=2, working_title="Bản sửa mới nhất", hook="Bạn có một phút?")
    publish("scripts", "script-writer", {**script, "api_key": "private",
            "sections": [{**script["sections"][0], "path": "/private/narration.txt"}],
            "claims": [{**script["claims"][0], "credentials": "private"}]})
    metadata = {"video_id": "V1", "title": "Một tiêu đề rõ ràng", "description": "Mô tả có nguồn",
                "tags": ["học hỏi"], "chapters": [{"time": "00:00", "label": "Bắt đầu"}],
                "primary_keyword": "học hỏi", "language": "vi", "category": "Education", "alt_titles": ["Một góc nhìn khác"]}
    publish("metadata-packages", "seo-optimizer", {**metadata, "output_path": "/private/metadata.json",
            "chapters": [{**metadata["chapters"][0], "credentials": "private"}]})
    publication = {"video_id": "V1", "kind": "video", "status": "failed", "scheduled_at": None,
                   "url": "https://www.youtube.com/watch?v=real-ref", "platform_ref": "real-ref"}
    publish("publish-events", "orchestrator", {**publication,
            "evidence": json.dumps({"file": "/private/video.mp4", "credentials": "private"}), "api_key": "private"})
    publish("publish-events", "orchestrator", {"video_id": "V1", "kind": "reply", "status": "published",
            "platform_ref": "reply:C1", "evidence": "Reply should not replace the video publication"})
    performance = {"video_id": "V1", "channel_id": "CH1", "window_days": 7, "views": 123,
                   "impressions": 1000, "ctr": 0.123, "avg_view_duration_s": 45, "retention_curve": [{"t": 10, "pct": 80}],
                   "likes": 12, "comments": 3, "variant_id": None}
    publish("performance-snapshots", "adapter:youtube", {**performance, "api_key": "private",
            "retention_curve": [{**performance["retention_curve"][0], "path": "/private/metrics"}]})
    factory.refresh()
    project = factory.snapshot()["projects"][0]
    assert project["request"] == request
    assert project["research"] == research
    assert project["script"] == script
    assert project["metadata"] == metadata
    assert project["publication"] == publication
    assert project["performance"] == performance
    assert "/private/" not in json.dumps(project, ensure_ascii=False)
    # Mutating the UI's copy must not change the durable work or the next snapshot.
    project["script"]["sections"][0]["narration"] = "Changed in browser"
    assert factory.snapshot()["projects"][0]["script"]["sections"][0]["narration"] == "Một câu chuyện"
    factory.close()
    reopened = Factory(factory.bus.path, MediaConfig(output_dir=factory.orch.renderer.out_dir), demo=True)
    try:
        restored = reopened.snapshot()["projects"][0]
        assert restored["request"] == request
        assert restored["script"] == script
        assert restored["publication"] == publication
        assert restored["performance"] == performance
        assert not reopened.snapshot()["running"]
    finally:
        reopened.close()


def test_dashboard_snapshot_handles_imported_channel_without_structured_request(dashboard_factory):
    add_brief(dashboard_factory)
    dashboard_factory.bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human:owner", payload={
        "channel_id": "CH1", "goals": ["Giáo dục"], "audience": "Người mới", "pillars": ["Học hỏi"],
        "requested_video": "Imported channel has no structured project request"}))
    dashboard_factory.refresh()
    assert dashboard_factory.snapshot()["projects"][0]["request"] is None


def test_rejected_create_projects_expose_planning_error_without_changing_command_status(dashboard_factory):
    factory = dashboard_factory
    command_id = factory.command("create", {"title": "Budget rejection", "budget_tokens": 1000})
    factory.command("run", {})
    for _ in range(8): factory.cycle()
    command = next(c for c in factory.snapshot()["commands"] if c["id"] == command_id)
    assert command["status"] == "done"
    assert "budget" in command["planning_error"]
    assert not factory.snapshot()["projects"]
    factory.close()
    reopened = Factory(factory.bus.path, MediaConfig(output_dir=factory.orch.renderer.out_dir), demo=True)
    try:
        restored = next(c for c in reopened.snapshot()["commands"] if c["id"] == command_id)
        assert restored["planning_error"] == command["planning_error"]
    finally:
        reopened.close()


def test_pending_and_successful_create_projects_do_not_expose_planning_error(dashboard_factory):
    factory = dashboard_factory
    command_id = factory.command("create", {"title": "Valid project"})
    factory.cycle()
    assert not next(c for c in factory.snapshot()["commands"] if c["id"] == command_id).get("planning_error")
    factory.command("settings", {"automation": "production"})
    factory.command("run", {})
    for _ in range(12):
        factory.cycle()
        if factory.snapshot()["projects"]: break
    assert factory.snapshot()["projects"]
    assert not next(c for c in factory.snapshot()["commands"] if c["id"] == command_id).get("planning_error")


def test_failed_plan_generation_exposes_channel_error_and_ignores_malformed_evidence(dashboard_factory, monkeypatch):
    from studio.runner import RunnerError
    factory = dashboard_factory
    command_id = factory.command("create", {"title": "Transport independent model failure"})
    factory.cycle()
    cid = "P" + command_id[:24]
    for evidence in ("{broken", "[]", '{"errors": null}'):
        factory.bus.publish(Envelope(topic="audit-log", key="orchestrator", actor="orchestrator", payload={
            "actor": "orchestrator", "action": "plan.rejected", "channel_id": cid, "evidence": evidence}))
    factory.refresh()
    assert not next(c for c in factory.snapshot()["commands"] if c["id"] == command_id).get("planning_error")
    factory.orch.run(max_steps=1)
    generate = factory.orch.runner.generate

    def fail_plan(agent, *args, **kwargs):
        if agent == "channel-strategist": raise RunnerError("Invalid model planning output")
        return generate(agent, *args, **kwargs)

    monkeypatch.setattr(factory.orch.runner, "generate", fail_plan)
    factory.command("run", {}); factory.cycle()
    command = next(c for c in factory.snapshot()["commands"] if c["id"] == command_id)
    assert command["status"] == "done"
    assert "Invalid model planning output" in command["planning_error"]


def test_dashboard_media_carries_manifest_version_so_the_ui_never_pairs_a_stale_contact_sheet(dashboard_factory):
    """Console chỉ hiện contact sheet cùng phiên bản với bản cuối (ADR-0016): snapshot phải mang `manifest_version`."""
    add_brief(dashboard_factory)
    for kind, version in (("final_video", 2), ("contact_sheet", 1)):
        asset = MediaAsset(video_id="V1", kind=kind, path=f"/out/V1/{kind}.bin", manifest_version=version,
                           provider="ffmpeg", provenance=Provenance(generated_by="ffmpeg:test"))
        dashboard_factory.bus.publish(Envelope(topic="media-assets", key="V1", actor="renderer", payload=asset.model_dump()))
    dashboard_factory.refresh()
    media = dashboard_factory.snapshot()["projects"][0]["media"]
    assert [(m["kind"], m["manifest_version"]) for m in media] == [("final_video", 2), ("contact_sheet", 1)]
