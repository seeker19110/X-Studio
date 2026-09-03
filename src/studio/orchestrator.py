"""Orchestrator: vòng lặp tự động topic → agent → topic cho phòng ban video (ADR-0001, kế thừa ADR-0007 của
software-company).

Mỗi event trên bus được đối chiếu với bảng ROUTES (khớp front matter `reads`/`writes`): khớp thì gọi `AgentRunner`
rồi publish đầu ra. Những bước có tác dụng phụ là CODE, không phải model: renderer (TTS/ảnh/ghép video) chạy khi có
scene-manifests / cut-lists / thumbnail-specs; preflight chạy trên metadata-packages; desk gom review và quyết định
khi nào đủ điều kiện xin gate. Ba thứ không bao giờ tự đi tiếp (approval-first, ADR-0002):

- Human gate: kế hoạch biên tập chờ gate `plan`; gói nội dung chờ gate `publish` (KHÔNG có gì lên lịch trước khi
  duyệt); trả lời bình luận chờ gate `replies`; video blocked/escalate chờ gate `escalation`.
- Supervisor: video bị pause/budget_cut/escalate thì mọi event của video đó bị hoãn đến khi `resume`.
- Số liệu thật: `performance-snapshots`, `audience-comments`, `channel-briefs` do người/adapter publish (CLI).

Adapter nền tảng (ADR-0008): sau gate `publish` approve, publisher chỉ QUYẾT ĐỊNH (`publish-events` với scheduled_at,
evidence); CODE gọi `Platform.upload_video` + `set_thumbnail` + `schedule` rồi ghi đè `platform_ref`/`url`/`evidence`
bằng kết quả thật (model không tự khai id). Gate `replies` approve → code gọi `Platform.reply` cho từng draft đã duyệt.

Mọi event đã xử lý được đánh dấu bằng `audit-log` (actor=orchestrator, action=orchestrated) nên mở lại bus SQLite là
chạy tiếp đúng chỗ (resume sản xuất bị gián đoạn). Không retry lời gọi model: lỗi ghi audit rồi đi tiếp.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .analytics import judge_experiment, retention_drops
from .blackboard import Blackboard
from .bus import InMemoryBus
from .desk import ProductionDesk
from .events import (
    AuditLog,
    CutList,
    Envelope,
    MetadataPackage,
    PerformanceSnapshot,
    SceneManifest,
    ThumbnailSpec,
    can_transition,
)
from .gate_cli import PersistentGate, rollback_target
from .gates import GateRequest, gate_approvers
from .llm import LLMError, ModelClient
from .media import MediaError, MediaSuite
from .platform import Platform, PlatformError, UploadResult, make_platform
from .preflight import PreflightReport, preflight
from .registry import AgentSpec, load_agents
from .renderer import ACTOR as RENDERER
from .renderer import Renderer
from .runner import CONTEXT_ONLY, AgentRunner, RunnerError
from .supervisor import Supervisor
from .youtube import sync_comments, sync_metrics

ACTOR = "orchestrator"
# Topic con người / adapter được nạp tay qua CLI `publish` (README, architecture.md). audit-log (quyết định gate) và các
# topic do agent sinh KHÔNG nạp tay được — gate đi qua gate_cli, còn lại là việc của agent/code.
HUMAN_TOPICS = frozenset({"channel-briefs", "publish-events", "performance-snapshots", "audience-comments"})
SYNC_EVERY_ENV = "STUDIO_SYNC_EVERY"  # giây giữa hai lần kéo số liệu/bình luận cho video đã lên lịch/đăng; 0 = tắt
SYNC_STATES = frozenset({"scheduled", "published"})
PAUSING = frozenset({"pause", "budget_cut", "escalate"})
CONTROL_TOPICS = frozenset({"audit-log", "shared-context", "supervisor-actions"})
REVIEW_AGENT = {"fact": "fact-checker", "rights": "rights-checker", "quality": "quality-reviewer"}
CHANNEL_TOPICS = frozenset({"channel-briefs", "trend-reports"})
ACTIVE_STATES = frozenset({"briefed", "researched", "scripted", "in_production", "in_review", "changes_requested"})


def key_for(topic: str, payload: dict[str, Any], default: str) -> str:
    if topic in CHANNEL_TOPICS: return str(payload.get("channel_id") or default)
    if topic == "analytics-reports": return str(payload.get("video_id") or payload.get("channel_id") or default)
    return str(payload.get("video_id") or default)


When = Callable[[Envelope, "Orchestrator"], bool]
Enrich = Callable[[Envelope, "Orchestrator"], dict[str, Any]]


@dataclass(frozen=True)
class Route:
    topic_in: str
    agent: str
    topic_out: str  # topic, hoặc CONTEXT_ONLY = chỉ ghi blackboard
    when: When | None = None
    many: bool = False
    enrich: Enrich | None = None


def _from(*actors: str) -> When:
    return lambda e, _o: e.actor in actors


def _field(name: str, *values: Any) -> When:
    return lambda e, _o: e.payload.get(name) in values


def _retry(e: Envelope, _o: Orchestrator) -> bool:
    return int(e.payload.get("retry", 0)) > 0


def _fact_pass(e: Envelope, _o: Orchestrator) -> bool:
    return e.payload.get("source") == "fact" and e.payload.get("verdict") == "pass"


def _kind(k: str) -> When:
    return lambda e, _o: e.payload.get("kind") == k and e.actor == RENDERER


def _video_report(e: Envelope, _o: Orchestrator) -> bool:
    return bool(e.payload.get("video_id"))


def _latest_payload(o: Orchestrator, topic: str, key: str) -> dict[str, Any] | None:
    env = o.latest(topic, key); return env.payload if env else None


def _with_brief(e: Envelope, o: Orchestrator) -> dict[str, Any]:
    vid = e.payload.get("video_id") or e.key
    b = o.desk.briefs.get(vid)
    return {"brief": b.model_dump()} if b else {}


def _with_dossier(e: Envelope, o: Orchestrator) -> dict[str, Any]:
    vid = e.payload.get("video_id") or e.key
    d = _latest_payload(o, "research-dossiers", vid); s = _latest_payload(o, "scripts", vid)
    return {k: v for k, v in (("dossier", d), ("previous_script", s)) if v}


def _with_script(e: Envelope, o: Orchestrator) -> dict[str, Any]:
    vid = e.payload.get("video_id") or e.key
    out: dict[str, Any] = {}
    s = _latest_payload(o, "scripts", vid); b = o.desk.briefs.get(vid)
    if s: out["script"] = s
    if b: out["brief"] = b.model_dump()
    return out


def _assets_of(o: Orchestrator, vid: str, version: int | None = None) -> list[dict[str, Any]]:
    out = []
    for env in o.bus.replay(topic="media-assets", key=vid):
        p = env.payload
        if version is None or p.get("kind") == "thumbnail" or int(p.get("manifest_version", 0)) == version: out.append(p)
    return out


def _with_manifest_assets(e: Envelope, o: Orchestrator) -> dict[str, Any]:
    vid = e.payload["video_id"]; m = o.manifest(vid)
    if m is None: return {}
    return {"manifest": m.model_dump(), "scene_assets": _assets_of(o, vid, m.version),
            "repair_rounds_used": o.desk.repair_rounds[vid], "repair_rounds_max": 3}


def _with_package(e: Envelope, o: Orchestrator) -> dict[str, Any]:
    vid = e.payload["video_id"]; m = o.manifest(vid)
    pkg: dict[str, Any] = {"script": _latest_payload(o, "scripts", vid), "metadata": _latest_payload(o, "metadata-packages", vid),
                           "manifest": m.model_dump() if m else None,
                           "thumbnails": [a for a in _assets_of(o, vid) if a["kind"] == "thumbnail"],
                           "final_video": next((a for a in reversed(_assets_of(o, vid)) if a["kind"] == "final_video"), None),
                           "preflight": [f.model_dump() for f in o.preflights.get(vid, PreflightReport()).findings]}
    return {"package": {k: v for k, v in pkg.items() if v is not None}}


def _with_provenance(e: Envelope, o: Orchestrator) -> dict[str, Any]:
    vid = e.payload["video_id"]
    s = _latest_payload(o, "scripts", vid) or {}
    return {"assets": [{"kind": a["kind"], "scene_id": a.get("scene_id"), "path": a["path"], "provenance": a["provenance"]}
                       for a in _assets_of(o, vid)], "claims": s.get("claims", []), "brief": (o.desk.briefs[vid].model_dump() if vid in o.desk.briefs else {})}


def _with_retention(e: Envelope, o: Orchestrator) -> dict[str, Any]:
    snap = PerformanceSnapshot.model_validate(e.payload); m = o.manifest(snap.video_id)
    out: dict[str, Any] = {"retention_drops": [d.model_dump() for d in retention_drops(snap.retention_curve, m)]}
    if m: out["scenes"] = [{"scene_id": s.scene_id, "order": s.order, "duration_s": s.duration_s, "narration": s.narration[:120]} for s in m.scenes]
    if snap.variant_id:
        prev = next((PerformanceSnapshot.model_validate(x.payload) for x in reversed(list(o.bus.replay("performance-snapshots", snap.video_id)))
                     if x.payload.get("variant_id") and x.payload["variant_id"] != snap.variant_id), None)
        if prev:
            out["experiment"] = judge_experiment(f"EXP-{snap.video_id}-{snap.variant_id}", "thumbnail", prev, snap).model_dump()
    meta = _latest_payload(o, "metadata-packages", snap.video_id)
    if meta: out["metadata"] = {"title": meta.get("title"), "alt_titles": meta.get("alt_titles", [])}
    return out


def _with_calibration(e: Envelope, o: Orchestrator) -> dict[str, Any]:
    out: dict[str, Any] = {"calibration": o.supervisor.calibration(), "desk": o.desk.summary()}
    cb = _latest_payload(o, "channel-briefs", e.payload.get("channel_id") or e.key)
    if cb: out["channel_brief"] = cb
    return out


ROUTES: tuple[Route, ...] = (
    # chiến lược
    Route("channel-briefs", "trend-researcher", "trend-reports"),
    # từng video: nghiên cứu → kịch bản → kiểm chứng
    Route("video-briefs", "trend-researcher", "research-dossiers", _field("retry", 0)),
    Route("video-briefs", "script-writer", "scripts", _retry, enrich=_with_dossier),  # làm lại có hint, giữ hồ sơ cũ
    Route("research-dossiers", "script-writer", "scripts", enrich=_with_brief),
    Route("scripts", "fact-checker", "review-results"),
    # kịch bản qua kiểm chứng → sản xuất + SEO song song
    Route("review-results", "production-manager", "scene-manifests", _fact_pass, enrich=_with_script),
    Route("review-results", "seo-optimizer", "metadata-packages", _fact_pass, enrich=_with_script),
    Route("scene-manifests", "thumbnail-designer", "thumbnail-specs", _from("production-manager"), enrich=_with_script),
    # bản nháp → editor quyết định sửa cảnh / chốt; bản cuối → hai review độc lập
    Route("media-assets", "editor", "cut-lists", _kind("draft_video"), enrich=_with_manifest_assets),
    Route("media-assets", "quality-reviewer", "review-results", _kind("final_video"), enrich=_with_package),
    Route("media-assets", "rights-checker", "review-results", _kind("final_video"), enrich=_with_provenance),
    # sau đăng: số liệu thật → phân tích; insight theo video ghi vào chiến lược
    Route("performance-snapshots", "analytics-analyst", "analytics-reports", enrich=_with_retention),
    Route("analytics-reports", "channel-strategist", CONTEXT_ONLY, _video_report),
    Route("audience-comments", "community-manager", "reply-drafts", many=True),
)
SEO_RETRY_ROUTE = Route("metadata-packages", "seo-optimizer", "metadata-packages")  # preflight block → làm lại một lần
PUBLISH_ROUTE = Route("metadata-packages", "publisher", "publish-events", enrich=_with_package)  # chỉ sau gate publish
REPLY_ROUTE = Route("reply-drafts", "publisher", "publish-events")  # chỉ sau gate replies
PLAN_ROUTE = Route("trend-reports", "channel-strategist", "video-briefs", many=True, enrich=_with_calibration)
# Đầu vào khiến channel-strategist lập kế hoạch biên tập (nhiều brief một lượt) → gate `plan` → dispatch.
PLAN_INPUTS: dict[str, When] = {"trend-reports": lambda e, _o: True,
                                "analytics-reports": lambda e, _o: not e.payload.get("video_id")}


def check_routes(agents: dict[str, AgentSpec]) -> list[str]:
    bad = []
    for r in (*ROUTES, SEO_RETRY_ROUTE, PUBLISH_ROUTE, REPLY_ROUTE, PLAN_ROUTE):
        spec = agents[r.agent]
        if r.topic_in not in spec.reads and "*" not in spec.reads: bad.append(f"{r.agent} không đọc {r.topic_in}")
        if r.topic_out == CONTEXT_ONLY:
            if not spec.namespaces_write: bad.append(f"{r.agent} không có namespace để ghi blackboard")
        elif r.topic_out not in spec.writes: bad.append(f"{r.agent} không ghi {r.topic_out}")
    strat = agents["channel-strategist"]
    bad += [f"channel-strategist không đọc {t}" for t in PLAN_INPUTS if t not in strat.reads]
    return bad


@dataclass
class StepResult:
    event_id: str
    topic: str
    key: str
    actions: list[str] = field(default_factory=list)
    deferred: str | None = None


class Orchestrator:
    def __init__(self, bus: InMemoryBus, client: ModelClient, agents: dict[str, AgentSpec] | None = None,
                 max_retries: int = 3, media: MediaSuite | None = None, out_dir: Path | None = None,
                 platform: Platform | None = None, sync_every: int | None = None):
        self.bus = bus
        self.agents = agents or load_agents()
        bad = check_routes(self.agents)
        if bad: raise ValueError("ROUTES lệch front matter: " + "; ".join(bad))
        self.blackboard = Blackboard(bus)
        self.renderer = Renderer(bus, media, out_dir)
        self.gate = PersistentGate(bus, approvers=gate_approvers(self.renderer.media.cfg))
        self.desk = ProductionDesk(bus, max_retries=max_retries)
        self.supervisor = Supervisor(bus, max_retries=max_retries)
        self.runner = AgentRunner(bus, client, self.agents, self.blackboard)
        self.platform: Platform = platform or make_platform(self.renderer.media.cfg)
        self.sync_every = int(os.environ.get(SYNC_EVERY_ENV, "0") or 0) if sync_every is None else int(sync_every)
        self.last_sync: dict[str, datetime] = {}  # video_id → lần kéo gần nhất (chỉ trong bộ nhớ; mở lại là kéo ngay)
        self.triggers: dict[str, str] = {}       # video_id → người duyệt plan chứa video (ghi vào gate publish)
        self.processed: set[str] = set()
        self.paused: set[str] = set()
        self.plans: dict[str, dict[str, Any]] = {}
        self.reply_batches: dict[str, list[dict[str, Any]]] = {}
        self.preflights: dict[str, PreflightReport] = {}
        self.queue: list[Envelope] = []
        self.deferred: dict[str, tuple[Envelope, str]] = {}
        self.once: set[str] = set()
        self.stats: Counter[str] = Counter()
        self._rehydrate()
        bus.subscribe("*", self._on_event)

    # ---------- khôi phục từ log ----------

    def _rehydrate(self) -> None:
        approved: list[tuple[str, str]] = []  # (plan_id, event_id của gate.decide approve)
        for env in self.bus.replay():
            if env.topic == "audit-log":
                a = env.payload; d = _evidence(a)
                if a["actor"] == ACTOR and a["action"] == "orchestrated": self.processed.add(d["event_id"])
                elif a["actor"] == ACTOR and a["action"] == "once": self.once.add(d["key"])
                elif a["action"] == "plan.proposed": self.plans[d["plan_id"]] = d
                elif a["action"] == "replies.proposed": self.reply_batches[d["batch_id"]] = d["drafts"]
                elif a["action"] == "gate.decide" and d.get("decision") == "approve" and d["subject_id"] in self.plans:
                    approved.append((d["subject_id"], env.event_id)); self._remember_trigger(d["subject_id"], str(d.get("by")))
            elif env.topic == "supervisor-actions": self._track_pause(env)
            elif env.topic == "shared-context": self.blackboard._on(env)
            elif env.topic == "metadata-packages":
                self.desk.replay(env)
                self.preflights[env.payload["video_id"]] = preflight(MetadataPackage.model_validate(env.payload), self._format(env.payload["video_id"]))
            else: self.desk.replay(env)
            self.supervisor.replay(env)
        # Plan đã duyệt chỉ coi là "đã dispatch" khi thấy bằng chứng: brief của plan đã lên bus, hoặc event quyết định đã
        # được orchestrated. Nếu tiến trình chết giữa approve và dispatch, event gate.decide còn trong queue sẽ dispatch thật.
        briefed = {e.payload.get("video_id") for e in self.bus.replay("video-briefs")}
        for pid, eid in approved:
            plan = self.plans[pid]
            if eid in self.processed or any(b.get("video_id") in briefed for b in plan.get("briefs", [])):
                self._dispatch_plan(pid, replaying=True)
        self.queue = [e for e in self.bus.replay() if self._actionable(e) and e.event_id not in self.processed]

    def _actionable(self, env: Envelope) -> bool:
        if env.topic == "audit-log": return env.payload.get("action") == "gate.decide"
        return env.topic not in CONTROL_TOPICS

    def _track_pause(self, env: Envelope) -> None:
        act, target = env.payload["action"], env.payload["target"]
        if act in PAUSING:
            self.paused.add(target)
            if act == "escalate" and target in self.desk.state and self.desk.state[target] not in {"closed", "blocked"}:
                self.desk.state[target] = "escalated"  # supervisor escalate → chờ gate escalation, không tự đi tiếp
        elif act == "resume": self.paused.discard(target)

    def _on_event(self, env: Envelope) -> None:
        if env.topic == "supervisor-actions":
            self._track_pause(env)
            if env.payload["action"] == "resume": self._retry_deferred()
        elif self._actionable(env):
            self.queue.append(env)

    def latest(self, topic: str, key: str) -> Envelope | None:
        return next(reversed(list(self.bus.replay(topic=topic, key=key))), None)

    def manifest(self, vid: str) -> SceneManifest | None:
        e = self.latest("scene-manifests", vid)
        return SceneManifest.model_validate(e.payload) if e else None

    def _format(self, vid: str) -> str:
        b = self.desk.briefs.get(vid); return b.format if b else "long"

    # ---------- vòng lặp ----------

    def run(self, max_steps: int | None = None) -> list[StepResult]:
        out: list[StepResult] = []
        while self.queue and (max_steps is None or len(out) < max_steps):
            env = self.queue.pop(0)
            try:
                r = self.process(env)
            except Exception as e:  # một event hỏng (DeskError, dữ liệu lạ) không được làm chết vòng lặp
                r = StepResult(env.event_id, env.topic, env.key, actions=[f"!{type(e).__name__}"])
                self._audit("orchestrator_failed", {"event_id": env.event_id, "topic": env.topic, "error": f"{type(e).__name__}: {str(e)[:300]}"},
                            video_id=env.payload.get("video_id") if isinstance(env.payload, dict) else None)
                self._done(env, r)
            if r is not None: out.append(r)
            self._check_escalations()
        return out

    def tick(self, now: datetime | None = None) -> list[StepResult]:
        if hasattr(self.bus, "poll"): self.bus.poll()
        results = self.run()
        self._sync(now)
        remind, overdue = self.gate.due(now)
        for sid in [*remind, *overdue]:
            self._audit(f"gate.{'overdue' if sid in overdue else 'remind'}", {"subject_id": sid}, once=f"gate:{sid}")
        for vid, missing in self.desk.overdue_reviews(now).items():
            fin = next((e for e in reversed(list(self.bus.replay("media-assets", vid))) if e.payload.get("kind") == "final_video"), None)
            for src in sorted(missing):
                key = f"review:{vid}:{src}:{self.desk.review_since[vid].isoformat()}"
                if fin is None or key in self.once: continue
                self._remember(key); self._audit("review.reassign", {"video_id": vid, "source": src}, video_id=vid)
                res = StepResult(fin.event_id, fin.topic, fin.key)
                route = next(r for r in ROUTES if r.agent == REVIEW_AGENT[src] and r.topic_in == "media-assets")
                self._call(route, fin, res); results.append(res)
        active = {vid for vid, st in self.desk.state.items() if st in ACTIVE_STATES}
        self.supervisor.check_timeouts(now, active=active)
        results += self.run()
        return results

    def _sync(self, now: datetime | None = None) -> None:
        """Kéo số liệu + bình luận thật cho video đã lên lịch/đăng, mỗi `sync_every` giây một lần (0 = tắt).
        Event sinh ra (performance-snapshots, audience-comments) đi vào queue như khi nạp tay; lỗi adapter chỉ ghi audit."""
        if self.sync_every <= 0: return
        now = now or datetime.now(UTC)
        for vid, st in list(self.desk.state.items()):
            if st not in SYNC_STATES or vid in self.paused: continue
            last = self.last_sync.get(vid)
            if last is not None and (now - last).total_seconds() < self.sync_every: continue
            self.last_sync[vid] = now
            try:
                snap = sync_metrics(self.bus, self.platform, vid)
                cm = sync_comments(self.bus, self.platform, vid, since=last.isoformat() if last else None)
                self._audit("sync.tick", {"video_id": vid, "platform": self.platform.name, "snapshot_event": snap.event_id,
                                          "comments": len(cm.payload["comments"]) if cm else 0}, video_id=vid)
            except (PlatformError, OSError) as e:
                self._audit("sync.failed", {"video_id": vid, "error": str(e)[:300]}, video_id=vid)

    def watch(self, interval: float = 5.0, max_ticks: int | None = None) -> None:
        n = 0
        while max_ticks is None or n < max_ticks:
            self.tick(); n += 1; time.sleep(interval)

    def _retry_deferred(self) -> None:
        for eid, (env, _why) in list(self.deferred.items()):
            self.deferred.pop(eid); self.queue.append(env)

    # ---------- xử lý một event ----------

    def process(self, env: Envelope) -> StepResult | None:
        if env.event_id in self.processed: return None
        res = StepResult(env.event_id, env.topic, env.key)
        vid = env.payload.get("video_id") if isinstance(env.payload, dict) else None
        target = vid or env.key
        if env.topic == "audit-log":
            self._on_gate_decision(env, res); self._done(env, res); return res
        if target in self.paused and env.topic not in {"analytics-reports", "channel-briefs", "trend-reports"}:
            res.deferred = f"paused:{target}"; self.deferred[env.event_id] = (env, res.deferred); return res
        # --- bước code (không model) ---
        if env.topic == "scene-manifests" and env.actor != RENDERER:
            self._render(env, res)
        elif env.topic == "cut-lists":
            self._apply_cut(env, res)
        elif env.topic == "thumbnail-specs":
            self._thumbs(env, res)
        elif env.topic == "metadata-packages":
            self._preflight(env, res)
        elif env.topic == "review-results" and env.payload.get("verdict") != "pass":
            self._rework(env, res)
        elif env.topic == "publish-events" and env.payload.get("status") == "rolled_back":
            self._rework(env, res, hint=f"rolled back: {env.payload.get('evidence', '')}")
        # --- kế hoạch biên tập ---
        when = PLAN_INPUTS.get(env.topic)
        if when and when(env, self):
            self._plan(env, res)
        # --- route model ---
        for r in ROUTES:
            if r.topic_in != env.topic or (r.when and not r.when(env, self)): continue
            self._call(r, env, res)
        if env.topic == "reply-drafts" and env.actor == "community-manager":
            self._collect_replies(env, res)
        if vid: self._maybe_request_publish(vid, res)
        self._done(env, res)
        return res

    def _done(self, env: Envelope, res: StepResult) -> None:
        self.processed.add(env.event_id); self.stats[env.topic] += 1
        self._audit("orchestrated", {"event_id": env.event_id, "topic": env.topic, "actions": res.actions},
                    video_id=env.payload.get("video_id") if isinstance(env.payload, dict) else None)

    def _call(self, r: Route, env: Envelope, res: StepResult, extra: dict[str, Any] | None = None) -> list[Envelope]:
        data = dict(r.enrich(env, self)) if r.enrich else {}
        if extra: data.update(extra)
        try:
            if r.topic_out == CONTEXT_ONLY:
                self.runner.run_context(r.agent, env, extra=data or None); res.actions.append(f"{r.agent}→context"); return []
            g = self.runner.generate(r.agent, env, r.topic_out, many=r.many, extra=data or None)
            outs = []
            for i, p in enumerate(g.payloads):
                key = key_for(r.topic_out, p, env.key)
                # một lần gọi model → token chỉ tính ở payload đầu, kẻo supervisor cộng ngân sách gấp N lần
                outs.append(self.runner.publish(r.agent, env, r.topic_out, p, key=key, tokens=g.tokens if i == 0 else 0, model=g.model,
                                                context_writes=g.context_writes, cache_hit_ratio=g.cache_hit_ratio))
                g.context_writes = []
            res.actions.append(f"{r.agent}→{r.topic_out}×{len(outs)}")
            return outs
        except (RunnerError, LLMError) as e:
            res.actions.append(f"{r.agent}!{type(e).__name__}")
            self._audit("agent_failed", {"agent": r.agent, "error": str(e)[:300]}, video_id=env.payload.get("video_id"))
            return []

    # ---------- bước code ----------

    def _render(self, env: Envelope, res: StepResult) -> None:
        m = SceneManifest.model_validate(env.payload)
        try:
            assets = self.renderer.render(m); res.actions.append(f"render→{len(assets)} asset")
        except (MediaError, ValueError) as e:
            self._audit("render_failed", {"error": str(e)[:300]}, video_id=m.video_id); res.actions.append("render!failed")

    def _apply_cut(self, env: Envelope, res: StepResult) -> None:
        cut = CutList.model_validate(env.payload); m = self.manifest(cut.video_id)
        if m is None: return
        try:
            if cut.decision == "repair" and self.desk.repair_allowed(cut.video_id):
                self.renderer.apply_cutlist(m, cut); res.actions.append(f"repair→v{m.version + 1}")
            else:
                if cut.decision == "repair":
                    self._audit("repair.limit", {"video_id": cut.video_id, "rounds": self.desk.repair_rounds[cut.video_id]}, video_id=cut.video_id)
                self.renderer.finalize(m, cut.order or None); res.actions.append("finalize")
        except (MediaError, ValueError) as e:
            self._audit("render_failed", {"error": str(e)[:300]}, video_id=cut.video_id); res.actions.append("render!failed")

    def _thumbs(self, env: Envelope, res: StepResult) -> None:
        try:
            n = len(self.renderer.thumbnails(ThumbnailSpec.model_validate(env.payload))); res.actions.append(f"thumbnails→{n}")
        except MediaError as e:
            self._audit("render_failed", {"error": str(e)[:300]}, video_id=env.payload.get("video_id"))

    def _preflight(self, env: Envelope, res: StepResult) -> None:
        meta = MetadataPackage.model_validate(env.payload)
        rep = preflight(meta, self._format(meta.video_id)); self.preflights[meta.video_id] = rep
        self._audit("preflight", {"video_id": meta.video_id, "blocked": rep.blocked, "findings": [f.model_dump() for f in rep.findings]},
                    video_id=meta.video_id)
        res.actions.append(f"preflight:{'block' if rep.blocked else 'ok'}:{len(rep.findings)}")
        key = f"seo-retry:{meta.video_id}:{self.desk.briefs[meta.video_id].retry if meta.video_id in self.desk.briefs else 0}"
        if rep.blocked and key not in self.once:
            self._remember(key)
            self._call(SEO_RETRY_ROUTE, env, res, extra={"preflight_findings": [f.model_dump() for f in rep.findings],
                                                        "instruction": "sửa các finding mức block, giữ nguyên phần đã tốt"})

    def _rework(self, env: Envelope, res: StepResult, hint: str | None = None) -> None:
        vid = env.payload["video_id"]
        if vid not in self.desk.briefs: return
        if hint is None:
            f = env.payload.get("findings", [])
            hint = env.payload.get("root_cause") or "; ".join(x["text"] for x in f if x.get("level") == "block") or "; ".join(x["text"] for x in f)
            hint = f"{env.payload.get('source')}: {hint}"
        out = self.desk.rework(vid, hint, stage="script" if env.payload.get("source") == "fact" else "production")
        res.actions.append("rework" if out else "blocked")

    # ---------- kế hoạch biên tập → gate plan → dispatch ----------

    def _plan(self, env: Envelope, res: StepResult) -> None:
        cid = env.payload.get("channel_id") or env.key
        try:
            g = self.runner.generate("channel-strategist", env, "video-briefs", many=True, extra=_with_calibration(env, self))
        except (RunnerError, LLMError) as e:
            res.actions.append("plan!failed"); self._audit("agent_failed", {"agent": "channel-strategist", "error": str(e)[:300]}); return
        for b in g.payloads: b.setdefault("channel_id", cid)
        bad = self.desk.check_plan(g.payloads)
        self.runner.write_context("channel-strategist", env, g.context_writes)
        if bad:
            self._audit("plan.rejected", {"channel_id": cid, "errors": bad}, channel_id=cid); res.actions.append("plan!rejected"); return
        n = sum(1 for p in self.plans.values() if p.get("channel_id") == cid) + 1
        pid = f"PLAN-{cid}-{n}"
        self.plans[pid] = {"plan_id": pid, "channel_id": cid, "briefs": g.payloads, "source_event": env.event_id}
        self._audit("plan.proposed", self.plans[pid], channel_id=cid)
        self.gate.request(GateRequest(kind="plan", subject_id=pid, created_by="channel-strategist",
                                      checklist=[f"{b['video_id']}: {b['working_title']} ({b.get('format')}, est {b.get('estimate_tokens')} tok)" for b in g.payloads]))
        res.actions.append(f"plan→gate:{pid}")

    def _remember_trigger(self, pid: str, by: str) -> None:
        for b in self.plans.get(pid, {}).get("briefs", []):
            if b.get("video_id"): self.triggers[str(b["video_id"])] = by

    def _dispatch_plan(self, pid: str, replaying: bool = False) -> None:
        plan = self.plans.get(pid)
        if not plan or plan.get("dispatched"): return
        plan["dispatched"] = True
        if replaying: return
        self.desk.dispatch(plan["briefs"])

    # ---------- gate publish / replies ----------

    def _publish_checklist(self, vid: str) -> list[str]:
        """Người duyệt thấy ngay thứ sẽ lên nền tảng: file video cuối, thumbnail đã chọn, tiêu đề — cùng review + preflight."""
        rep = self.preflights.get(vid, PreflightReport())
        final = next((a for a in reversed(_assets_of(self, vid)) if a["kind"] == "final_video"), None)
        meta = _latest_payload(self, "metadata-packages", vid) or {}
        thumb = self._chosen_thumbnail(vid)
        facts = [f"final_video:{final['path']}" if final else "final_video:(chưa có)",
                 f"thumbnail:{thumb}" if thumb else "thumbnail:(chưa có)", f"title:{meta.get('title', '(chưa có)')}"]
        return [f"review:{s}:{r.verdict}" for s, r in sorted(self.desk.reviews[vid].items())] + rep.checklist() + facts

    def _maybe_request_publish(self, vid: str, res: StepResult) -> None:
        sid = f"PUB-{vid}"
        if not self.desk.ready_for_publish(vid) or sid in self.gate.pending: return
        key = f"publish:{vid}:{self.desk.briefs[vid].retry}"
        if key in self.once: return
        self._remember(key)
        self.gate.request(GateRequest(kind="publish", subject_id=sid, created_by="desk", checklist=self._publish_checklist(vid),
                                      triggered_by=self.triggers.get(vid)))
        res.actions.append(f"gate:publish:{sid}")

    def _collect_replies(self, env: Envelope, res: StepResult) -> None:
        vid = env.payload["video_id"]; bid = f"REP-{vid}-{sum(1 for b in self.reply_batches if b.startswith(f'REP-{vid}-')) + 1}"
        drafts = [e.payload for e in self.bus.replay("reply-drafts", vid) if e.event_id not in self.processed and e.actor == "community-manager"]
        drafts = [d for d in drafts if not any(d in b for b in self.reply_batches.values())]
        if not drafts: return
        self.reply_batches[bid] = drafts
        self._audit("replies.proposed", {"batch_id": bid, "video_id": vid, "drafts": drafts}, video_id=vid)
        src = self.latest("audience-comments", vid)  # người nạp lô bình luận (nếu là người) → triggered_by
        self.gate.request(GateRequest(kind="replies", subject_id=bid, created_by="community-manager",
                                      triggered_by=src.actor if src and src.actor.startswith("human") else None,
                                      checklist=[f"{d['comment_id']}{' [cần người]' if d.get('requires_human') else ''}: {d['reply']}" for d in drafts]))
        res.actions.append(f"gate:replies:{bid}")
        # các event reply-drafts cùng lô coi như đã xử lý
        for e in list(self.queue):
            if e.topic == "reply-drafts" and e.payload in drafts: self.queue.remove(e); self.processed.add(e.event_id)

    def _on_gate_decision(self, env: Envelope, res: StepResult) -> None:
        d = _evidence(env.payload); sid, dec, reason = d["subject_id"], d.get("decision"), d.get("reason", "")
        if sid in self.plans:
            if dec == "approve":
                self._remember_trigger(sid, str(d.get("by"))); self._dispatch_plan(sid); res.actions.append(f"dispatch:{sid}")
            return
        if sid.startswith("PUB-"):
            vid = sid[4:]
            if dec == "approve":
                st = self.desk.state.get(vid)
                if st != "approved":  # duyệt lại sau upload lỗi: đã approved thì giữ nguyên, không ép transition
                    if st is None or not can_transition(st, "approved"):  # vd. video đã bị escalate/closed sau khi xin gate
                        self._audit("gate.stale", {"subject_id": sid, "state": st, "decision": dec}, video_id=vid)
                        res.actions.append(f"stale:{sid}"); return
                    self.desk.mark_approved(vid)
                meta = self.latest("metadata-packages", vid)
                if meta: self._publish_video(meta, res, approved_by=str(d.get("by")), reason=reason)
            elif dec == "request_changes":
                out = self.desk.rework(vid, f"gate publish {dec}: {reason}", stage="gate"); res.actions.append("rework" if out else "blocked")
            elif dec == "hold":  # tạm giữ: không làm lại, không tăng retry — mở lại gate để người quyết định sau
                self.gate.request(GateRequest(kind="publish", subject_id=sid, created_by="desk", checklist=self._publish_checklist(vid)))
                self._audit("gate.hold", {"subject_id": sid, "reason": reason}, video_id=vid); res.actions.append(f"hold:{sid}")
            elif dec == "rollback":
                self._rollback(vid, res, reason=reason, by=str(d.get("by")))
            elif dec == "reject":
                self.desk.close(vid); res.actions.append("closed")
            return
        if sid in self.reply_batches:
            if dec == "approve":
                for dr in self.reply_batches[sid]:
                    if dr.get("requires_human"): continue
                    e = Envelope(topic="reply-drafts", key=dr["video_id"], actor="human", payload=dr)
                    self._post_reply(e, res, approved_by=str(d.get("by")))
            return
        if sid.startswith("ESC-"):
            vid = sid[4:]
            # event bị hoãn của video này (vd. review block cũ) đã lỗi thời sau quyết định gate: bỏ, không phát lại
            for eid, (env_d, _w) in list(self.deferred.items()):
                if (env_d.payload.get("video_id") or env_d.key) == vid: self.deferred.pop(eid); self.processed.add(eid)
            if dec == "approve": self.desk.reopen(vid, reason or "mở lại sau escalation"); self.paused.discard(vid); res.actions.append("reopen")
            elif dec == "reject": self.desk.close(vid); res.actions.append("closed")
            self.bus.publish(Envelope(topic="supervisor-actions", key=vid, actor="supervisor",
                                      payload={"target": vid, "action": "resume", "reason": f"gate escalation {dec}"}))

    def _rollback(self, vid: str, res: StepResult, reason: str, by: str) -> None:
        """Gate rollback trên video đã scheduled/published: phát `publish-events` rolled_back (desk làm lại có hint)."""
        prev = rollback_target(self.bus, vid)
        if prev is None:
            self._audit("rollback.nothing", {"video_id": vid, "reason": reason}, video_id=vid); res.actions.append("rollback!nothing"); return
        ev = {"video_id": vid, "kind": "video", "status": "rolled_back", "platform_ref": prev.get("platform_ref"), "url": prev.get("url"),
              "evidence": f"gate rollback by {by}: {reason}"[:1000]}
        self.bus.publish(Envelope(topic="publish-events", key=vid, actor=ACTOR, payload=ev))
        self._audit("platform.rollback", {"video_id": vid, "platform_ref": prev.get("platform_ref"), "by": by, "reason": reason}, video_id=vid)
        res.actions.append(f"rollback:{prev.get('platform_ref')}")

    # ---------- adapter nền tảng (ADR-0008): model quyết định, code hành động ----------

    def _decide(self, r: Route, env: Envelope, res: StepResult, extra: dict[str, Any]) -> tuple[dict[str, Any] | None, Any]:
        """Gọi model cho route publisher nhưng KHÔNG publish: trả (payload, Generated) để code điền kết quả thật."""
        data = dict(r.enrich(env, self)) if r.enrich else {}; data.update(extra)
        try:
            g = self.runner.generate(r.agent, env, r.topic_out, extra=data)
            return (g.payloads[0] if g.payloads else None), g
        except (RunnerError, LLMError) as e:
            res.actions.append(f"{r.agent}!{type(e).__name__}")
            self._audit("agent_failed", {"agent": r.agent, "error": str(e)[:300]}, video_id=env.payload.get("video_id"))
            return None, None

    def _emit(self, r: Route, env: Envelope, p: dict[str, Any], g: Any, res: StepResult) -> None:
        try:
            self.runner.publish(r.agent, env, r.topic_out, p, key=key_for(r.topic_out, p, env.key), tokens=g.tokens, model=g.model,
                                context_writes=g.context_writes, cache_hit_ratio=g.cache_hit_ratio)
            res.actions.append(f"{r.agent}→{r.topic_out}×1")
        except RunnerError as e:
            res.actions.append(f"{r.agent}!{type(e).__name__}")

    def _chosen_thumbnail(self, vid: str) -> str | None:
        spec = _latest_payload(self, "thumbnail-specs", vid) or {}
        thumbs = [a for a in _assets_of(self, vid) if a["kind"] == "thumbnail"]
        if not thumbs: return None
        pick = next((a for a in thumbs if spec.get("chosen") and a.get("variant_id") == spec["chosen"]), thumbs[0])
        return pick["path"]

    def _publish_video(self, meta_env: Envelope, res: StepResult, approved_by: str, reason: str) -> None:
        vid = meta_env.payload["video_id"]
        p, g = self._decide(PUBLISH_ROUTE, meta_env, res, {"approved_by": approved_by, "gate_reason": reason})
        if p is None: return
        p.setdefault("kind", "video")
        if p.get("status") not in {"scheduled", "published"}:  # model tự thấy không đủ điều kiện → không chạm adapter
            self._emit(PUBLISH_ROUTE, meta_env, p, g, res); return
        final = next((a for a in reversed(_assets_of(self, vid)) if a["kind"] == "final_video"), None)
        thumb = self._chosen_thumbnail(vid); meta = MetadataPackage.model_validate(meta_env.payload)
        note = str(p.get("evidence") or ""); prefix = (note + " | " if note else "") + "code: "
        # Bước 1 — upload, idempotent: audit `platform.upload` trước đó của video này = file đã ở trên nền tảng, dùng lại ref
        # (duyệt lại sau khi thumbnail/lịch lỗi không được upload lần hai).
        prior = self._prior_upload(vid)
        try:
            if prior is not None:
                up = UploadResult(prior["platform_ref"], prior.get("url") or "", "reused", evidence=f"dùng lại upload trước: {prior['platform_ref']}")
                steps: dict[str, Any] = {"platform": self.platform.name, "upload": up.evidence, "file": prior.get("file"), "checksum": prior.get("checksum")}
                self._audit("platform.upload_reused", {"video_id": vid, "platform_ref": up.platform_ref, "approved_by": approved_by}, video_id=vid)
            else:
                if final is None: raise PlatformError("không có final_video để upload")
                up = self.platform.upload_video(Path(final["path"]), meta, privacy="private", publish_at=None)
                steps = {"platform": self.platform.name, "upload": up.evidence, "file": final["path"], "checksum": final.get("checksum")}
                self._audit("platform.upload", {"video_id": vid, "platform_ref": up.platform_ref, "url": up.url, "approved_by": approved_by, **steps}, video_id=vid)
            res.actions.append(f"platform:upload:{up.platform_ref}")
        except (PlatformError, OSError) as e:
            p.update(status="failed", platform_ref=None, url=None, evidence=prefix + f"upload lỗi ({self.platform.name}): {str(e)[:300]}")
            self._audit("platform.upload_failed", {"video_id": vid, "error": str(e)[:300], "status": getattr(e, "status", None)}, video_id=vid)
            res.actions.append("platform:upload!failed"); self._emit(PUBLISH_ROUTE, meta_env, p, g, res); return
        # Bước 2/3 — thumbnail, lịch: lỗi ở đây KHÔNG làm mất platform_ref (event failed vẫn mang ref để người xử lý/duyệt lại)
        failed: str | None = None
        for step, fn in (("thumbnail", lambda: self.platform.set_thumbnail(up.platform_ref, Path(thumb)) if thumb else None),
                         ("schedule", lambda: self.platform.schedule(up.platform_ref, str(p["scheduled_at"])) if p.get("scheduled_at") else None)):
            try:
                out = fn()
                if out is not None: steps[step] = out
            except (PlatformError, OSError) as e:
                failed = f"{step} lỗi ({self.platform.name}): {str(e)[:300]}"; steps[step] = failed
                self._audit(f"platform.{step}_failed", {"video_id": vid, "platform_ref": up.platform_ref, "error": str(e)[:300],
                                                        "status": getattr(e, "status", None)}, video_id=vid)
                res.actions.append(f"platform:{step}!failed"); break
        if failed: status = "failed"
        elif p.get("scheduled_at"): status = "scheduled"
        else:  # không lên lịch: giữ trạng thái nền tảng báo (public → published), không tự khai "scheduled"
            status = {"public": "published", "scheduled": "scheduled"}.get(up.status, str(p["status"]))
        p.update(status=status, platform_ref=up.platform_ref, url=up.url or None, evidence=prefix + json.dumps(steps, ensure_ascii=False))
        self._emit(PUBLISH_ROUTE, meta_env, p, g, res)

    def _prior_upload(self, vid: str) -> dict[str, Any] | None:
        """Audit `platform.upload` (orchestrator) gần nhất của video — bằng chứng file đã lên nền tảng; rollback xoá hiệu lực."""
        found: dict[str, Any] | None = None
        for env in self.bus.replay("audit-log"):
            a = env.payload
            if a.get("actor") != ACTOR or a.get("video_id") != vid: continue
            if a.get("action") == "platform.upload": found = _evidence(a)
            elif a.get("action") == "platform.rollback": found = None  # đã rút lại → lần duyệt sau phải upload mới
        return found if found and found.get("platform_ref") else None

    def _post_reply(self, draft_env: Envelope, res: StepResult, approved_by: str) -> None:
        dr = draft_env.payload; vid = dr["video_id"]; cid = dr["comment_id"]
        p, g = self._decide(REPLY_ROUTE, draft_env, res, {"approved_by": approved_by})
        if p is None: return
        p.update(kind="reply", comment_id=cid)  # comment_id để `sync-comments` biết bình luận đã trả lời
        if p.get("status") not in {"scheduled", "published"}:
            self._emit(REPLY_ROUTE, draft_env, p, g, res); return
        try:
            r = self.platform.reply(cid, str(dr["reply"]))  # đăng đúng văn bản đã qua gate, không phải bản model viết lại
            p.update(status="published", platform_ref=r.platform_ref, url=None,
                     evidence=f"code: {self.platform.name} reply {cid} → {r.reply_id}; {r.evidence}"[:1000])
            self._audit("platform.reply", {"video_id": vid, "comment_id": cid, "reply_id": r.reply_id, "approved_by": approved_by}, video_id=vid)
            res.actions.append(f"platform:reply:{cid}")
        except PlatformError as e:
            p.update(status="failed", platform_ref=f"reply:{cid}", evidence=f"code: reply {cid} lỗi ({self.platform.name}): {str(e)[:300]}")
            self._audit("platform.reply_failed", {"video_id": vid, "comment_id": cid, "error": str(e)[:300]}, video_id=vid)
            res.actions.append(f"platform:reply!failed:{cid}")
        self._emit(REPLY_ROUTE, draft_env, p, g, res)

    def _check_escalations(self) -> None:
        for vid, st in list(self.desk.state.items()):
            if st != "blocked" and vid not in self.paused: continue
            sid = f"ESC-{vid}"
            if sid in self.gate.pending or f"esc:{vid}:{st}:{self.desk.briefs[vid].retry}" in self.once: continue
            self._remember(f"esc:{vid}:{st}:{self.desk.briefs[vid].retry}")
            self.gate.request(GateRequest(kind="escalation", subject_id=sid, created_by="supervisor",
                                          checklist=[f"state={st}", "root cause rõ", "hint đủ cụ thể", "ngân sách còn"]))

    # ---------- audit ----------

    def _remember(self, key: str) -> None:
        self.once.add(key); self._audit("once", {"key": key})

    def _audit(self, action: str, data: dict[str, Any], video_id: str | None = None, channel_id: str | None = None,
               once: str | None = None) -> None:
        if once:
            if once in self.once: return
            self._remember(once)
        a = AuditLog(actor=ACTOR, action=action, video_id=video_id, channel_id=channel_id,
                     evidence=json.dumps(data, ensure_ascii=False, default=str))
        self.bus.publish(Envelope(topic="audit-log", key=ACTOR, actor=ACTOR, payload=a.model_dump()))

    # ---------- báo cáo ----------

    def status(self) -> dict[str, Any]:
        return {"queue": len(self.queue), "deferred": {k: v[1] for k, v in self.deferred.items()},
                "videos": dict(self.desk.state), "gates": {sid: r.kind for sid, r in self.gate.pending.items()},
                "paused": sorted(self.paused), "blackboard": {ns: sc.content_ref for ns, sc in self.blackboard.snapshot().items()},
                "processed": len(self.processed), "media": self.renderer.media.names, "platform": self.platform.name}


def _evidence(a: dict[str, Any]) -> dict[str, Any]:
    try: return json.loads(a.get("evidence") or "{}")
    except json.JSONDecodeError: return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Orchestrator phòng ban video: run | publish | status | report")
    ap.add_argument("--db", type=Path, default=Path("studio.sqlite"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--watch", type=float, default=None); r.add_argument("--max-steps", type=int, default=None)
    p = sub.add_parser("publish"); p.add_argument("topic"); p.add_argument("json_file", type=Path); p.add_argument("--actor", default="human")
    p.add_argument("--key", default=None)
    sub.add_parser("status"); sub.add_parser("report")
    ns = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    from .sqlite_bus import SQLiteBus
    bus = SQLiteBus(ns.db)
    if ns.cmd == "publish":
        if ns.topic not in HUMAN_TOPICS:
            print(f"lỗi: `publish` chỉ nhận topic do người/adapter nạp: {', '.join(sorted(HUMAN_TOPICS))}. "
                  f"Topic `{ns.topic}` không nạp tay được" + (" — quyết định gate đi qua `studio.gate_cli`." if ns.topic == "audit-log" else "."),
                  file=sys.stderr)
            return 2
        payload = json.loads(ns.json_file.read_text(encoding="utf-8"))
        env = bus.publish(Envelope(topic=ns.topic, key=ns.key or key_for(ns.topic, payload, "-"), actor=ns.actor, payload=payload))
        print(f"published {env.topic} key={env.key} event={env.event_id}"); return 0
    from .llm import make_client
    orch = Orchestrator(bus, make_client())
    if ns.cmd == "status":
        print(json.dumps(orch.status(), ensure_ascii=False, indent=2)); return 0
    if ns.cmd == "report":
        print(json.dumps(orch.supervisor.report(), ensure_ascii=False, indent=2)); return 0
    if ns.watch: orch.watch(ns.watch)
    else:
        for s in orch.run(ns.max_steps):
            print(f"{s.topic:<22} {s.key:<12} {', '.join(s.actions) or s.deferred or '-'}")
        st = orch.status(); print(json.dumps({"videos": st["videos"], "gates": st["gates"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
