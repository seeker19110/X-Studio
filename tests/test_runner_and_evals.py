import json

import pytest

from studio.blackboard import Blackboard
from studio.bus import InMemoryBus
from studio.evals import RecordingClient, ReplayClient, check, load_cases, run_eval, stale_recordings
from studio.events import Envelope
from studio.fakes import make_scripted_client
from studio.llm import FakeClient, LLMConfig, LLMError, load_config, make_client
from studio.registry import load_agents
from studio.runner import AgentRunner, RunnerError, build_user_message, output_schema

AGENTS = load_agents()


def _script_env():
    return Envelope(topic="scripts", key="V1", actor="script-writer", payload={
        "video_id": "V1", "working_title": "t", "hook": "h", "sections": [{"heading": "a", "narration": "n"}],
        "claims": [{"claim_id": "C1", "text": "42%", "source": "https://x"}]})


def test_runner_publishes_valid_output_and_audits_tokens():
    bus = InMemoryBus(); bb = Blackboard(bus)
    client = FakeClient(responses=[{"video_id": "V1", "source": "fact", "verdict": "pass", "findings": []}])
    r = AgentRunner(bus, client, AGENTS, bb).run("fact-checker", _script_env(), "review-results")
    assert r.output.topic == "review-results" and r.tokens == 1300
    audit = [e.payload for e in bus.replay("audit-log")]
    assert audit[-1]["action"] == "produced:review-results" and audit[-1]["tokens"] == 1300 and audit[-1]["video_id"] == "V1"
    assert client.calls[0]["cache_key"] == "fact-checker" and "DỮ LIỆU" in client.calls[0]["user"]


def test_runner_rejects_wrong_topic_and_invalid_output():
    bus = InMemoryBus()
    with pytest.raises(RunnerError):
        AgentRunner(bus, FakeClient(), AGENTS).run("fact-checker", _script_env(), "scripts")
    client = FakeClient(responses=[{"video_id": "V1", "source": "fact"}])  # thiếu verdict
    with pytest.raises(RunnerError):
        AgentRunner(bus, client, AGENTS).run("fact-checker", _script_env(), "review-results")
    assert any(e.payload["action"] == "invalid_output" for e in bus.replay("audit-log"))


def test_context_writes_only_to_owned_namespace():
    bus = InMemoryBus(); bb = Blackboard(bus)
    client = FakeClient(responses=[{"payload": {"video_id": "V1", "source": "rights", "verdict": "pass"},
                                    "context_writes": [{"namespace": "rights", "content_ref": "p.json", "summary": "ok"},
                                                       {"namespace": "seo", "content_ref": "x", "summary": "lạ"}]}])
    env = Envelope(topic="media-assets", key="V1", actor="renderer", payload={"video_id": "V1", "kind": "final_video", "path": "f",
                                                                             "provenance": {"generated_by": "fake:x"}})
    AgentRunner(bus, client, AGENTS, bb).run("rights-checker", env, "review-results")
    assert bb.read("rights") is not None and bb.read("seo") is None
    assert any(e.payload["action"] == "context_rejected" for e in bus.replay("audit-log"))


def test_output_schema_wrapping_and_extra_block():
    spec = AGENTS["script-writer"]
    s = output_schema({"type": "object", "properties": {}}, spec.namespaces_write, many=False)
    assert set(s["properties"]) == {"payload", "context_writes"}
    msg = build_user_message(spec, _script_env(), "scripts", {}, extra={"brief": {"video_id": "V1"}})
    assert "Dữ liệu bổ sung" in msg and '"payload"' in msg


def test_llm_config_env_overrides(monkeypatch, tmp_path):
    (tmp_path / "llm.yaml").write_text("provider: openai\nmodels: {strong: m1, standard: m2}\nbase_url: http://x/v1\n", encoding="utf-8")
    monkeypatch.setenv("STUDIO_MODEL_STRONG", "m9")
    cfg = load_config(tmp_path / "llm.yaml")
    assert cfg.provider == "openai" and cfg.model_for("strong") == "m9" and cfg.model_for("standard") == "m2"
    with pytest.raises(LLMError):
        LLMConfig().model_for("strong")
    with pytest.raises(LLMError):
        make_client(LLMConfig(provider="gemini-native"))


def test_scripted_client_covers_every_agent_eval_case():
    """Mọi ca eval chạy được với client giả có kịch bản (bảo đảm ca eval + schema + prompt khớp nhau)."""
    for aid in sorted(AGENTS):
        cases = load_cases(aid)
        assert len(cases) >= 2, f"{aid} cần ≥ 2 ca eval"
        for r in run_eval(aid, make_scripted_client(fact_verdict="block" if aid == "fact-checker" else "pass"), AGENTS):
            assert r.passed or all("FakeClient" not in f for f in r.failures), (aid, r)


def test_check_criteria():
    p = {"a": 1, "b": "Hello", "c": [1, 2], "d": [{"x": "block"}]}
    assert check(p, {"equals": {"a": 1}, "contains": {"b": "hell"}, "min_len": {"c": 2}, "max_len": {"c": 2}, "one_of": {"d.0.x": ["block"]}}) == []
    assert len(check(p, {"equals": {"a": 2}, "min_len": {"c": 3}})) == 2


def test_record_and_replay_roundtrip(tmp_path, monkeypatch):
    import studio.evals as ev
    monkeypatch.setattr(ev, "RECORDINGS_DIR", tmp_path)
    rec = RecordingClient(make_scripted_client(), "fact-checker")
    res = run_eval("fact-checker", rec, AGENTS); assert all(r.passed for r in res)
    p = rec.save(); data = json.loads(p.read_text(encoding="utf-8"))
    assert data["agent"] == "fact-checker" and len(data["cases"]) == len(load_cases("fact-checker"))
    res2 = run_eval("fact-checker", ReplayClient("fact-checker"), AGENTS)
    assert all(r.passed for r in res2) and stale_recordings(["fact-checker"]) == {}
    with pytest.raises(LLMError):
        ReplayClient("editor")
