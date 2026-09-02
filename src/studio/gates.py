from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

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

class HumanGate:
    """Không bao giờ tự đi tiếp. Separation of duties: decided_by != created_by."""
    def __init__(self, timeout: timedelta = timedelta(hours=24), remind_at: timedelta = timedelta(hours=12)):
        self.timeout, self.remind_at = timeout, remind_at
        self.pending: dict[str, GateRequest] = {}
        self.history: list[GateRequest] = []

    def request(self, req: GateRequest) -> GateRequest:
        self.pending[req.subject_id] = req; return req

    def decide(self, subject_id: str, decision: Decision, by: str, reason: str = "") -> GateRequest:
        req = self.pending[subject_id]
        if req.created_by and req.created_by == by:
            raise PermissionError("người duyệt phải khác người tạo (four-eyes)")
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
