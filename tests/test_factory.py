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


def test_command_store_closes_connection_after_transaction(tmp_path):
    import sqlite3
    store = CommandStore(tmp_path / "commands.sqlite")
    with store.connect() as con:
        assert con.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        con.execute("SELECT 1")


def test_factory_rejects_malformed_command_bodies_before_enqueueing(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    f = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True)
    try:
        for kind in ("create", "settings", "gate", "run", "pause"):
            for body in (None, [], "invalid"):
                with pytest.raises(ValueError, match="object"): f.command(kind, body)
        for body in ({"decision": "approve"}, {"subject_id": [], "decision": "approve"},
                     {"subject_id": "ESC-V", "decision": "approve", "reason": []}):
            with pytest.raises(ValueError): f.command("gate", body)
        assert not f.commands.pending()
    finally:
        f.close()


def test_factory_marks_obsolete_gate_commands_as_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    f = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True)
    try:
        f.command("gate", {"subject_id": "PUB-missing", "decision": "approve"})
        f.cycle()
        command = f.commands.recent()[0]
        assert command["status"] == "error" and "gate" in command["error"]
    finally:
        f.close()


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


def test_creator_audience_boundaries_and_language_reach_script_writer(tmp_path, monkeypatch):
    from studio.longform import generate_partitioned

    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    f = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out", production={"automation": "production"}), demo=True)
    supplied = []

    def capture(runner, agent, inp, topic, extra, *args, **kwargs):
        if agent == "script-writer": supplied.append(extra["brief"])
        return generate_partitioned(runner, agent, inp, topic, extra, *args, **kwargs)

    monkeypatch.setattr("studio.longform.generate_partitioned", capture)
    try:
        f.command("create", {"title": "A creator's story", "audience": "Physics professors only", "language": "en",
                             "boundaries": ["Hook mong muốn: EXACT_HOOK_42", "Thông điệp chính: EXACT_MESSAGE_99",
                                            "không hứa thu nhập", "Hook mong muốn: EXACT_HOOK_42"]})
        for _ in range(24):
            f.cycle()
            if supplied: break
        assert supplied
        brief = supplied[0]
        assert brief["audience"] == "Physics professors only"
        assert "Hook mong muốn: EXACT_HOOK_42" in brief["boundaries"]
        assert "Thông điệp chính: EXACT_MESSAGE_99" in brief["boundaries"]
        assert brief["boundaries"].count("không hứa thu nhập") == 1
        assert brief["boundaries"].count("Hook mong muốn: EXACT_HOOK_42") == 1
        assert any("en" in instruction for instruction in brief["boundaries"])
    finally:
        f.close()


@pytest.mark.parametrize("format,language,aspect", [("long", "en", "16:9"), ("short", "en", "9:16")])
def test_creator_format_and_language_bind_actual_scene_manifest(tmp_path, monkeypatch, format, language, aspect):
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    f = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out", production={"automation": "production"}), demo=True)
    generate = f.orch.runner.generate
    seo_calls = []

    def bad_first_metadata(agent, *args, **kwargs):
        result = generate(agent, *args, **kwargs)
        if agent == "seo-optimizer":
            seo_calls.append(agent)
            if len(seo_calls) == 1: result.payloads[0]["title"] = "X" * 120
        return result

    monkeypatch.setattr(f.orch.runner, "generate", bad_first_metadata)
    try:
        f.command("create", {"title": "Creator format", "format": format, "language": language,
                             "target_minutes": .8 if format == "short" else 8})
        for _ in range(24):
            f.cycle()
            manifests = list(f.bus.replay("scene-manifests"))
            if any(g.kind == "publish" for g in f.orch.gate.pending.values()): break
        assert manifests
        manifest = manifests[0].payload
        assert manifest["aspect"] == aspect
        assert manifest["voice"]["language"] == language
        assert manifest["voice"]["voice_id"] == "alloy"
        assert len(seo_calls) == 2
        metadata = list(f.bus.replay("metadata-packages"))
        assert metadata and all(event.payload["language"] == language for event in metadata)
    finally:
        f.close()
