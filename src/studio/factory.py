"""Single-worker video factory: durable commands, orchestration and UI snapshots."""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from xagents_core.sqlite_bus import Lease

from . import media_cost
from .autopilot import Autopilot
from .events import Envelope, MetadataPackage, PerformanceSnapshot, PublishEvent, Script
from .fakes import make_scripted_client
from .film import CinemaSpec
from .llm import make_client
from .media import MediaConfig, make_media
from .orchestrator import Orchestrator
from .platform import PlatformError, YouTubePlatform
from .sqlite_bus import SQLiteBus
from .storage_admission import StorageAdmissionError, StorageMonitor
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


class FactorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automation: Literal["manual", "production", "publish"] = "production"
    threads: int = Field(default=2, ge=1, le=16)
    burn_captions: bool = True
    style: str = Field(default="", max_length=1000)


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "request_changes", "reject", "hold"]
    request_at: str | None = None
    reason: str = Field(default="Studio console", max_length=1000)


class CommandStore:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS factory_commands (id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, "
                        "status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '', created REAL DEFAULT (unixepoch()))")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30); con.row_factory = sqlite3.Row
        try:
            with con: yield con
        finally:
            con.close()

    def add(self, id: str, kind: str, body: dict) -> None:
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO factory_commands(id,kind,body) VALUES (?,?,?)", (id, kind, json.dumps(body)))

    def pending(self) -> list[dict]:
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM factory_commands WHERE status='pending' ORDER BY (kind='create'), rowid LIMIT 20")]

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
            self.websites = WebsiteJobs(db)
            cleanup.callback(self.websites.close)
            self.storage = StorageMonitor(cfg, db)
            client = make_scripted_client(plan_size=1, repairs=0) if demo else make_client()
            self.orch = Orchestrator(self.bus, client, media=make_media(cfg))
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
        if kind not in {"create", "run", "pause", "gate", "settings", "website", "website_action"}: raise ValueError("unknown command")
        if not isinstance(body, dict): raise ValueError("command data must be an object")
        if kind == "create":
            request = ProjectRequest.model_validate(body)
            if request.visual_mode == "generated_video" and self.orch.renderer.media.scene_video is None:
                raise ValueError("Chế độ clip AI cần cấu hình scene_video.provider trong media.yaml")
            self.storage.check_admission()
        if kind == "website":
            if self.demo: raise ValueError("Website → Video cần xưởng thật; chế độ demo không gọi model hoặc dịch vụ giọng đọc thật")
            WebsiteRequest.model_validate(body)
        if kind == "website_action": JobAction.model_validate(body)
        if kind == "settings": FactorySettings.model_validate(body)
        if kind == "gate": GateDecision.model_validate(body)
        id = uuid4().hex
        self.commands.add(id, kind, body)
        return id

    def apply_settings(self, body: dict) -> None:
        settings = FactorySettings.model_validate(body)
        cfg = self.orch.renderer.media.cfg
        cfg.production.update(automation=settings.automation, style=settings.style)
        cfg.video["burn_captions"] = settings.burn_captions
        cfg.render["threads"] = settings.threads
        self.orch.renderer.media = make_media(cfg)
        self.autopilot.recheck.clear()

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
        elif kind == "create":
            request = ProjectRequest.model_validate(body)
            # Deterministic ID makes replay after a crash between publish and command completion idempotent.
            cid = "P" + re.sub(r"[^a-zA-Z0-9]", "", row["id"])[:24]
            if self.bus.latest("channel-briefs", cid) is None:
                self.storage.check_admission(force=False)
                payload = {"channel_id": cid, "goals": [request.title], "audience": request.audience,
                           "pillars": [request.title], "cadence": "Một video theo yêu cầu", "language": request.language,
                           "boundaries": [*request.boundaries, *request.direction()], "requested_video": request.model_dump()}
                self.bus.publish(Envelope(topic="channel-briefs", key=cid, actor="human:owner", payload=payload))
        elif kind == "gate":
            sid = str(body["subject_id"])
            if sid not in self.orch.gate.pending: raise ValueError("gate không còn chờ duyệt; tải lại để xem yêu cầu mới")
            if body.get("request_at") and body["request_at"] != self.orch.gate.pending[sid].created_at.isoformat():
                raise ValueError("Quyết định đã thay đổi; tải lại để xem yêu cầu mới")
            self.orch.gate.decide(sid, body["decision"], by="human:editor", reason=str(body.get("reason", "Studio console")))

    def cycle(self) -> None:
        self.bus.poll()
        for command in self.commands.pending():
            try:
                self.execute(command)
                self.commands.complete(command["id"])
            except StorageAdmissionError:
                # Controls are ordered first. Leave this and later creates pending,
                # avoid rescanning for every blocked job, and keep current work moving.
                break
            except (ValueError, KeyError, PermissionError) as exc:
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
        assets = list(self.bus.replay("media-assets"))
        publications = {e.key: e for e in self.bus.replay("publish-events") if e.payload.get("kind", "video") == "video"}
        projects = []
        cfg = o.renderer.media.cfg
        spend = media_cost.totals(self.bus.replay("audit-log", media_cost.ACTOR))
        for vid, brief in o.desk.briefs.items():
            media = [{"id": e.event_id, "kind": e.payload["kind"], "scene_id": e.payload.get("scene_id"),
                      "url": "/media/" + e.event_id, "provider": e.payload["provider"],
                      "provenance": e.payload["provenance"], "duration_s": e.payload.get("duration_s"),
                      "manifest_version": e.payload.get("manifest_version")}
                     for e in assets if e.key == vid]
            manifest = o.manifest(vid)
            scenes = [{"id": s.scene_id, "narration": s.narration, "duration_s": s.duration_s, "locked": s.locked,
                       "visual_prompt": s.visual_prompt}
                      for s in manifest.scenes] if manifest else []
            reviews = {k: {"verdict": v.verdict, "findings": [f.model_dump() for f in v.findings]}
                       for k, v in o.desk.reviews[vid].items()}
            channel = self.bus.latest("channel-briefs", brief.channel_id)
            request = channel.payload.get("requested_video") if channel else None
            request = {k: request[k] for k in ProjectRequest.model_fields if k in request} if isinstance(request, dict) else None
            research_event = self.bus.latest("research-dossiers", vid)
            research: dict[str, Any] | None = None
            if research_event:
                payload = research_event.payload
                research = {k: payload[k] for k in ("video_id", "topic", "evidence", "gaps", "assumptions") if k in payload}
                for key, fields in (("sources", ("title", "url", "type", "accessed")),
                                    ("competitor_videos", ("title", "url", "views"))):
                    if key in payload:
                        research[key] = [{k: row[k] for k in fields if k in row} for row in payload[key]]
            script_event = self.bus.latest("scripts", vid)
            metadata_event = self.bus.latest("metadata-packages", vid)
            performance_event = self.bus.latest("performance-snapshots", vid)
            publication_event = publications.get(vid)
            projects.append({"id": vid, "title": brief.working_title, "state": o.desk.state[vid],
                             "minutes": brief.target_minutes, "format": brief.format, "scenes": scenes,
                             "media": media, "reviews": reviews, "retry": brief.retry,
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
        audit_events = list(self.bus.replay("audit-log"))
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
                    "storage": self.storage.snapshot(),
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
        snapshot["youtube_oauth_configured"] = False
        if isinstance(o.platform, YouTubePlatform):
            try:
                snapshot["youtube_oauth_configured"] = bool(o.platform.store.status().get("logged_in"))
            except (OSError, ValueError, TypeError, AttributeError, PlatformError):
                pass  # local MP4 production remains available when OAuth is absent or damaged
        with self._mutex: self._snapshot = snapshot

    def snapshot(self) -> dict:
        with self._mutex: result = copy.deepcopy(self._snapshot)
        result["website_jobs"] = self.websites.snapshot()
        result["website_output_dir"] = "C:\\Videos\\X-Studio"
        return result

    def media_path(self, id: str) -> Path | None:
        if id.startswith("website-"): return self.websites.media_path(id)
        asset = next((e.payload for e in self.bus.replay("media-assets") if e.event_id == id), None)
        if asset is None: return None
        path = Path(asset["path"]).resolve()
        return path if path.is_relative_to(self.orch.renderer.out_dir.resolve()) and path.is_file() else None

    def close(self) -> None:
        self.stop.set()
        try: self.websites.close()
        finally:
            try: self.bus.close()
            finally: self.lease.release()
