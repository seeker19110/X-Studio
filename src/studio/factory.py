"""Single-worker video factory: durable commands, orchestration and UI snapshots."""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from xagents_core.sqlite_bus import Lease

from .autopilot import Autopilot
from .events import Envelope
from .fakes import make_scripted_client
from .llm import make_client
from .media import MediaConfig, make_media
from .orchestrator import Orchestrator
from .sqlite_bus import SQLiteBus


class ProjectRequest(BaseModel):
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


class CommandStore:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS factory_commands (id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, "
                        "status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '', created REAL DEFAULT (unixepoch()))")

    def connect(self):
        con = sqlite3.connect(self.path, timeout=30); con.row_factory = sqlite3.Row
        return con

    def add(self, id: str, kind: str, body: dict) -> None:
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO factory_commands(id,kind,body) VALUES (?,?,?)", (id, kind, json.dumps(body)))

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
        self.lease = Lease(db); self.lease.acquire()
        self.bus = SQLiteBus(db)
        self.commands = CommandStore(db)
        try:
            client = make_scripted_client(plan_size=1, repairs=0) if demo else make_client()
            self.orch = Orchestrator(self.bus, client, media=make_media(cfg))
        except Exception:
            self.bus.close(); self.lease.release(); raise
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

    def command(self, kind: str, body: dict) -> str:
        if kind not in {"create", "run", "pause", "gate", "settings"}: raise ValueError("unknown command")
        if kind == "create": ProjectRequest.model_validate(body)
        if kind == "settings": FactorySettings.model_validate(body)
        if kind == "gate" and body.get("decision") not in {"approve", "request_changes", "reject", "hold"}:
            raise ValueError("invalid gate decision")
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
        elif kind == "settings":
            self.apply_settings(body)
        elif kind == "create":
            request = ProjectRequest.model_validate(body)
            # Deterministic ID makes replay after a crash between publish and command completion idempotent.
            cid = "P" + re.sub(r"[^a-zA-Z0-9]", "", row["id"])[:24]
            if self.bus.latest("channel-briefs", cid) is None:
                payload = {"channel_id": cid, "goals": [request.title], "audience": request.audience,
                           "pillars": [request.title], "cadence": "Một video theo yêu cầu", "language": request.language,
                           "boundaries": request.boundaries, "requested_video": request.model_dump()}
                self.bus.publish(Envelope(topic="channel-briefs", key=cid, actor="human:owner", payload=payload))
        elif kind == "gate":
            sid = str(body["subject_id"])
            if sid in self.orch.gate.pending:
                if body.get("request_at") and body["request_at"] != self.orch.gate.pending[sid].created_at.isoformat():
                    raise ValueError("Quyết định đã thay đổi; tải lại để xem yêu cầu mới")
                self.orch.gate.decide(sid, body["decision"], by="human:editor", reason=str(body.get("reason", "Studio console")))

    def cycle(self) -> None:
        self.bus.poll()
        for command in self.commands.pending():
            try:
                self.execute(command)
                self.commands.complete(command["id"])
            except (ValueError, KeyError, PermissionError) as exc:
                self.commands.complete(command["id"], error=str(exc)[:300])
        if self.running:
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
        projects = []
        for vid, brief in o.desk.briefs.items():
            media = [{"id": e.event_id, "kind": e.payload["kind"], "scene_id": e.payload.get("scene_id"),
                      "url": "/media/" + e.event_id, "provider": e.payload["provider"],
                      "provenance": e.payload["provenance"], "duration_s": e.payload.get("duration_s")}
                     for e in assets if e.key == vid]
            manifest = o.manifest(vid)
            scenes = [{"id": s.scene_id, "narration": s.narration, "duration_s": s.duration_s, "locked": s.locked}
                      for s in manifest.scenes] if manifest else []
            reviews = {k: {"verdict": v.verdict, "findings": [f.model_dump() for f in v.findings]}
                       for k, v in o.desk.reviews[vid].items()}
            projects.append({"id": vid, "title": brief.working_title, "state": o.desk.state[vid],
                             "minutes": brief.target_minutes, "format": brief.format, "scenes": scenes,
                             "media": media, "reviews": reviews, "retry": brief.retry,
                             "qc": o.qcs[vid].as_dict() if vid in o.qcs else None})
        events = [e for e in self.bus.replay("audit-log") if e.payload.get("action") not in {"orchestrated", "once"}][-30:]
        snapshot = {"running": self.running, "demo": self.demo, "projects": projects,
                    "gates": [{"id": sid, "kind": g.kind, "checklist": g.checklist, "request_at": g.created_at.isoformat(),
                               "hold": self.autopilot.holds.get(sid, "")} for sid, g in o.gate.pending.items()],
                    "queue": len(o.queue), "deferred": len(o.deferred), "commands": self.commands.recent(),
                    "events": [{"action": e.payload["action"], "actor": e.actor, "video_id": e.payload.get("video_id"),
                                "time": e.ts.isoformat()} for e in reversed(events)],
                    "automation": o.renderer.media.cfg.production.get("automation", "manual"),
                    "providers": o.renderer.media.names, "platform": o.platform.name,
                    "render_mode": o.renderer.media.cfg.render.get("mode", "graph"),
                    "threads": o.renderer.media.cfg.render.get("threads", 2)}
        snapshot.update(burn_captions=o.renderer.media.cfg.video.get("burn_captions", False),
                        style=o.renderer.media.cfg.production.get("style", ""))
        with self._mutex: self._snapshot = snapshot

    def snapshot(self) -> dict:
        with self._mutex: return copy.deepcopy(self._snapshot)

    def media_path(self, id: str) -> Path | None:
        asset = next((e.payload for e in self.bus.replay("media-assets") if e.event_id == id), None)
        if asset is None: return None
        path = Path(asset["path"]).resolve()
        return path if path.is_relative_to(self.orch.renderer.out_dir.resolve()) and path.is_file() else None

    def close(self) -> None:
        self.stop.set(); self.bus.close(); self.lease.release()
