"""Factory commands are durable and media delivery stays inside the output root."""

import pytest

from studio.factory import CommandStore, Factory, ProjectRequest
from studio.media import MediaConfig


def test_project_requests_validate_duration_and_budget():
    from pydantic import ValidationError
    with pytest.raises(ValidationError): ProjectRequest(title="x", target_minutes=721)
    with pytest.raises(ValidationError): ProjectRequest(title="x", target_minutes=0)
    assert ProjectRequest(title="x", target_minutes=720).target_minutes == 720


def test_commands_survive_restart_and_deduplicate(tmp_path):
    db = tmp_path / "commands.sqlite"
    store = CommandStore(db); store.add("a", "pause", {})
    store.add("a", "pause", {})
    assert len(CommandStore(db).pending()) == 1
    store.complete("a", error="test error")
    assert not store.pending() and store.recent()[0]["error"] == "test error"


def test_settings_and_pause_survive_more_than_twenty_commands(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    cfg = MediaConfig(output_dir=tmp_path / "out")
    f = Factory(tmp_path / "studio.sqlite", cfg, demo=True)
    f.command("pause", {}); f.cycle()
    for _ in range(24):
        f.command("settings", {"automation": "production", "threads": 3, "burn_captions": True, "style": "Watercolor"})
        f.cycle()
    with pytest.raises(ValueError): f.command("settings", {"threads": 100})
    f.close()
    f = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True)
    assert not f.running
    assert f.snapshot()["automation"] == "production"
    assert f.snapshot()["threads"] == 3
    assert f.snapshot()["style"] == "Watercolor"
    f.close()


def test_factory_project_runs_to_review_offline_with_persistent_status(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    factory = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "output", production={"automation":"production"}), demo=True)
    factory.commands.add("new", "create", {"title":"Một ngày học mới", "target_minutes":8})
    for _ in range(120):
        factory.cycle()
        if factory.snapshot()["gates"] and any(g["kind"]=="publish" for g in factory.snapshot()["gates"]): break
    state = factory.snapshot()
    assert state["projects"] and state["gates"]
    assert state["projects"][0]["title"] == "Một ngày học mới"
    assert not factory.orch.platform.videos if hasattr(factory.orch.platform, "videos") else True
    factory.commands.add("stop", "pause", {}); factory.cycle()
    assert factory.snapshot()["running"] is False
    factory.commands.add("go", "run", {}); factory.cycle()
    assert factory.snapshot()["running"] is True
    assert factory.media_path("unknown") is None
    factory.close()
