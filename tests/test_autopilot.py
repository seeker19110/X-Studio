"""Delegated automation has its own identity; it cannot forge a human approval."""
import json

from studio.autopilot import Autopilot
from studio.bus import InMemoryBus
from studio.events import Envelope
from studio.fakes import make_scripted_client
from studio.gate_cli import PersistentGate, trusted_decision
from studio.media import MediaConfig, make_media
from studio.orchestrator import Orchestrator


def workshop(tmp_path, monkeypatch, mode="production"):
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    cfg = MediaConfig(output_dir=tmp_path, production={"automation": mode})
    o = Orchestrator(InMemoryBus(), make_scripted_client(plan_size=1, repairs=0), media=make_media(cfg))
    o.bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human:owner", payload={
        "channel_id": "CH1", "goals": ["giáo dục"], "audience": "mọi người", "pillars": ["khoa học"],
        "cadence": "1 video", "language": "vi", "boundaries": []}))
    o.run()
    return o


def test_production_mode_advances_plan_but_holds_publication(tmp_path, monkeypatch):
    o = workshop(tmp_path, monkeypatch); auto = Autopilot(o)
    assert auto.advance() == 1
    o.run()
    assert "PUB-CH1-V1" in o.gate.pending
    assert auto.advance() == 0 and not list(o.bus.replay("publish-events"))
    decisions = [e for e in o.bus.replay("audit-log") if e.payload["action"] == "gate.decide"]
    assert decisions[0].actor == "policy:studio"
    assert trusted_decision(decisions[0])["by"] == "policy:studio"
    assert "PLAN-CH1-1" not in PersistentGate(o.bus).pending


def test_manual_mode_never_advances_and_fake_media_never_goes_live(tmp_path, monkeypatch):
    o = workshop(tmp_path, monkeypatch, "manual")
    assert Autopilot(o).advance() == 0
    o.renderer.media.cfg.production["automation"] = "publish"
    a = Autopilot(o); a.advance(); o.run()
    assert a.advance() == 0
    assert "fake" in " ".join(a.holds.values()).lower()
    assert not list(o.bus.replay("publish-events"))


def test_forged_or_tampered_policy_receipt_is_ignored(tmp_path, monkeypatch):
    o = workshop(tmp_path, monkeypatch); Autopilot(o).advance()
    event = next(e for e in o.bus.replay("audit-log") if e.payload["action"] == "gate.decide")
    data = json.loads(event.payload["evidence"]); data["subject_id"] = "PUB-CH1-V1"
    forged = event.model_copy(deep=True); forged.payload["evidence"] = json.dumps(data)
    assert trusted_decision(forged) is None
    forged.actor = "script-writer"
    assert trusted_decision(forged) is None


def test_reopened_gate_rejects_receipt_for_old_generation(tmp_path, monkeypatch):
    from studio.gates import GateRequest
    o = workshop(tmp_path, monkeypatch); Autopilot(o).advance()
    event = next(e for e in o.bus.replay("audit-log") if e.payload["action"] == "gate.decide")
    sid = json.loads(event.payload["evidence"])["subject_id"]
    o.gate.request(GateRequest(kind="plan", subject_id=sid, created_by="channel-strategist", checklist=[]))
    o.gate.apply(event)
    assert sid in o.gate.pending


def test_held_gate_does_not_rescan_long_video_every_worker_tick(tmp_path, monkeypatch):
    from unittest.mock import Mock
    o = workshop(tmp_path, monkeypatch); auto = Autopilot(o)
    auto.assess = Mock(return_value="QC warning")
    assert auto.advance() == auto.advance() == 0
    assert auto.assess.call_count == 1


def test_orchestrator_ignores_policy_receipt_without_accepted_gate(tmp_path,monkeypatch):
    from studio.orchestrator import StepResult
    from studio.policy_receipts import sign
    o=workshop(tmp_path,monkeypatch)
    data=sign({'subject_id':'PLAN-CH1-1','by':'policy:studio','decision':'approve','policy':'production','request_at':'stale'})
    event=Envelope(topic='audit-log',key='policy:studio',actor='policy:studio',payload={'actor':'policy:studio','action':'gate.decide','evidence':json.dumps(data)})
    result=StepResult(event.event_id,event.topic,event.key)
    o._on_gate_decision(event,result)
    assert 'untrusted:policy:studio' in result.actions
