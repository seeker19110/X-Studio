"""ProductionDesk: phần XÁC ĐỊNH của vòng đời một video (tương ứng DeliveryLead ở software-company).

Theo dõi trạng thái từng video từ bus, gom review bắt buộc (fact + rights + quality), quyết định khi nào gói nội dung
đủ điều kiện xin gate `publish`, làm lại có hint khi review fail/block (retry ≤ max), đếm vòng sửa cảnh, block khi
vượt hạn mức. Không gọi model. Dựng lại từ replay (chế độ `replaying`: đổi state, không phát lại event).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from .bus import InMemoryBus
from .events import (
    BUDGET_FACTOR,
    MAX_REPAIR_ROUNDS,
    REQUIRED_REVIEWS,
    AuditLog,
    Envelope,
    ReviewResult,
    VideoBrief,
    can_transition,
)

ACTOR = "desk"
DONE_STATES = frozenset({"closed"})


class DeskError(Exception): ...


class ProductionDesk:
    def __init__(self, bus: InMemoryBus, max_retries: int = 3, review_timeout: timedelta = timedelta(hours=2)):
        self.bus, self.max_retries, self.review_timeout = bus, max_retries, review_timeout
        self.briefs: dict[str, VideoBrief] = {}
        self.state: dict[str, str] = {}
        self.reviews: dict[str, dict[str, ReviewResult]] = defaultdict(dict)
        self.repair_rounds: dict[str, int] = defaultdict(int)
        self.manifest_version: dict[str, int] = {}
        self.has: dict[str, set[str]] = defaultdict(set)  # video → {final_video, metadata, thumbnail, script}
        self.review_since: dict[str, datetime] = {}
        self.replaying = False
        bus.subscribe("*", self._on)

    # ---------- tiện ích ----------

    def _audit(self, action: str, video_id: str, evidence: str) -> None:
        if self.replaying: return
        a = AuditLog(actor=ACTOR, action=action, video_id=video_id, evidence=evidence)
        self.bus.publish(Envelope(topic="audit-log", key=ACTOR, actor=ACTOR, payload=a.model_dump()))

    def _set(self, vid: str, dst: str) -> None:
        src = self.state.get(vid)
        if src is not None and not can_transition(src, dst):
            raise DeskError(f"{vid}: {src} → {dst} không hợp lệ")
        self.state[vid] = dst

    def replay(self, env: Envelope) -> None:
        prev, self.replaying = self.replaying, True
        try: self._on(env)
        finally: self.replaying = prev

    # ---------- kiểm plan (trước gate) ----------

    def check_plan(self, briefs: list[dict]) -> list[str]:
        """Lỗi trong kế hoạch biên tập: thiếu estimate, budget < estimate × 1.5, video_id trùng, priority ngoài 1..5."""
        bad: list[str] = []; seen: set[str] = set()
        for raw in briefs:
            try: b = VideoBrief.model_validate(raw)
            except Exception as e: bad.append(f"brief không hợp lệ: {str(e)[:120]}"); continue
            if b.video_id in seen or b.video_id in self.briefs: bad.append(f"{b.video_id}: video_id trùng")
            seen.add(b.video_id)
            if not b.estimate_tokens: bad.append(f"{b.video_id}: thiếu estimate_tokens")
            elif b.budget_tokens < b.estimate_tokens * BUDGET_FACTOR: bad.append(f"{b.video_id}: budget < estimate × {BUDGET_FACTOR}")
            if not 1 <= b.priority <= 5: bad.append(f"{b.video_id}: priority ngoài 1..5")
        return bad

    def dispatch(self, briefs: list[dict], actor: str = "channel-strategist") -> list[Envelope]:
        out = []
        for raw in sorted(briefs, key=lambda r: (r.get("priority", 3), r.get("video_id", ""))):
            b = VideoBrief.model_validate(raw)  # chuẩn hoá: mọi trường mặc định (retry, hint, format...) có mặt trên bus
            out.append(self.bus.publish(Envelope(topic="video-briefs", key=b.video_id, actor=actor, payload=b.model_dump())))
        return out

    # ---------- theo dõi ----------

    def _on(self, env: Envelope) -> None:
        t, p = env.topic, env.payload
        vid = p.get("video_id")
        if t == "video-briefs":
            b = VideoBrief.model_validate(p); self.briefs[b.video_id] = b
            if b.retry == 0:
                self.state[b.video_id] = "briefed"; self.repair_rounds[b.video_id] = 0
            else:  # làm lại: mọi artifact cũ và review cũ đều vô hiệu
                self.state[b.video_id] = "changes_requested"
            self.reviews[b.video_id].clear(); self.has[b.video_id].clear()
        elif t == "research-dossiers" and vid in self.state and self.state[vid] == "briefed":
            self._set(vid, "researched")
        elif t == "scripts" and vid in self.state:
            if self.state[vid] in {"briefed", "researched", "changes_requested", "blocked", "escalated"}:
                self._set(vid, "scripted")
            self.reviews[vid].pop("fact", None); self.has[vid].add("script")
            self.review_since[vid] = env.ts
        elif t == "scene-manifests" and vid in self.state:
            self.manifest_version[vid] = int(p.get("version", 1))
            if self.state[vid] in {"scripted", "changes_requested"}: self._set(vid, "in_production")
        elif t == "cut-lists" and vid in self.state and p.get("decision") == "repair":
            self.repair_rounds[vid] += 1
        elif t == "media-assets" and vid in self.state:
            k = p.get("kind")
            if k == "final_video":
                self.has[vid].add("final_video"); self.reviews[vid].pop("rights", None); self.reviews[vid].pop("quality", None)
                if self.state[vid] in {"in_production", "changes_requested"}: self._set(vid, "in_review")
                self.review_since[vid] = env.ts
            elif k == "thumbnail": self.has[vid].add("thumbnail")
        elif t == "metadata-packages" and vid in self.state:
            self.has[vid].add("metadata")
        elif t == "review-results" and vid in self.state:
            self.reviews[vid][p["source"]] = ReviewResult.model_validate(p)
        elif t == "publish-events" and vid in self.state and p.get("kind", "video") == "video":
            st = p.get("status")
            if st == "scheduled" and self.state[vid] in {"approved", "in_review"}: self._set(vid, "scheduled")
            elif st == "published" and self.state[vid] in {"approved", "scheduled"}: self._set(vid, "published")
        elif t == "analytics-reports" and vid in self.state and self.state[vid] == "published":
            self._set(vid, "analyzed")

    # ---------- quyết định ----------

    def required_reviews(self, vid: str) -> set[str]:
        return set(REQUIRED_REVIEWS)

    def fact_passed(self, vid: str) -> bool:
        r = self.reviews[vid].get("fact"); return r is not None and r.verdict == "pass"

    def failing(self, vid: str) -> list[ReviewResult]:
        return [r for r in self.reviews[vid].values() if r.verdict != "pass"]

    def ready_for_publish(self, vid: str) -> bool:
        """Đủ điều kiện xin gate publish: video cuối + metadata + thumbnail + mọi review bắt buộc pass."""
        if self.state.get(vid) != "in_review": return False
        if not {"final_video", "metadata", "thumbnail"} <= self.has[vid]: return False
        got = {s for s, r in self.reviews[vid].items() if r.verdict == "pass"}
        return self.required_reviews(vid) <= got

    def repair_allowed(self, vid: str) -> bool:
        return self.repair_rounds[vid] <= MAX_REPAIR_ROUNDS

    def mark_approved(self, vid: str) -> None:
        self._set(vid, "approved")

    def rework(self, vid: str, hint: str, stage: str = "script") -> Envelope | None:
        """Review fail/block hoặc gate request_changes: phát lại brief với retry+1 và hint (script-writer làm lại);
        retry > max → blocked, để supervisor/gate escalation. Trả về envelope brief mới hoặc None nếu blocked."""
        b = self.briefs[vid]
        if b.retry + 1 > self.max_retries:
            self._set(vid, "blocked"); self._audit("video.blocked", vid, f"retry {b.retry + 1} > {self.max_retries}: {hint[:200]}")
            return None
        nb = b.model_copy(update={"retry": b.retry + 1, "hint": f"[{stage}] {hint}"[:1000]})
        self._audit("video.rework", vid, hint[:300])
        if self.replaying: return None
        return self.bus.publish(Envelope(topic="video-briefs", key=vid, actor=ACTOR, payload=nb.model_dump()))

    def reopen(self, vid: str, hint: str) -> Envelope | None:
        """Gate escalation approve: mở lại với hint, retry về 0 (đi lại từ nghiên cứu)."""
        b = self.briefs[vid].model_copy(update={"retry": 0, "hint": hint[:1000]})
        self._audit("video.reopen", vid, hint[:300])
        if self.replaying: return None
        return self.bus.publish(Envelope(topic="video-briefs", key=vid, actor=ACTOR, payload=b.model_dump()))

    def close(self, vid: str) -> None:
        self.state[vid] = "closed"; self._audit("video.closed", vid, "")

    def overdue_reviews(self, now: datetime | None = None) -> dict[str, set[str]]:
        now = now or datetime.now(UTC); out = {}
        for vid, since in self.review_since.items():
            if self.state.get(vid) != "in_review" or now - since <= self.review_timeout: continue
            missing = self.required_reviews(vid) - set(self.reviews[vid])
            if missing: out[vid] = missing
        return out

    def summary(self) -> dict[str, int]:
        c: dict[str, int] = defaultdict(int)
        for s in self.state.values(): c[s] += 1
        return dict(c)
