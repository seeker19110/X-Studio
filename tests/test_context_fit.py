"""K3.1b: studio cắt ngữ cảnh bằng `xagents_core.context.fit`.

Trước bản này studio không có trần đầu vào nào — `build_user_message` nối thẳng payload + enrich + blackboard.
Mỗi test dưới đây đỏ khi bỏ bản sửa: bỏ `max_input_chars` khỏi `LLMConfig` → nhóm cấu hình đỏ; bỏ lời gọi `fit`
trong `AgentRunner.generate` → nhóm runner đỏ (prompt dài nguyên, không có audit `context_trimmed`).
"""

from __future__ import annotations

import json

from studio.blackboard import Blackboard
from studio.bus import InMemoryBus
from studio.events import Envelope
from studio.llm import FakeClient, LLMConfig, load_config, make_client
from studio.registry import load_agents
from studio.runner import DEFAULT_MAX_INPUT_CHARS, AgentRunner

AGENTS = load_agents()


def _script_env(narration: str = "n") -> Envelope:
    return Envelope(topic="scripts", key="V1", actor="script-writer", payload={
        "video_id": "V1", "working_title": "t", "hook": "h",
        "sections": [{"heading": "a", "narration": narration}],
        "claims": [{"claim_id": "C1", "text": "42%", "source": "https://x"}]})


# ---------- cấu hình ----------

def test_config_default_and_yaml_and_env(tmp_path, monkeypatch):
    assert LLMConfig().max_input_chars == 120_000
    p = tmp_path / "llm.yaml"
    p.write_text("provider: fake\nmax_input_chars: 40000\n", encoding="utf-8")
    monkeypatch.delenv("STUDIO_MAX_INPUT_CHARS", raising=False)
    assert load_config(p).max_input_chars == 40_000
    monkeypatch.setenv("STUDIO_MAX_INPUT_CHARS", "9000")   # env thắng file
    assert load_config(p).max_input_chars == 9_000


def test_backend_does_not_override_max_input_chars(tmp_path, monkeypatch):
    """Trần prompt là thuộc tính của cả hệ: một `backends:` khai riêng cũng không đổi được, nếu không thì cùng một
    agent bị cắt khác nhau tuỳ tài khoản nào còn hạn mức."""
    monkeypatch.delenv("STUDIO_MAX_INPUT_CHARS", raising=False)
    p = tmp_path / "llm.yaml"
    p.write_text("provider: fake\nmax_input_chars: 40000\nbackends:\n  - name: b1\n    provider: fake\n"
                 "    max_input_chars: 999\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.max_input_chars == 40_000
    assert cfg.backend_config(cfg.backends[0]).max_input_chars == 40_000


def test_make_client_attaches_max_input_chars(monkeypatch):
    monkeypatch.delenv("STUDIO_MAX_INPUT_CHARS", raising=False)
    assert make_client(LLMConfig(provider="fake", max_input_chars=7777)).max_input_chars == 7777
    routed = make_client(LLMConfig(provider="fake", max_input_chars=555,
                                   models={"standard": "m"}, backends=[{"name": "b1", "provider": "fake"}]))
    assert routed.max_input_chars == 555


# ---------- runner ----------

def test_runner_takes_ceiling_from_client_then_default_then_argument():
    bus = InMemoryBus()
    client = FakeClient()
    assert AgentRunner(bus, client, AGENTS).max_input_chars == DEFAULT_MAX_INPUT_CHARS
    client.max_input_chars = 8_000
    assert AgentRunner(bus, client, AGENTS).max_input_chars == 8_000
    assert AgentRunner(bus, client, AGENTS, max_input_chars=3_000).max_input_chars == 3_000


def test_generate_trims_long_payload_and_audits():
    bus = InMemoryBus(); bb = Blackboard(bus)
    client = FakeClient(responses=[{"video_id": "V1", "source": "fact", "verdict": "pass"}])
    runner = AgentRunner(bus, client, AGENTS, bb, max_input_chars=5_000)
    long = "x" * 200_000
    g = runner.generate("fact-checker", _script_env(long), "review-results")
    assert g.payloads   # vẫn chạy được, không phải lỗi

    trims = [e for e in bus.replay("audit-log") if e.payload["action"] == "context_trimmed"]
    assert len(trims) == 1
    report = json.loads(trims[0].payload["evidence"])
    assert report["trimmed_payload"] > 150_000
    # 200k ký tự ≈ 62k token trước khi cắt. Không bám sát trần 5k vì `fit` có sàn cứng `MIN_KEEP*4`: system prompt
    # của fact-checker đã chiếm gần hết trần, cắt payload xuống 0 thì prompt vô nghĩa — thà vượt trần một ít.
    assert report["est_tokens"] < 2_500
    assert report["payload"] < 2_000


def test_generate_trims_extra_under_the_same_ceiling():
    """`extra` (dữ liệu enrich) cũng vào prompt, nên phải chia chung hạn mức với payload — cắt riêng thì tổng vẫn vượt."""
    bus = InMemoryBus(); bb = Blackboard(bus)
    seen: list[str] = []
    client = FakeClient(responses=[{"video_id": "V1", "source": "fact", "verdict": "pass"}])
    orig = client.complete

    def spy(*a, **k):
        seen.append(a[1] if len(a) > 1 else k["user"])
        return orig(*a, **k)
    client.complete = spy  # type: ignore[method-assign]

    runner = AgentRunner(bus, client, AGENTS, bb, max_input_chars=6_000)
    runner.generate("fact-checker", _script_env(), "review-results", extra={"notes": "y" * 200_000})
    assert len(seen[0]) < 6_000
    assert "y" * 100_000 not in seen[0]


def test_generate_does_not_trim_or_audit_when_input_fits():
    bus = InMemoryBus(); bb = Blackboard(bus)
    client = FakeClient(responses=[{"video_id": "V1", "source": "fact", "verdict": "pass"}])
    runner = AgentRunner(bus, client, AGENTS, bb)
    env = _script_env()
    runner.generate("fact-checker", env, "review-results")
    assert not [e for e in bus.replay("audit-log") if e.payload["action"] == "context_trimmed"]
    assert env.payload["sections"][0]["narration"] == "n"   # envelope gốc không bị đụng
