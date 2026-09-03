from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

APPROVERS_ENV = "STUDIO_GATE_APPROVERS"  # "human:owner,human:editor" — rỗng = ai cũng duyệt được (four-eyes vẫn áp)

# plan: kế hoạch biên tập; publish: gói nội dung trước khi lên lịch (approval-first); replies: trả lời bình luận;
# escalation: video bị block / supervisor escalate.
GateKind = Literal["plan", "publish", "replies", "escalation"]
Decision = Literal["approve", "request_changes", "reject", "hold", "rollback", "pending"]

@dataclass
class GateRequest:
    kind: GateKind
    subject_id: str
    checklist: list[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decision: Decision = "pending"
    reason: str = ""
    decided_by: str | None = None
    created_by: str | None = None
    triggered_by: str | None = None  # người (nếu biết) đã kích hoạt bước tạo ra gate này, vd. người duyệt plan/nạp bình luận


def gate_approvers(cfg: Any = None) -> frozenset[str]:
    """Danh sách người được duyệt: env STUDIO_GATE_APPROVERS thắng, sau đó media.yaml `gate.approvers`."""
    raw = os.environ.get(APPROVERS_ENV)
    if raw is not None: return frozenset(x.strip() for x in raw.split(",") if x.strip())
    gate = getattr(cfg, "gate", None) or {}
    return frozenset(str(x) for x in (gate.get("approvers") or []))

class HumanGate:
    """Không bao giờ tự đi tiếp. Separation of duties: decided_by != created_by; `approvers` (nếu đặt) giới hạn ai được duyệt."""
    def __init__(self, timeout: timedelta = timedelta(hours=24), remind_at: timedelta = timedelta(hours=12),
                 approvers: frozenset[str] | set[str] | None = None):
        self.timeout, self.remind_at = timeout, remind_at
        self.approvers = frozenset(approvers or ())
        self.pending: dict[str, GateRequest] = {}
        self.history: list[GateRequest] = []

    def request(self, req: GateRequest) -> GateRequest:
        self.pending[req.subject_id] = req; return req

    def decide(self, subject_id: str, decision: Decision, by: str, reason: str = "", enforce: bool = True) -> GateRequest:
        req = self.pending[subject_id]
        if req.created_by and req.created_by == by:
            raise PermissionError("người duyệt phải khác người tạo (four-eyes)")
        if enforce and self.approvers and by not in self.approvers:  # replay lịch sử không kiểm lại (danh sách có thể đã đổi)
            raise PermissionError(f"{by} không nằm trong danh sách người duyệt ({APPROVERS_ENV} / media.yaml gate.approvers)")
        req.decision, req.decided_by, req.reason = decision, by, reason
        self.history.append(self.pending.pop(subject_id)); return req

    def due(self, now: datetime | None = None) -> tuple[list[str], list[str]]:
        now = now or datetime.now(UTC); remind, overdue = [], []
        for sid, r in self.pending.items():
            age = now - r.created_at
            if age > self.timeout: overdue.append(sid)
            elif age > self.remind_at: remind.append(sid)
        return remind, overdue

    def is_approved(self, subject_id: str) -> bool:
        return any(r.subject_id == subject_id and r.decision == "approve" for r in self.history)
