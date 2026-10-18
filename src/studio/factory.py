"""Single-worker video factory: durable commands, orchestration and UI snapshots."""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from xagents_core.sqlite_bus import Lease

from . import media_cost
from .autopilot import Autopilot
from .editing import MediaLibrary, SceneEdit, apply_edit, validate_edit
from .events import Envelope, MetadataPackage, PerformanceSnapshot, PublishEvent, SceneManifest, Script
from .fakes import make_scripted_client
from .film import CinemaSpec
from .llm import make_client
from .media import MediaConfig, make_media
from .orchestrator import Orchestrator
from .platform import PlatformError, YouTubePlatform
from .presentation import QUALITY_DIRECTION
from .render_cache import digest
from .resources import MediaAccounting, cleanup_segments, inventory, preview_segments
from .shorts import ShortsRequest, create_short, validate_short
from .sqlite_bus import SQLiteBus
from .storage import IdeaStore, migrate
from .website import JobAction, WebsiteJobs, WebsiteRequest


class ProjectRequest(CinemaSpec):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(default="Người muốn học hỏi", max_length=300)
    target_minutes: float = Field(default=8, gt=0, le=720)
    format: Literal["long", "short"] = "long"
    language: str = Field(default="vi", pattern=r"^[a-z]{2}$")
    boundaries: list[str] = Field(default_factory=list, max_length=20)
    budget_tokens: int = Field(default=150000, ge=1000, le=10000000)
    rights_dossier: dict[str, Any] = Field(default_factory=dict, max_length=40)


class FactorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automation: Literal["manual", "production", "publish"] = "production"
    threads: int = Field(default=2, ge=1, le=16)
    burn_captions: bool = True
    style: str = Field(default="", max_length=1000)
    media_budget_usd: float | None = Field(default=None,ge=0,allow_inf_nan=False)
    pricing: dict[Literal['tts_per_1000_chars','image_per_request','video_per_second'],Annotated[float,Field(ge=0,allow_inf_nan=False)]] = Field(default_factory=dict)


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_bytes: int = Field(ge=0,le=10*1024**4,strict=True)
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "request_changes", "reject", "hold"]
    request_at: str | None = None
    reason: str = Field(default="Studio console", max_length=1000)


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    destination: str = Field(min_length=1, max_length=1000)


class CommandStore:
    def __init__(self, path: Path):
        self.path = path
        migrate(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30); con.row_factory = sqlite3.Row
        try:
            with con: yield con
        finally:
            con.close()

    def add(self, id: str, kind: str, body: dict) -> None:
        with self.connect() as con:
            existing = con.execute('SELECT kind,body FROM factory_commands WHERE id=?',(id,)).fetchone()
            if existing and (existing[0] != kind or json.loads(existing[1]) != body): raise ValueError('Request ID already used')
            con.execute("INSERT OR IGNORE INTO factory_commands(id,kind,body) VALUES (?,?,?)", (id, kind, json.dumps(body)))

    def get(self, id: str) -> dict | None:
        with self.connect() as con:
            row = con.execute('SELECT * FROM factory_commands WHERE id=?',(id,)).fetchone()
        return dict(row) if row else None

    def pending(self) -> list[dict]:
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM factory_commands WHERE status='pending' ORDER BY rowid LIMIT 20")]

    def complete(self, id: str, error: str = "") -> None:
        with self.connect() as con:
            con.execute("UPDATE factory_commands SET status=?,error=? WHERE id=?", ("error" if error else "done", error, id))

    def recent(self) -> list[dict]:
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT id,kind,status,error FROM factory_commands ORDER BY rowid DESC LIMIT 20")]

    def last_done(self, kinds: tuple[str, ...]) -> dict | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM factory_commands WHERE status='done' AND kind IN (" +
                              ",".join("?" for _ in kinds) + ") ORDER BY rowid DESC LIMIT 1", kinds).fetchone()
            return dict(row) if row else None


class Factory:
    def __init__(self, db: Path, cfg: MediaConfig, *, demo: bool = False):
        db.parent.mkdir(parents=True, exist_ok=True)
        with ExitStack() as cleanup:
            self.lease = Lease(db); self.lease.acquire()
            cleanup.callback(self.lease.release)
            self.bus = SQLiteBus(db)
            cleanup.callback(self.bus.close)
            self.commands = CommandStore(db)
            self.ideas = IdeaStore(db)
            self.library = MediaLibrary(cfg)
            self._work_lock = self.library.lock
            self.websites = WebsiteJobs(db)
            cleanup.callback(self.websites.close)
            client = make_scripted_client(plan_size=1, repairs=0) if demo else make_client()
            self.orch = Orchestrator(self.bus, client, media=make_media(cfg))
            self.websites.accounting = MediaAccounting(self.bus,cfg)
            self.autopilot = Autopilot(self.orch)
            self.demo = demo
            last = self.commands.last_done(("run", "pause"))
            self.running = last is None or last["kind"] == "run"
            saved = self.commands.last_done(("settings",))
            if saved: self.apply_settings(json.loads(saved["body"]))
            self.stop = threading.Event()
            self._mutex = threading.Lock()
            self._snapshot: dict[str, Any] = {}
            self.refresh()
            cleanup.pop_all()

    def command(self, kind: str, body: dict) -> str:
        if kind not in {"create", "run", "pause", "gate", "settings", "website", "website_action", "backup", "scene", "cleanup", "shorts"}: raise ValueError("unknown command")
        if not isinstance(body, dict): raise ValueError("command data must be an object")
        if kind == "create":
            request = ProjectRequest.model_validate(body)
            if request.visual_mode == "generated_video" and self.orch.renderer.media.scene_video is None:
                raise ValueError("Chế độ clip AI cần cấu hình scene_video.provider trong media.yaml")
        if kind == "website":
            if self.demo: raise ValueError("Website → Video cần xưởng thật; chế độ demo không gọi model hoặc dịch vụ giọng đọc thật")
            WebsiteRequest.model_validate(body)
        if kind == "website_action": JobAction.model_validate(body)
        if kind == "settings": FactorySettings.model_validate(body)
        if kind == "gate": GateDecision.model_validate(body)
        if kind == 'backup': BackupRequest.model_validate(body)
        if kind == 'cleanup': CleanupRequest.model_validate(body)
        id = uuid4().hex
        if kind == 'scene':
            scene_request = SceneEdit.model_validate(body); id = 'scene-'+scene_request.request_id
            previous = self.commands.get(id)
            if previous:
                if json.loads(previous['body']) != body: raise ValueError('Request ID already used')
                return id
            validate_edit(self.orch,body)
        if kind == 'shorts':
            short_request = ShortsRequest.model_validate(body); id = 'shorts-'+short_request.request_id
            previous = self.commands.get(id)
            if previous:
                if json.loads(previous['body']) != body: raise ValueError('Request ID already used')
                return id
            validate_short(self.orch,body)
        self.commands.add(id, kind, body)
        return id

    def apply_settings(self, body: dict) -> None:
        settings = FactorySettings.model_validate(body)
        cfg = self.orch.renderer.media.cfg
        cfg.production.update(automation=settings.automation, style=settings.style)
        cfg.video["burn_captions"] = settings.burn_captions
        cfg.render["threads"] = settings.threads
        if 'media_budget_usd' in body: cfg.production['media_budget_usd'] = settings.media_budget_usd
        if 'pricing' in body: cfg.production['pricing'] = settings.pricing
        self.orch.renderer.media = make_media(cfg)
        self.autopilot.recheck.clear()

    def convert_idea(self, body: dict) -> dict:
        request = ProjectRequest.model_validate(body['data'])
        if request.visual_mode == 'generated_video' and self.orch.renderer.media.scene_video is None:
            raise ValueError('Chế độ clip AI cần cấu hình scene_video.provider trong media.yaml')
        return self.ideas.convert(body['id'], body['revision'], request.model_dump())

    def execute(self, row: dict) -> None:
        body = json.loads(row["body"]); kind = row["kind"]
        if kind in {"run", "pause"}:
            self.running = kind == "run"
        elif kind == "website":
            self.websites.enqueue(row["id"], body)
        elif kind == "website_action":
            self.websites.action(JobAction.model_validate(body))
        elif kind == "settings":
            self.apply_settings(body)
        elif kind == 'backup':
            from .backup import create_backup
            with self.websites.quiesce():
                create_backup(self.commands.path, self.orch.renderer.media.cfg, Path(body['destination']))
        elif kind == 'scene':
            apply_edit(self.orch,self.library,body,row['id'])
        elif kind == 'cleanup':
            if self.running: raise ValueError('Tạm dừng xưởng trước khi dọn cache')
            with self.websites.quiesce():
                cleanup_segments(self.orch.renderer.media.cfg,body['max_bytes'],body['fingerprint'])
        elif kind == 'shorts': create_short(self.orch,body,row['id'])
        elif kind == "create":
            request = ProjectRequest.model_validate(body)
            # Deterministic ID makes replay after a crash between publish and command completion idempotent.
            cid = "P" + re.sub(r"[^a-zA-Z0-9]", "", row["id"])[:24]
            if self.bus.latest("channel-briefs", cid) is None:
                payload = {"channel_id": cid, "goals": [request.title], "audience": request.audience,
                           "pillars": [request.title], "cadence": "Một video theo yêu cầu", "language": request.language,
                           "boundaries": [*request.boundaries, *request.direction(),
                                          *self.orch.renderer.media.cfg.production.get('quality_guidelines',QUALITY_DIRECTION)],
                           "requested_video": request.model_dump()}
                self.bus.publish(Envelope(topic="channel-briefs", key=cid, actor="human:owner", payload=payload))
        elif kind == "gate":
            sid = str(body["subject_id"])
            if sid not in self.orch.gate.pending: raise ValueError("gate không còn chờ duyệt; tải lại để xem yêu cầu mới")
            if body.get("request_at") and body["request_at"] != self.orch.gate.pending[sid].created_at.isoformat():
                raise ValueError("Quyết định đã thay đổi; tải lại để xem yêu cầu mới")
            self.orch.gate.decide(sid, body["decision"], by="human:editor", reason=str(body.get("reason", "Studio console")))

    def cycle(self) -> None:
        with self._work_lock: self._cycle()

    def _cycle(self) -> None:
        self.bus.poll()
        for command in self.commands.pending():
            if command['kind']=='shorts' and not self.running: continue
            try:
                self.execute(command)
                self.commands.complete(command["id"])
            except (ValueError, KeyError, OSError, sqlite3.DatabaseError) as exc:
                self.commands.complete(command["id"], error=str(exc)[:300])
        if self.running:
            self.websites.start()
            self.refresh()  # expose accepted commands before a potentially slow CLI/model stage
            self.orch.tick(max_steps=1)
            self.autopilot.advance()
        self.refresh()

    def work(self) -> None:
        while not self.stop.is_set():
            try: self.cycle()
            except Exception as exc:
                with self._mutex: self._snapshot["worker_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            self.stop.wait(.25)

    def refresh(self) -> None:
        o = self.orch
        events = list(self.bus.replay())
        latest = {(event.topic,event.key):event for event in events}
        assets_by_video: dict[str,list[Envelope]] = defaultdict(list)
        publications = {}; audit_events = []; origins = {}
        for event in events:
            if event.topic == "media-assets": assets_by_video[event.key].append(event)
            elif event.topic == "publish-events" and event.payload.get("kind","video") == "video": publications[event.key] = event
            elif event.topic == "audit-log":
                audit_events.append(event)
                if event.actor=='orchestrator' and event.payload.get('action')=='clip.derived':
                    try: origins[event.payload['video_id']] = json.loads(event.payload.get('evidence') or '{}')
                    except (ValueError,TypeError): pass
        projects = []
        cfg = o.renderer.media.cfg
        spend = media_cost.totals(self.bus.replay("audit-log", media_cost.ACTOR))
        for vid, brief in o.desk.briefs.items():
            media = [{"id": e.event_id, "kind": e.payload["kind"], "scene_id": e.payload.get("scene_id"),
                      "url": "/media/" + e.event_id, "provider": e.payload["provider"],
                      "provenance": e.payload["provenance"], "duration_s": e.payload.get("duration_s")}
                     | {'checksum':e.payload.get('checksum'),'manifest_version':e.payload.get('manifest_version')}
                     for e in assets_by_video.get(vid,[])]
            manifest_event = latest.get(("scene-manifests",vid))
            manifest = SceneManifest.model_validate(manifest_event.payload) if manifest_event else None
            scenes = [{"id": s.scene_id, "narration": s.narration, "duration_s": s.duration_s, "locked": s.locked,
                       "visual_prompt": s.visual_prompt}
                      for s in manifest.scenes] if manifest else []
            reviews = {k: {"verdict": v.verdict, "findings": [f.model_dump() for f in v.findings]}
                       for k, v in o.desk.reviews[vid].items()}
            channel = latest.get(("channel-briefs", brief.channel_id))
            request = channel.payload.get("requested_video") if channel else None
            request = {k: request[k] for k in ProjectRequest.model_fields if k in request} if isinstance(request, dict) else None
            if vid in origins and request:
                request.update(title=brief.working_title,format=brief.format,target_minutes=brief.target_minutes)
            research_event = latest.get(("research-dossiers", vid))
            research: dict[str, Any] | None = None
            if research_event:
                payload = research_event.payload
                research = {k: payload[k] for k in ("video_id", "topic", "evidence", "gaps", "assumptions") if k in payload}
                for key, fields in (("sources", ("title", "url", "type", "accessed")),
                                    ("competitor_videos", ("title", "url", "views"))):
                    if key in payload:
                        research[key] = [{k: row[k] for k in fields if k in row} for row in payload[key]]
            script_event = latest.get(("scripts", vid))
            metadata_event = latest.get(("metadata-packages", vid))
            performance_event = latest.get(("performance-snapshots", vid))
            publication_event = publications.get(vid)
            projects.append({"id": vid, "title": brief.working_title, "state": o.desk.state[vid],
                             "minutes": brief.target_minutes, "format": brief.format, "scenes": scenes,
                             'origin':origins.get(vid),
                             "media": media, "reviews": reviews, "retry": brief.retry,
                             'manifest_version':manifest.version if manifest else None,
                             'inspection':{**{k:v for k,v in o.inspections.get(vid,{}).items() if k not in {'references','contact_sheet'}},
                               'contact_url':'/media/inspection-'+o.inspections[vid]['fingerprint'][:32]} if o.inspections.get(vid,{}).get('available') and o.inspections[vid]['manifest_version']==(manifest.version if manifest else 0) else None,
                             "qc": o.qcs[vid].as_dict() if vid in o.qcs else None,
                             "media_cost": {"usd": spend.get(vid, 0.0), "budget_usd": media_cost.budget(cfg),
                                            "priced": media_cost.priced(cfg),
                                            "warning": media_cost.near_cap(spend.get(vid, 0.0), media_cost.budget(cfg))},
                             "brief": brief.model_dump(exclude={"hint"}), "request": request, "research": research,
                             "cinema": CinemaSpec.model_validate(brief.model_dump()).model_dump(),
                             "script": Script.model_validate(script_event.payload).model_dump() if script_event else None,
                             "metadata": MetadataPackage.model_validate(metadata_event.payload).model_dump() if metadata_event else None,
                             "publication": PublishEvent.model_validate(publication_event.payload).model_dump(exclude={"evidence"}) if publication_event else None,
                             "performance": PerformanceSnapshot.model_validate(performance_event.payload).model_dump() if performance_event else None})
        planning_errors: dict[str, str] = {}
        for event in audit_events:
            action = event.payload.get("action"); cid = event.payload.get("channel_id")
            if event.actor != "orchestrator" or not cid or action not in {"plan.rejected", "plan.proposed", "agent_failed"}: continue
            try: data = json.loads(event.payload.get("evidence") or "{}")
            except (ValueError, TypeError): continue
            if not isinstance(data, dict): continue
            if action == "plan.proposed": planning_errors.pop(cid, None)
            elif action == "plan.rejected" and isinstance(data.get("errors"), list):
                planning_errors[cid] = "; ".join(str(error) for error in data["errors"])
            elif data.get("agent") == "channel-strategist": planning_errors[cid] = str(data.get("error") or "")
        commands = self.commands.recent()
        project_channels = {brief.channel_id for brief in o.desk.briefs.values()}
        for command in commands:
            if command["kind"] != "create" or command["status"] != "done": continue
            cid = "P" + re.sub(r"[^a-zA-Z0-9]", "", command["id"])[:24]
            if cid not in project_channels and planning_errors.get(cid): command["planning_error"] = planning_errors[cid]
        events = [e for e in audit_events if e.payload.get("action") not in {"orchestrated", "once"}][-30:]
        snapshot = {"running": self.running, "demo": self.demo, "projects": projects,
                    "gates": [{"id": sid, "kind": g.kind, "checklist": g.checklist, "request_at": g.created_at.isoformat(),
                               "hold": self.autopilot.holds.get(sid, "")} for sid, g in o.gate.pending.items()],
                    "queue": len(o.queue), "deferred": len(o.deferred), "commands": commands,
                    "events": [{"action": e.payload["action"], "actor": e.actor, "video_id": e.payload.get("video_id"),
                                "time": e.ts.isoformat()} for e in reversed(events)],
                    "automation": o.renderer.media.cfg.production.get("automation", "manual"),
                    "providers": o.renderer.media.names, "platform": o.platform.name,
                    "render_mode": o.renderer.media.cfg.render.get("mode", "graph"),
                    "threads": o.renderer.media.cfg.render.get("threads", 2)}
        snapshot.update(burn_captions=o.renderer.media.cfg.video.get("burn_captions", False),
                        style=o.renderer.media.cfg.production.get("style", ""),
                        scene_video_provider=o.renderer.media.cfg.scene_video.get("provider", "none"),
                        scene_video_ready=o.renderer.media.scene_video is not None)
        snapshot['media_budget_usd'] = o.renderer.media.cfg.production.get('media_budget_usd')
        snapshot['pricing'] = o.renderer.media.cfg.production.get('pricing',{})
        snapshot["youtube_oauth_configured"] = False
        if isinstance(o.platform, YouTubePlatform):
            try:
                snapshot["youtube_oauth_configured"] = bool(o.platform.store.status().get("logged_in"))
            except (OSError, ValueError, TypeError, AttributeError, PlatformError):
                pass  # local MP4 production remains available when OAuth is absent or damaged
        with self._mutex: self._snapshot = snapshot

    def doctor(self, flow: str = 'render') -> dict:
        from .doctor import check_environment
        if flow not in {'render', 'website', 'film'}: raise ValueError('Unknown production flow')
        return check_environment(self.orch.renderer.media.cfg, flow=flow, demo=self.demo)

    @staticmethod
    def _overview(project: dict) -> dict:
        overview = {k:v for k,v in project.items() if k not in {'scenes','media','script','research','metadata'}}
        overview.update(summary=True,scene_count=len(project['scenes']),has_draft=any(a['kind']=='draft_video' for a in project['media']),scenes=[],
                        media=[a for a in project['media'] if a['kind'] in {'thumbnail','final_video','captions'}],
                        metadata={k:v for k,v in (project.get('metadata') or {}).items() if k in {'title','language'}})
        return overview

    def project_detail(self, id: str) -> dict:
        with self._mutex:
            project = next((p for p in self._snapshot['projects'] if p['id']==id),None)
            if project is None: raise ValueError('Không tìm thấy dự án')
            return copy.deepcopy(project)

    def projects_page(self, limit: int = 50, offset: int = 0) -> dict:
        if not 1 <= limit <= 100 or offset < 0: raise ValueError('Invalid project page')
        with self._mutex:
            projects = self._snapshot['projects']
            return {'projects':copy.deepcopy([self._overview(p) for p in projects[offset:offset+limit]]),
                    'total':len(projects),'limit':limit,'offset':offset}

    def snapshot(self, *, lean: bool = False) -> dict:
        with self._mutex:
            if lean:
                result = copy.deepcopy({k:v for k,v in self._snapshot.items() if k != 'projects'})
                result['projects'] = copy.deepcopy([self._overview(p) for p in self._snapshot['projects']])
            else: result = copy.deepcopy(self._snapshot)
        result["website_jobs"] = self.websites.snapshot()
        result["website_output_dir"] = "C:\\Videos\\X-Studio"
        backup = self.commands.last_done(('backup',))
        result['last_backup'] = json.loads(backup['body'])['destination'] if backup else None
        return result

    def media_path(self, id: str) -> Path | None:
        if id.startswith("website-"): return self.websites.media_path(id)
        if id.startswith('library-'): return self.library.path(id.removeprefix('library-'))
        if id.startswith('inspection-'):
            report = next((r for r in self.orch.inspections.values() if r.get('fingerprint','')[:32]==id.removeprefix('inspection-')),None)
            if not report: return None
            path = Path(report['contact_sheet'])
            return path if path.resolve().is_relative_to(self.orch.renderer.out_dir.resolve()) and path.is_file() and digest(path)==report['contact_sha256'] else None
        asset = next((e.payload for e in self.bus.replay("media-assets") if e.event_id == id), None)
        if asset is None: return None
        path = Path(asset["path"]).resolve()
        return path if path.is_relative_to(self.orch.renderer.out_dir.resolve()) and path.is_file() else None

    def resources(self, max_bytes: int = 0) -> dict:
        cfg = self.orch.renderer.media.cfg
        return {'inventory':inventory(cfg,self.websites.store.rows()),'ledger':MediaAccounting(self.bus,cfg).summary(),
                'cleanup':preview_segments(cfg,max_bytes),'can_cleanup':not self.running}

    def close(self) -> None:
        self.stop.set()
        try: self.websites.close()
        finally:
            try: self.bus.close()
            finally: self.lease.release()
