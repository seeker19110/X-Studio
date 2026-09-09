from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from xagents_core.supervisor import Budget as CoreBudget
from xagents_core.supervisor import SupervisorBase
from xagents_core.ticket_model import Budgeted

from .bus import InMemoryBus
from .events import NAMESPACE_OWNERS, AuditLog, Envelope, SupervisorAction, SupervisorActionKind, VideoBrief


@dataclass
class Budget(CoreBudget):
    """Sổ token của một video. Trần đo bằng TỔNG token (`used`), không phải token đầu ra như company: studio
    chưa tách `output_tokens` trong `audit-log`, và đổi mẫu số ở đây là đổi lúc nào video bị cắt — một thay
    đổi hành vi thật, không phải hợp nhất. Nên chỉ đúng một property được ghi đè."""

    @property
    def ratio(self) -> float: return self.used / self.limit if self.limit else 0.0


class Supervisor(SupervisorBase):
    """Watchdog + cost controller + knowledge base. Subscribe mọi topic. Ngân sách token theo video.

    Cơ chế chung (`_act_once`, `escalate_gate`, sổ nợ kiến trúc) ở `xagents_core.supervisor.SupervisorBase` từ
    K3.7; `report()` Ở LẠI đây vì nó nói về video, không phải về ticket."""

    escalate_once = False  # xem `escalate_gate`: chống lặp nằm ở khoá `once` BỀN của orchestrator, không ở RAM

    def __init__(self, bus: InMemoryBus, max_retries: int = 3, video_timeout: timedelta = timedelta(hours=6)):
        super().__init__()
        self.bus, self.max_retries, self.video_timeout = bus, max_retries, video_timeout
        self.budgets: dict[str, Budget] = {}
        self.last_seen: dict[str, datetime] = {}
        self.error_signatures: dict[str, list[str]] = defaultdict(list)
        self.actions: list[SupervisorAction] = []
        self.knowledge: list[dict] = []
        # `notified` (video → ngưỡng đã báo, mỗi ngưỡng đúng một lần) ở `SupervisorBase` cùng `_act_once`.
        self.replaying = False
        bus.subscribe("*", self._on)

    def _budget(self, item: Budgeted) -> Budget:
        """Sổ ngân sách của một đơn vị công việc có trần token. `Budgeted` là Protocol CẤU TRÚC
        (`xagents_core.ticket_model`): `VideoBrief` không kế thừa gì và cố ý KHÔNG có `project_id` như `Task`
        của company — thứ mã chung cần biết chỉ là "vật này có trần token"."""
        return Budget(item.budget_tokens)

    def _act(self, target: str, action: SupervisorActionKind, reason: str, evidence: str | None = None) -> None:
        a = SupervisorAction(target=target, action=action, reason=reason, evidence=evidence)
        self.actions.append(a)
        if not self.replaying:
            self.bus.publish(Envelope(topic="supervisor-actions", key=target, actor="supervisor", payload=a.model_dump()))

    def replay(self, env: Envelope) -> None:
        prev, self.replaying = self.replaying, True
        try: self._on(env)
        finally: self.replaying = prev

    def _on(self, env: Envelope) -> None:
        if env.actor == "supervisor":
            return
        self.last_seen[env.key] = env.ts
        if env.topic == "video-briefs":
            b = VideoBrief.model_validate(env.payload)
            self.budgets.setdefault(b.video_id, self._budget(b))
            if b.retry > self.max_retries:  # desk cho phép retry ≤ max; vượt mới là bất thường
                self._act(b.video_id, "escalate", f"retry {b.retry} > {self.max_retries}")
        elif env.topic == "audit-log":
            a = AuditLog.model_validate(env.payload)
            if a.video_id and a.video_id in self.budgets:
                bud = self.budgets[a.video_id]; bud.used += a.tokens
                if bud.ratio >= self.CUT_AT: self._act_once(a.video_id, "budget_cut", f"dùng {bud.used}/{bud.limit} token")
                elif bud.ratio >= self.WARN_AT: self._act_once(a.video_id, "warn", f"đã dùng {bud.ratio:.0%} ngân sách")
        elif env.topic == "review-results" and env.payload.get("verdict") in {"fail", "block"}:
            sig = env.payload.get("root_cause") or " | ".join(f["text"] for f in env.payload.get("findings", []))
            sigs = self.error_signatures[env.key]; sigs.append(sig)
            if sigs.count(sig) >= 2:
                self._act(env.key, "escalate", "cùng lỗi lặp ≥ 2 lần", evidence=sig)
        elif env.topic == "shared-context":
            if env.actor not in NAMESPACE_OWNERS.get(env.payload["namespace"], set()):
                self._act(env.actor, "pause", "ghi sai namespace")

    def escalate_gate(self, subject_id: str, reason: str, once_key: str | None = None) -> None:
        """Gate quá hạn: người duyệt im lặng cũng là một dạng bế tắc, phải hiện ra như mọi bế tắc khác.
        Chống lặp KHÔNG nằm ở đây mà ở khoá `once` bền của orchestrator (`escalate_once = False`):
        `Supervisor.actions` được dựng lại bằng replay nên một cờ RAM ở đây sẽ nói "đã escalate rồi" sai bét
        sau mỗi lần mở lại bus (khuôn 2)."""
        super().escalate_gate(subject_id, reason, once_key)

    def check_timeouts(self, now: datetime | None = None, active: set[str] | None = None) -> list[str]:
        now = now or datetime.now(UTC); stuck = []
        for key, ts in self.last_seen.items():
            if active is not None and key not in active: continue
            if now - ts > self.video_timeout:
                stuck.append(key); self.last_seen[key] = now
                self._act(key, "escalate", f"không hoạt động > {self.video_timeout}")
        return stuck

    def detect_injection(self, text: str) -> bool:
        needles = ("ignore previous instructions", "ignore all prior", "you are now", "system prompt:", "bỏ qua hướng dẫn trước")
        return any(n in text.lower() for n in needles)

    def record_lesson(self, context: str, problem: str, solution: str, evidence: str) -> None:
        self.knowledge.append({"context": context, "problem": problem, "solution": solution, "evidence": evidence})

    def lessons(self) -> list[dict]:
        out = []
        for env in self.bus.replay(topic="shared-context", key="knowledge"):
            if not str(env.payload.get("content_ref", "")).startswith("audit-log:lesson:"): continue
            try: d = json.loads(env.payload.get("summary") or "{}")
            except json.JSONDecodeError: continue
            if isinstance(d, dict) and d.get("video_id"): out.append(d)
        return out

    def calibration(self) -> dict[str, dict]:
        """median(actual/estimate) theo định dạng video (long/short) — channel-strategist nhận khi lập kế hoạch."""
        by: dict[str, list[float]] = defaultdict(list)
        for d in self.lessons():
            if d.get("ratio") and d.get("format"): by[d["format"]].append(float(d["ratio"]))
        return {k: {"ratio_median": round(statistics.median(v), 2), "samples": len(v)} for k, v in sorted(by.items())}

    def report(self) -> dict:
        videos: dict[str, dict[str, Any]] = {}
        for env in self.bus.replay(topic="video-briefs"):
            b = VideoBrief.model_validate(env.payload)
            videos[b.video_id] = {"estimate_tokens": b.estimate_tokens, "budget_tokens": b.budget_tokens, "retry": b.retry,
                                  "format": b.format, "actual_tokens": self.budgets[b.video_id].used if b.video_id in self.budgets else 0}
        for row in videos.values():
            est = row["estimate_tokens"]; row["ratio"] = round(row["actual_tokens"] / est, 2) if est else None
        actions: defaultdict[str, int] = defaultdict(int)
        for a in self.actions: actions[a.action] += 1
        reviews = [e.payload for e in self.bus.replay(topic="review-results")]
        caught = sum(1 for r in reviews if r.get("verdict") != "pass")
        published = sum(1 for e in self.bus.replay(topic="publish-events") if e.payload.get("status") == "published")
        return {"videos": videos, "actions": dict(actions), "lessons": len(self.knowledge),
                "rework_rate": round(sum(1 for r in videos.values() if r["retry"]) / len(videos), 2) if videos else None,
                "review_catch_rate": round(caught / len(reviews), 2) if reviews else None,
                "published": published, "calibration": self.calibration()}
