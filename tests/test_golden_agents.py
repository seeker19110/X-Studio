"""Golden tests cho 14 agent (prompt là code, kế thừa ADR-0004 của software-company).

Mỗi agent có `tests/golden/agents/<id>.md` = system prompt đã biên dịch; `tests/golden/registry.json` là hợp đồng
cả phòng ban. Sửa agents/ hoặc skills/ → phải tăng `version` và chạy: UPDATE_GOLDEN=1 uv run pytest tests/test_golden_agents.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import get_args

import pytest

from studio.events import NAMESPACE_OWNERS, Topic
from studio.registry import AgentSpec, load_agents

GOLDEN_DIR = Path(__file__).parent / "golden"
AGENT_GOLDEN_DIR = GOLDEN_DIR / "agents"
REGISTRY_GOLDEN = GOLDEN_DIR / "registry.json"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

TOPICS = set(get_args(Topic))
NON_TOPIC_CHANNELS = {"*", "knowledge-base"}
BLOCKS = {"strategy", "creative", "production", "distribution", "quality", "analytics", "supervision"}
MODEL_TIERS = {"standard", "strong", "light"}
REQUIRED_SECTIONS = ["## Vai trò", "## Bạn PHẢI", "## Bạn KHÔNG ĐƯỢC", "## Đầu vào", "## Đầu ra", "## Definition of done"]
_HEADER = re.compile(r"^<!-- golden agent=(?P<id>[\w-]+) version=(?P<version>\d+) -->\n", re.MULTILINE)

AGENTS = load_agents()
IDS = sorted(AGENTS)


def render(a: AgentSpec) -> str:
    return f"<!-- golden agent={a.id} version={a.version} -->\n{a.system_prompt().rstrip()}\n"


def contract(a: AgentSpec) -> dict:
    return {"block": a.block, "model_tier": a.model_tier, "version": a.version, "reads": a.reads, "writes": a.writes,
            "context_namespace_write": a.context_namespace_write, "skills": a.skills, "skills_core": a.skills_core,
            "budget_tokens_per_task": a.budget_tokens_per_task, "max_retries": a.max_retries, "timeout_minutes": a.timeout_minutes}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.mark.parametrize("agent_id", IDS)
def test_agent_prompt_matches_golden(agent_id: str):
    a = AGENTS[agent_id]; path = AGENT_GOLDEN_DIR / f"{agent_id}.md"; expected = render(a)
    if UPDATE:
        _write(path, expected); return
    assert path.exists(), f"thiếu golden cho {agent_id}; chạy: UPDATE_GOLDEN=1 uv run pytest {Path(__file__).name}"
    actual = path.read_text(encoding="utf-8")
    if actual == expected: return
    m = _HEADER.match(actual); old_version = int(m.group("version")) if m else None
    if old_version is not None and old_version >= a.version:
        pytest.fail(f"[{agent_id}] prompt hoặc skill đã đổi nhưng version vẫn là {a.version} (golden ghi {old_version}). "
                    f"Tăng `version` rồi chạy UPDATE_GOLDEN=1 uv run pytest {Path(__file__).name}")
    pytest.fail(f"[{agent_id}] golden lệch (version {old_version} → {a.version}). Cập nhật: UPDATE_GOLDEN=1 uv run pytest {Path(__file__).name}")


def test_no_stale_golden_files():
    on_disk = {p.stem for p in AGENT_GOLDEN_DIR.glob("*.md")} if AGENT_GOLDEN_DIR.exists() else set()
    stale = on_disk - set(IDS)
    if UPDATE:
        for s in stale: (AGENT_GOLDEN_DIR / f"{s}.md").unlink()
        return
    assert not stale, {"stale": stale}


def test_registry_contract_matches_golden():
    expected = {aid: contract(AGENTS[aid]) for aid in IDS}
    text = json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if UPDATE:
        _write(REGISTRY_GOLDEN, text); return
    assert REGISTRY_GOLDEN.exists(), "thiếu tests/golden/registry.json; chạy UPDATE_GOLDEN=1"
    actual = json.loads(REGISTRY_GOLDEN.read_text(encoding="utf-8"))
    diff = {k: (actual.get(k), expected[k]) for k in expected if actual.get(k) != expected[k]}
    assert actual == expected, {"changed (golden, hiện tại)": diff, "removed": set(actual) - set(expected)}


@pytest.mark.parametrize("agent_id", IDS)
def test_front_matter_is_well_formed(agent_id: str):
    a = AGENTS[agent_id]
    assert a.block in BLOCKS, a.block
    assert a.model_tier in MODEL_TIERS, a.model_tier
    for ch in a.reads + a.writes:
        assert ch in TOPICS | NON_TOPIC_CHANNELS, f"{agent_id}: kênh lạ `{ch}`"
    assert a.reads and a.writes
    assert a.max_retries >= 0 and a.timeout_minutes > 0
    assert len(a.all_skills) == len(set(a.all_skills))
    assert a.skills


@pytest.mark.parametrize("agent_id", IDS)
def test_prompt_has_required_sections_in_order(agent_id: str):
    p = AGENTS[agent_id].prompt
    assert p.startswith(f"# {agent_id}\n")
    positions = [p.find(s) for s in REQUIRED_SECTIONS]
    missing = [s for s, i in zip(REQUIRED_SECTIONS, positions, strict=True) if i < 0]
    assert not missing, {"thiếu mục": missing}
    assert positions == sorted(positions)


@pytest.mark.parametrize("agent_id", IDS)
def test_prompt_mentions_its_topics_and_namespace(agent_id: str):
    a = AGENTS[agent_id]
    for t in a.writes:
        if t in TOPICS and t != "audit-log":
            assert t in a.prompt, f"{agent_id}: prompt không nhắc topic ghi `{t}`"
    for ns in a.namespaces_write:
        assert ns in a.prompt, f"{agent_id}: prompt không nhắc namespace `{ns}`"


def test_every_namespace_has_exactly_the_declared_owners():
    declared = {a.id: set(a.namespaces_write) for a in AGENTS.values()}
    for ns, owners in NAMESPACE_OWNERS.items():
        for o in owners:
            assert ns in declared.get(o, set()), f"{o} là owner của `{ns}` nhưng front matter không khai báo"


def test_every_topic_has_a_writer_and_a_reader():
    readers = {t for a in AGENTS.values() for t in a.reads} | {"*"}
    writers = {t for a in AGENTS.values() for t in a.writes}
    human_or_code_written = {"channel-briefs", "performance-snapshots", "audience-comments", "media-assets", "shared-context",
                             "audit-log"}  # audit-log: runner/desk/orchestrator/renderer ghi bằng code
    human_or_code_read = {"cut-lists", "thumbnail-specs", "publish-events", "supervisor-actions", "shared-context", "audit-log"}
    for t in TOPICS:
        assert t in writers or t in human_or_code_written, f"không ai ghi `{t}`"
        assert t in readers or t in human_or_code_read, f"không ai đọc `{t}`"
