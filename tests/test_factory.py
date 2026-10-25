"""Factory commands are durable and media delivery stays inside the output root."""

import pytest

from studio.factory import CommandStore, Factory, ProjectRequest
from studio.media import MediaConfig


def test_project_requests_validate_duration_and_budget():
    from pydantic import ValidationError
    with pytest.raises(ValidationError): ProjectRequest(title="x", target_minutes=721)
    with pytest.raises(ValidationError): ProjectRequest(title="x", target_minutes=0)
    assert ProjectRequest(title="x", target_minutes=720).target_minutes == 720


@pytest.mark.parametrize("credential", [None, "invalid", '{"refresh_token":"test-token"}'])
def test_snapshot_distinguishes_youtube_adapter_from_oauth(tmp_path, monkeypatch, credential):
    from studio.platform import TokenStore, YouTubePlatform
    monkeypatch.setenv("STUDIO_POLICY_KEY_FILE", str(tmp_path / "policy.key"))
    f = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True)
    try:
        token_path = tmp_path / "youtube.json"
        if credential is not None: token_path.write_text(credential)
        f.orch.platform = YouTubePlatform(TokenStore(token_path))
        f.refresh()
        snapshot = f.snapshot()
        assert snapshot["platform"] == "youtube"
        assert snapshot["youtube_oauth_configured"] is (credential is not None and credential != "invalid")
        assert "test-token" not in str(snapshot)
    finally:
        f.close()


def test_commands_are_visible_while_the_model_stage_is_running(tmp_path, monkeypatch):
    f = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True)
    try:
        command = f.command("create", {"title": "Live production"})
        def model_stage(**kwargs):
            snapshot = f.snapshot()
            assert snapshot["running"] is True
            assert snapshot["commands"][0]["id"] == command
            assert snapshot["commands"][0]["status"] == "done"
            assert snapshot["queue"] > 0
        monkeypatch.setattr(f.orch, "tick", model_stage)
        f.cycle()
    finally:
        f.close()


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


class _StorageBlocked(ValueError):
    pass


class _StorageStub:
    def __init__(self):
        self.blocked = False
        self.checks = 0
        self.forces = []

    def snapshot(self, force=False):
        return {"status": "blocked" if self.blocked else "normal", "total_bytes": 42}

    def check_admission(self, *, force=True):
        self.checks += 1
        self.forces.append(force)
        if self.blocked: raise _StorageBlocked("workspace hard limit")
        return self.snapshot()


def _factory_with_storage(tmp_path, monkeypatch):
    monitor = _StorageStub()
    monkeypatch.setattr("studio.factory.StorageMonitor", lambda cfg, db: monitor, raising=False)
    monkeypatch.setattr("studio.factory.StorageAdmissionError", _StorageBlocked, raising=False)
    return Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True), monitor


def test_storage_blocks_new_admission_and_is_visible_in_snapshot(tmp_path, monkeypatch):
    factory, monitor = _factory_with_storage(tmp_path, monkeypatch)
    try:
        monitor.blocked = True
        with pytest.raises(_StorageBlocked, match="hard limit"):
            factory.command("create", {"title": "new project"})
        assert factory.commands.pending() == []
        factory.refresh()
        assert factory.snapshot()["storage"] == {"status": "blocked", "total_bytes": 42}
    finally:
        factory.close()


def test_storage_waiting_creates_do_not_starve_controls_or_existing_work(tmp_path, monkeypatch):
    from studio.gates import GateRequest

    factory, monitor = _factory_with_storage(tmp_path, monkeypatch)
    try:
        for index in range(25): factory.command("create", {"title": f"project {index}"})
        monitor.blocked = True  # space filled after acceptance, before dispatch
        controls = [factory.command("pause", {}), factory.command("run", {}),
                    factory.command("settings", {"threads": 3})]
        factory.orch.gate.request(GateRequest(kind="publish", subject_id="PUB-wait", created_by="desk", checklist=[]))
        controls.append(factory.command("gate", {"subject_id": "PUB-wait", "decision": "hold"}))
        ticks = []
        monkeypatch.setattr(factory.orch, "tick", lambda **kwargs: ticks.append(1))
        monkeypatch.setattr(factory.autopilot, "advance", lambda: None)
        factory.cycle()
        with factory.commands.connect() as con:
            rows = {row["id"]: row["status"] for row in con.execute("SELECT id,status FROM factory_commands")}
        assert all(rows[id] == "done" for id in controls)
        assert sum(status == "pending" for status in rows.values()) == 25
        assert not list(factory.bus.replay("channel-briefs"))
        assert ticks == [1] and factory.running
        assert monitor.forces[:25] == [True] * 25
        assert monitor.forces[-1] is False
        monitor.blocked = False
        factory.cycle(); factory.cycle()
        assert not factory.commands.pending()
        assert len(list(factory.bus.replay("channel-briefs"))) == 25
    finally:
        factory.close()


def test_storage_does_not_readmit_already_published_create_after_restart(tmp_path, monkeypatch):
    factory, monitor = _factory_with_storage(tmp_path, monkeypatch)
    command = factory.command("create", {"title": "already admitted"})
    factory.execute(factory.commands.pending()[0])  # crash window before command completion
    factory.close()
    monitor.blocked = True
    checks = monitor.checks
    reopened = Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True)
    try:
        reopened.running = False
        reopened.cycle()
        assert monitor.checks == checks
        assert len(list(reopened.bus.replay("channel-briefs"))) == 1
        assert next(row for row in reopened.commands.recent() if row["id"] == command)["status"] == "done"
    finally:
        reopened.close()


def test_storage_constructor_failure_releases_factory_lease(tmp_path, monkeypatch):
    def broken(cfg, db): raise RuntimeError("storage unavailable")
    monkeypatch.setattr("studio.factory.StorageMonitor", broken, raising=False)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        Factory(tmp_path / "studio.sqlite", MediaConfig(output_dir=tmp_path / "out"), demo=True)
    assert not (tmp_path / "studio.sqlite.lock").exists()


def test_storage_blocked_cycles_reuse_monitor_scan(tmp_path, monkeypatch):
    import os

    from studio.storage_admission import StorageAdmissionError

    scans = []
    real_scandir = os.scandir

    def counted(path):
        scans.append(path)
        return real_scandir(path)

    monkeypatch.setattr("studio.storage_admission.os.scandir", counted)
    output = tmp_path / "out"
    output.mkdir()
    pressure = output / "test-pressure.bin"
    pressure.write_bytes(b"x" * (2 * 1024 * 1024))
    approved = output / "approved-video.mp4"
    approved.write_bytes(b"approved asset must survive")
    cfg = MediaConfig(output_dir=output, production={"storage": {
        "hard_limit_bytes": 1024 * 1024, "scan_interval_s": 3600}})
    factory = Factory(tmp_path / "studio.sqlite", cfg, demo=True)
    try:
        factory.running = False
        assert factory.snapshot()["storage"]["status"] == "blocked"
        factory.commands.add("accepted-before-limit", "create", {"title": "pending"})
        control = factory.command("settings", {"threads": 4})
        initial_scans = len(scans)
        for _ in range(5): factory.cycle()
        assert len(scans) == initial_scans
        assert factory.commands.pending()[0]["status"] == "pending"
        with pytest.raises(StorageAdmissionError): factory.command("create", {"title": "new"})
        assert len(scans) > initial_scans  # an explicit new admission still measures afresh
        assert next(row for row in factory.commands.recent() if row["id"] == control)["status"] == "done"
        assert factory.snapshot()["threads"] == 4
        pressure.unlink()  # test-owned pressure only; factory itself must never delete assets
        factory.storage._scanned_at -= 3601  # expire the configured observation interval
        factory.cycle()
        assert not factory.commands.pending()
        assert len(list(factory.bus.replay("channel-briefs"))) == 1
        assert factory.snapshot()["storage"]["status"] == "normal"
        assert approved.read_bytes() == b"approved asset must survive"
    finally:
        factory.close()


@pytest.mark.parametrize("stage", ["bus", "commands", "client", "autopilot", "settings", "refresh"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_factory_startup_failure_closes_bus_and_releases_lease(tmp_path, monkeypatch, stage, error_type):
    import sqlite3

    from studio import factory as module

    db = tmp_path / "startup.sqlite"
    cfg = MediaConfig(output_dir=tmp_path / "out")
    if stage == "settings":
        saved = CommandStore(db); saved.add("saved", "settings", {"threads": 2}); saved.complete("saved")
    buses = []
    real_bus = module.SQLiteBus

    def open_bus(*args, **kwargs):
        bus = real_bus(*args, **kwargs); buses.append(bus); return bus

    def fail(*args, **kwargs):
        raise error_type(f"injected {stage}")

    with monkeypatch.context() as patch:
        patch.setattr(module, "SQLiteBus", open_bus)
        if stage == "bus": patch.setattr(module, "SQLiteBus", fail)
        elif stage == "commands": patch.setattr(module, "CommandStore", fail)
        elif stage == "client": patch.setattr(module, "make_client", fail)
        elif stage == "autopilot": patch.setattr(module, "Autopilot", fail)
        elif stage == "settings": patch.setattr(Factory, "apply_settings", fail)
        else: patch.setattr(Factory, "refresh", fail)
        with pytest.raises(error_type, match=f"injected {stage}"):
            Factory(db, cfg, demo=stage != "client")
        assert not db.with_name(db.name + ".lock").exists()
        for bus in buses:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                bus.poll()
    restarted = Factory(db, cfg, demo=True)
    restarted.close()


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_factory_close_failure_still_releases_lease(tmp_path, monkeypatch, error_type):
    db = tmp_path / "closing.sqlite"
    factory = Factory(db, MediaConfig(output_dir=tmp_path / "out"), demo=True)
    real_close = factory.bus.close

    def fail():
        real_close()
        raise error_type("injected close")

    monkeypatch.setattr(factory.bus, "close", fail)
    with pytest.raises(error_type, match="injected close"):
        factory.close()
    assert factory.stop.is_set()
    assert not db.with_name(db.name + ".lock").exists()
