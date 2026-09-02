from studio.events import NAMESPACE_OWNERS
from studio.registry import SKILLS_DIR, _split, load_agents

EXPECTED = {
    # strategy (2)
    "channel-strategist", "trend-researcher",
    # creative (1)
    "script-writer",
    # production (3)
    "production-manager", "editor", "thumbnail-designer",
    # distribution (3)
    "seo-optimizer", "publisher", "community-manager",
    # quality (3)
    "fact-checker", "rights-checker", "quality-reviewer",
    # analytics (1)
    "analytics-analyst",
    # supervision (1)
    "supervisor",
}


def test_all_14_agents_load():
    agents = load_agents()
    assert set(agents) == EXPECTED
    assert len(agents) == 14


def test_prompts_have_skills_and_dod():
    for a in load_agents().values():
        assert "Definition of done" in a.prompt
        assert a.skill_text, a.id
        assert a.budget_tokens_per_task > 0


def test_namespace_write_matches_events_owner():
    for a in load_agents().values():
        for ns in a.namespaces_write:
            assert a.id in NAMESPACE_OWNERS[ns], (a.id, ns)


def test_every_namespace_owner_is_a_real_agent():
    agents = set(load_agents())
    for ns, owners in NAMESPACE_OWNERS.items():
        assert owners <= agents, (ns, owners - agents)


def test_skill_front_matter_name_matches_filename():
    for p in SKILLS_DIR.glob("*.md"):
        fm, _ = _split(p.read_text(encoding="utf-8"))
        assert fm["name"] == p.stem, p.name
        assert fm.get("version", 0) >= 1


def test_no_orphan_skill():
    used = {s for a in load_agents().values() for s in a.all_skills}
    on_disk = {p.stem for p in SKILLS_DIR.glob("*.md")}
    assert on_disk == used, {"unused": on_disk - used, "missing": used - on_disk}
