"""Nhánh lỗi/hiếm gặp còn thiếu coverage ở registry.py, routing.py, fakes.py, desk.py (check_plan/failing)."""

from __future__ import annotations

import pytest

from studio.bus import InMemoryBus
from studio.desk import ProductionDesk
from studio.events import Envelope
from studio.fakes import scripted
from studio.llm import LLMError
from studio.registry import SKILLS_DIR, _split, load_skill
from studio.routing import Backend, RoutingClient


def _brief(vid="V1", **kw):
    return {"video_id": vid, "channel_id": "CH1", "working_title": "x", "pillar": "p", "angle": "a", "audience": "u",
            "estimate_tokens": 10_000, "budget_tokens": 15_000, **kw}


# ---------- desk.py ----------

def test_check_plan_reports_invalid_brief_schema():
    desk = ProductionDesk(InMemoryBus())
    errs = desk.check_plan([{"video_id": "bad id with space", "channel_id": "CH1"}])
    assert any("brief không hợp lệ" in e for e in errs)


def test_failing_lists_non_pass_reviews():
    bus = InMemoryBus(); desk = ProductionDesk(bus); desk.dispatch([_brief()])
    bus.publish(Envelope(topic="review-results", key="V1", actor="fact-checker", payload={"video_id": "V1", "source": "fact", "verdict": "pass"}))
    bus.publish(Envelope(topic="review-results", key="V1", actor="rights-checker", payload={"video_id": "V1", "source": "rights", "verdict": "fail",
                                                                                             "findings": [{"level": "block", "text": "thieu nguon"}]}))
    fails = desk.failing("V1")
    assert len(fails) == 1 and fails[0].source == "rights"


# ---------- registry.py ----------

def test_split_raises_without_front_matter():
    with pytest.raises(ValueError, match="thiếu front matter"):
        _split("khong co front matter o day")


def test_load_skill_core_only_raises_when_no_core_sections(tmp_path, monkeypatch):
    import studio.registry as reg
    p = tmp_path / "skill-khong-core.md"
    p.write_text("---\nname: x\n---\n# Tiêu đề\nchỉ có văn bản, không có mục lõi\n", encoding="utf-8")
    monkeypatch.setattr(reg, "SKILLS_DIR", tmp_path)
    with pytest.raises(ValueError, match="không tìm thấy mục lõi"):
        reg.load_skill("skill-khong-core", core_only=True)


def test_load_agents_raises_on_duplicate_skill_declaration(tmp_path, monkeypatch):
    import studio.registry as reg
    skills_dir = tmp_path / "skills"; agents_dir = tmp_path / "agents"
    skills_dir.mkdir(); agents_dir.mkdir()
    (skills_dir / "s1.md").write_text("---\nname: s1\n---\n## Quy trình\nx\n## Checklist\ny\n", encoding="utf-8")
    (agents_dir / "a1.md").write_text(
        "---\nid: a1\nblock: b\nmodel_tier: light\nreads: []\nwrites: []\ncontext_namespace_write: null\n"
        "skills: [s1]\nskills_core: [s1]\nbudget_tokens_per_task: 100\nmax_retries: 0\ntimeout_minutes: 10\n"
        "---\nBody\n\nDefinition of done: x\n", encoding="utf-8")
    monkeypatch.setattr(reg, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(reg, "AGENTS_DIR", agents_dir)
    with pytest.raises(ValueError, match="skill vừa đầy đủ vừa rút gọn"):
        reg.load_agents()


# ---------- fakes.py ----------

def test_fake_response_unknown_agent_raises():
    with pytest.raises(ValueError, match="không có kịch bản"):
        scripted("agent-khong-ton-tai", {"video_id": "V1"}, {}, opts={})


# ---------- routing.py ----------

def test_routing_client_raises_when_tools_needed_but_no_backend_supports_it():
    from studio.llm import FakeClient
    b = Backend(name="x", client=FakeClient(), tiers=frozenset({"standard"}), supports_tools=False)
    r = RoutingClient([b])
    with pytest.raises(LLMError, match="không backend nào hỗ trợ tool-use"):
        r.complete(system="s", user="u", schema={}, model_tier="standard", tools=[object()])
