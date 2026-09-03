from datetime import UTC, datetime, timedelta

import pytest

from studio.bus import InMemoryBus
from studio.desk import DeskError, ProductionDesk
from studio.events import Envelope, Provenance, VideoBrief, can_transition
from studio.gate_cli import PersistentGate
from studio.gates import GateRequest
from studio.sqlite_bus import SQLiteBus


def _brief(vid="V1", **kw):
    b = VideoBrief(video_id=vid, channel_id="CH1", working_title="x", pillar="p", angle="a", audience="u",
                   estimate_tokens=10_000, budget_tokens=15_000, **kw)
    return b.model_dump()


def _asset(vid, kind, **kw):
    return {"video_id": vid, "kind": kind, "path": f"{kind}.bin", "provenance": Provenance(generated_by="fake:x").model_dump(), **kw}


def test_plan_checks_estimate_budget_and_duplicates():
    desk = ProductionDesk(InMemoryBus())
    assert desk.check_plan([_brief()]) == []
    assert any("thiếu estimate" in e for e in desk.check_plan([{**_brief(), "estimate_tokens": None}]))
    assert any("budget" in e for e in desk.check_plan([{**_brief(), "budget_tokens": 12_000}]))
    assert any("trùng" in e for e in desk.check_plan([_brief(), _brief()]))
    assert any("priority" in e for e in desk.check_plan([{**_brief(), "priority": 9}]))


def test_video_lifecycle_and_ready_for_publish():
    bus = InMemoryBus(); desk = ProductionDesk(bus)
    desk.dispatch([_brief()])
    assert desk.state["V1"] == "briefed"
    pub = lambda t, p, actor="x": bus.publish(Envelope(topic=t, key="V1", actor=actor, payload=p))  # noqa: E731
    pub("research-dossiers", {"video_id": "V1", "sources": [{"title": "a", "url": "u"}], "evidence": ["e"]})
    pub("scripts", {"video_id": "V1", "working_title": "t", "hook": "h", "sections": [{"heading": "a", "narration": "n"}]})
    assert desk.state["V1"] == "scripted" and not desk.fact_passed("V1")
    pub("review-results", {"video_id": "V1", "source": "fact", "verdict": "pass"})
    assert desk.fact_passed("V1")
    pub("scene-manifests", {"video_id": "V1", "scenes": [{"scene_id": "S1", "order": 0, "narration": "n", "visual_prompt": "p"}]})
    assert desk.state["V1"] == "in_production"
    pub("media-assets", _asset("V1", "final_video"), "renderer")
    assert desk.state["V1"] == "in_review" and not desk.ready_for_publish("V1")
    pub("metadata-packages", {"video_id": "V1", "title": "t", "description": "d"})
    pub("media-assets", _asset("V1", "thumbnail", variant_id="A"), "renderer")
    pub("review-results", {"video_id": "V1", "source": "quality", "verdict": "pass"})
    assert not desk.ready_for_publish("V1")  # thiếu rights
    pub("review-results", {"video_id": "V1", "source": "rights", "verdict": "pass"})
    assert desk.ready_for_publish("V1")
    desk.mark_approved("V1")
    pub("publish-events", {"video_id": "V1", "status": "scheduled"})
    pub("publish-events", {"video_id": "V1", "status": "published"})
    pub("analytics-reports", {"channel_id": "CH1", "video_id": "V1"})
    assert desk.state["V1"] == "analyzed"
    assert can_transition("analyzed", "closed") and not can_transition("closed", "briefed")


def test_rework_publishes_brief_with_hint_then_blocks():
    bus = InMemoryBus(); desk = ProductionDesk(bus, max_retries=2)
    desk.dispatch([_brief()])
    out = desk.rework("V1", "C1 không nguồn", stage="script")
    assert out is not None and out.payload["retry"] == 1 and out.payload["hint"].startswith("[script]")
    assert desk.state["V1"] == "changes_requested"
    desk.rework("V1", "vẫn thiếu")
    assert desk.rework("V1", "lần ba") is None and desk.state["V1"] == "blocked"
    re = desk.reopen("V1", "dùng nguồn primary")
    assert re.payload["retry"] == 0 and desk.state["V1"] == "briefed"


def test_invalid_transition_raises():
    desk = ProductionDesk(InMemoryBus()); desk.dispatch([_brief()])
    with pytest.raises(DeskError):
        desk._set("V1", "published")


def test_repair_rounds_limit_and_overdue_reviews():
    bus = InMemoryBus(); desk = ProductionDesk(bus, review_timeout=timedelta(hours=1)); desk.dispatch([_brief()])
    for _ in range(4):
        bus.publish(Envelope(topic="cut-lists", key="V1", actor="editor", payload={"video_id": "V1", "manifest_version": 1, "decision": "repair"}))
    assert not desk.repair_allowed("V1")
    bus.publish(Envelope(topic="scripts", key="V1", actor="script-writer", payload={"video_id": "V1", "working_title": "t", "hook": "h", "sections": []}))
    bus.publish(Envelope(topic="scene-manifests", key="V1", actor="production-manager", payload={"video_id": "V1", "scenes": []}))
    bus.publish(Envelope(topic="media-assets", key="V1", actor="renderer", payload=_asset("V1", "final_video")))
    later = datetime.now(UTC) + timedelta(hours=2)
    assert desk.overdue_reviews(later) == {"V1": {"fact", "rights", "quality"}}


def test_persistent_gate_rebuilds_from_sqlite(tmp_path):
    db = tmp_path / "s.sqlite"
    bus = SQLiteBus(db); gate = PersistentGate(bus)
    gate.request(GateRequest(kind="publish", subject_id="PUB-V1", checklist=["review:fact:pass"], created_by="desk"))
    with pytest.raises(PermissionError):
        gate.decide("PUB-V1", "approve", by="desk")
    gate.decide("PUB-V1", "approve", by="human:editor", reason="ok")
    bus.close()
    bus2 = SQLiteBus(db); gate2 = PersistentGate(bus2)
    assert gate2.is_approved("PUB-V1") and not gate2.pending
    bus2.close()


def test_supervisor_warns_and_cuts_budget_once_per_video():
    from studio.supervisor import Supervisor
    bus = InMemoryBus(); sup = Supervisor(bus)
    bus.publish(Envelope(topic="video-briefs", key="V1", actor="channel-strategist", payload=_brief()))
    def spend(t):
        bus.publish(Envelope(topic="audit-log", key="x", actor="x", payload={"actor": "x", "action": "produced:scripts", "video_id": "V1", "tokens": t}))
    spend(12_500); spend(100); spend(100)  # 3 lần trên 80%
    assert [a.action for a in sup.actions] == ["warn"]
    spend(3_000); spend(100)  # 2 lần trên 100%
    assert [a.action for a in sup.actions] == ["warn", "budget_cut"]
    assert [e.payload["action"] for e in bus.replay("supervisor-actions")] == ["warn", "budget_cut"]


def test_gate_approver_allowlist_and_triggered_by(tmp_path, monkeypatch):
    from studio.gates import HumanGate, gate_approvers
    from studio.media import MediaConfig
    monkeypatch.delenv("STUDIO_GATE_APPROVERS", raising=False)
    assert gate_approvers(None) == frozenset() and gate_approvers(MediaConfig(gate={"approvers": ["human:owner"]})) == {"human:owner"}
    monkeypatch.setenv("STUDIO_GATE_APPROVERS", "human:owner, human:editor")
    assert gate_approvers(MediaConfig(gate={"approvers": ["x"]})) == {"human:owner", "human:editor"}  # env thắng yaml
    g = HumanGate(approvers=gate_approvers())
    g.request(GateRequest(kind="publish", subject_id="PUB-V1", checklist=[], created_by="desk", triggered_by="human:owner"))
    with pytest.raises(PermissionError, match="danh sách người duyệt"):
        g.decide("PUB-V1", "approve", by="human:intern")
    assert g.decide("PUB-V1", "approve", by="human:editor").triggered_by == "human:owner"
    # bền vững: triggered_by qua audit-log; replay quyết định cũ không kiểm lại danh sách (đã đổi sau đó)
    db = tmp_path / "s.sqlite"; bus = SQLiteBus(db); pg = PersistentGate(bus, approvers={"human:editor"})
    pg.request(GateRequest(kind="publish", subject_id="PUB-V2", checklist=[], created_by="desk", triggered_by="human:owner"))
    pg.decide("PUB-V2", "approve", by="human:editor"); bus.close()
    bus2 = SQLiteBus(db); pg2 = PersistentGate(bus2, approvers={"human:someone-else"})
    assert pg2.history[-1].triggered_by == "human:owner" and pg2.is_approved("PUB-V2"); bus2.close()
    # gate_cli từ chối --by ngoài danh sách (mã 3)
    from studio.gate_cli import main as gate_main
    bus3 = SQLiteBus(db); PersistentGate(bus3).request(GateRequest(kind="publish", subject_id="PUB-V3", checklist=[], created_by="desk")); bus3.close()
    assert gate_main(["--db", str(db), "approve", "PUB-V3", "--by", "human:intern"]) == 3
    assert gate_main(["--db", str(db), "approve", "PUB-V3", "--by", "human:editor"]) == 0
