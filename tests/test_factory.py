"""Factory commands are durable and media delivery stays inside the output root."""

import pytest

from studio.factory import CommandStore, Factory, ProjectRequest
from studio.media import MediaConfig


def test_project_requests_validate_duration_and_budget():
    from pydantic import ValidationError
    with pytest.raises(ValidationError): ProjectRequest(title="x", target_minutes=721)
    with pytest.raises(ValidationError): ProjectRequest(title="x", target_minutes=0)
    assert ProjectRequest(title="x", target_minutes=720).target_minutes == 720


def test_rights_dossier_reaches_current_video_with_full_review_inputs_and_survives_restart(tmp_path, monkeypatch):
    from studio.orchestrator import _with_package, _with_provenance
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    cfg=MediaConfig(output_dir=tmp_path/'out',production={'automation':'production'})
    db=tmp_path/'studio.sqlite'
    declaration={'project_name':'X Studio','scope':'private_local_validation','permission':'Operator permits naming the local project in this test'}
    f=Factory(db,cfg,demo=True)
    try:
        f.command('create',{'title':'Permission intake','target_minutes':.5,'rights_dossier':declaration})
        for _ in range(40):f.cycle()
        vid=f.snapshot()['projects'][0]['id']
        trigger=next(e for e in reversed(list(f.bus.replay('media-assets',vid))) if e.payload['kind']=='final_video')
        data=_with_provenance(trigger,f.orch)
        assert data['operator_rights_dossier']['video_id']==vid
        assert data['operator_rights_dossier']['declaration']==declaration
        assert data['operator_rights_dossier']['source_actor']=='human:owner'
        assert data['script']['sections'][0]['narration'] and data['manifest']['scenes'][0]['visual_prompt']
        assert data['thumbnail_spec']['variants'][0]['prompt']
        assert all(asset['checksum'] and asset['manifest_version'] >= 0 for asset in data['assets'])
        assert _with_package(trigger,f.orch)['package']['brief']['target_minutes']==.5
    finally:f.close()
    reopened=Factory(db,cfg,demo=True)
    try:
        assert _with_provenance(trigger,reopened.orch)['operator_rights_dossier']['declaration']==declaration
        reopened.command('create',{'title':'No declaration for another project','target_minutes':.5})
        for _ in range(40):reopened.cycle()
        other=next(p['id'] for p in reopened.snapshot()['projects'] if p['id']!=vid)
        final=next(e for e in reversed(list(reopened.bus.replay('media-assets',other))) if e.payload['kind']=='final_video')
        assert 'operator_rights_dossier' not in _with_provenance(final,reopened.orch)
    finally:reopened.close()


def test_factory_backup_quiesces_website_and_restores_offline_project(tmp_path, monkeypatch):
    import json

    from studio.backup import restore_backup
    from studio.media import load_media_config
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE', str(tmp_path / 'policy.key'))
    db = tmp_path / 'studio.sqlite'; cfg = MediaConfig(output_dir=tmp_path / 'out', production={'automation':'production','style':'Màu ấm'},
                                                     video={'provider':'fake','burn_captions':False},render={'threads':3})
    f = Factory(db, cfg, demo=True)
    try:
        assert f.doctor()['demo']
        with pytest.raises(ValueError): f.doctor('bad')
        f.ideas.import_items([{'id':'a', 'title':'Bền vững'}])
        f.command('create', {'title':'Khôi phục video', 'target_minutes':.2})
        for _ in range(40):
            f.cycle()
            if f.snapshot()['gates'] and any(g['kind']=='publish' for g in f.snapshot()['gates']): break
        assert f.snapshot()['projects']
        bundle = tmp_path / 'backup'
        f.command('backup', {'destination':str(bundle)}); f.cycle()
        assert bundle.is_dir() and f.snapshot()['last_backup'] == str(bundle)
        assert not f.websites.paused.is_set()
        f.command('backup', {'destination':str(bundle)}); f.cycle()
        assert f.commands.recent()[0]['status'] == 'error'
    finally: f.close()
    restored = tmp_path / 'restored'; restore_backup(bundle, restored)
    reopened = Factory(restored / 'studio.sqlite', load_media_config(restored / 'media.restore.yaml'), demo=True)
    try:
        assert not reopened.running and reopened.snapshot()['automation'] == 'manual'
        assert reopened.snapshot()['style']=='Màu ấm' and reopened.snapshot()['threads']==3 and not reopened.snapshot()['burn_captions']
        assert reopened.ideas.snapshot()['ideas'][0]['title'] == 'Bền vững'
        for env in reopened.bus.replay('media-assets'):
            assert reopened.media_path(env.event_id).is_relative_to(restored)
        assert reopened.snapshot()['projects'][0]['title'] == 'Khôi phục video'
        assert json.loads((bundle / 'manifest.json').read_text())['files']
    finally: reopened.close()


def test_overview_omits_large_artifacts_and_project_detail_is_independent(tmp_path,monkeypatch):
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path / 'policy.key'))
    factory = Factory(tmp_path / 'studio.sqlite',MediaConfig(output_dir=tmp_path / 'out',production={'automation':'production'}),demo=True)
    try:
        factory.command('create',{'title':'Chi tiết dự án','target_minutes':.2})
        for _ in range(40): factory.cycle()
        full = factory.snapshot()['projects'][0]
        overview = factory.snapshot(lean=True)['projects'][0]
        assert overview['summary'] and overview['scene_count'] == len(full['scenes'])
        assert 'script' not in overview and not overview['scenes']
        assert all(a['kind'] in {'thumbnail','final_video','captions'} for a in overview['media'])
        detail = factory.project_detail(full['id']); assert detail['script'] == full['script']
        detail['title'] = 'changed'; assert factory.project_detail(full['id'])['title'] != 'changed'
        assert factory.projects_page(1,0)['total'] == 1
        for limit,offset in [(0,0),(101,0),(1,-1)]:
            with pytest.raises(ValueError): factory.projects_page(limit,offset)
        with pytest.raises(ValueError): factory.project_detail('missing')
    finally: factory.close()


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


def test_overview_pages_details_resources_and_cleanup_preserve_project_data(tmp_path,monkeypatch):
    import json
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    cfg=MediaConfig(output_dir=tmp_path/'out',production={'automation':'production'})
    f=Factory(tmp_path/'db',cfg,demo=True)
    try:
        f.ideas.import_items([{'id':'idea','title':'Dự án từ SQLite'}])
        f.convert_idea({'id':'idea','revision':1,'data':{'title':'Dự án từ SQLite','target_minutes':.2}})
        with pytest.raises(ValueError):f.convert_idea({'id':'idea','revision':1,'data':{'title':'x','visual_mode':'generated_video'}})
        for _ in range(40):f.cycle()
        from studio.events import AuditLog, Envelope
        audit=AuditLog(actor='orchestrator',action='clip.derived',video_id='bad',evidence='bad json')
        f.bus.publish(Envelope(topic='audit-log',key='bad',actor='orchestrator',payload=audit.model_dump()));f.refresh()
        project=f.snapshot()['projects'][0];vid=project['id']
        page=f.projects_page(10,0)
        assert page['total']==1 and page['projects'][0]['summary'] and page['projects'][0]['scene_count']>0
        assert not page['projects'][0]['scenes'] and 'script' not in page['projects'][0]
        assert f.snapshot(lean=True)['projects'][0]['summary']
        detail=f.project_detail(vid);detail['scenes'].clear()
        assert f.project_detail(vid)['scenes']
        with pytest.raises(ValueError):f.project_detail('missing')
        with pytest.raises(ValueError):f.projects_page(101)
        f.apply_settings({'pricing':{'image_per_request':.2},'media_budget_usd':2})
        assert cfg.production['pricing']['image_per_request']==.2
        folder=cfg.output_dir/vid/'.segments';folder.mkdir(parents=True)
        segment=folder/('a'*64+'.mkv');segment.write_bytes(b'checkpoint')
        segment.with_suffix('.json').write_text(json.dumps({'provider':'ffmpeg','model':'bounded-scene'}))
        resources=f.resources(0);assert resources['cleanup']['bytes']==10
        body={'max_bytes':0,'fingerprint':resources['cleanup']['fingerprint']}
        f.command('cleanup',body);f.cycle()
        assert segment.exists() and any(c['kind']=='cleanup' and c['status']=='error' for c in f.commands.recent())
        f.command('pause',{});f.cycle();f.command('cleanup',body);f.cycle()
        assert not segment.exists()
    finally:f.close()


def test_inspection_and_library_media_paths_verify_owned_content(tmp_path,monkeypatch):
    from io import BytesIO

    from PIL import Image

    from studio.events import MediaAsset, Provenance, SceneManifest
    from studio.inspection import inspect_project
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE',str(tmp_path/'policy.key'))
    f=Factory(tmp_path/'db',MediaConfig(output_dir=tmp_path/'out'),demo=True)
    try:
        image=BytesIO();Image.new('RGB',(5,5),'red').save(image,format='PNG');data=image.getvalue()
        row=f.library.upload('photo.png',BytesIO(data),len(data))['assets'][0]
        assert f.media_path('library-'+row['id']).is_file()
        video=tmp_path/'final.mp4';video.write_bytes(b'fixture')
        final=MediaAsset(video_id='V1',kind='final_video',path=str(video),provider='ffmpeg',provenance=Provenance(generated_by='test'))
        report=inspect_project(SceneManifest(video_id='V1',scenes=[]),final,f.orch.renderer.media.cfg)
        f.orch.inspections['V1']=report
        id='inspection-'+report['fingerprint'][:32]
        assert f.media_path(id).is_file() and f.media_path('inspection-missing') is None
        f.media_path(id).write_bytes(b'changed')
        assert f.media_path(id) is None
    finally:f.close()
