"""Human gate chung của hai công ty (K3.7 của ADR gốc 0001).

Cơ chế giống nhau ở cả hai xưởng: một sổ `pending` theo `subject_id`, một `history`, four-eyes
(`decided_by != created_by`), hạn chờ (`due`/`overdue`), và — từ K3.7 — một allowlist người duyệt tuỳ chọn.
Cái KHÁC nhau là **vốn từ của miền**: `GateKind` của company là `spec|release|escalation|acceptance`, của
studio là `plan|publish|replies|escalation`; studio còn mang `triggered_by`. Nên `kind`/`decision` ở đây là
`str` (lớp con thu hẹp thành Literal của mình, y như `Envelope.topic` ở K3.5a) và `GateRequest` là lớp cơ sở.

`approvers()` tham số hoá TÊN BIẾN MÔI TRƯỜNG chứ không viết cứng `STUDIO_GATE_APPROVERS`: core không được
biết tên công ty nào (ADR-0001 §2). Studio truyền `STUDIO_GATE_APPROVERS`, company truyền
`COMPANY_GATE_APPROVERS`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = ["GateRequest", "HumanGate", "approvers"]


@dataclass
class GateRequest:
    """Một gate đang chờ người. `kind` là `str` ở đây; lớp con của mỗi công ty thu hẹp thành Literal của mình."""

    kind: str
    subject_id: str
    checklist: list[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decision: str = "pending"
    reason: str = ""
    decided_by: str | None = None
    created_by: str | None = None
    #: Thế hệ của gate — số thứ tự tăng dần do `HumanGate.request()` gán, KHÔNG phải dữ liệu người gọi điền.
    #: Cùng một `subject_id` mở gate nhiều lần trong đời (duyệt → hỏng → mở lại), và `pending` khoá theo
    #: `subject_id` nên gate mới ghi đè gate cũ dưới đúng cái tên đó; `seq` là thứ phân biệt được hai thế hệ.
    #: Trước đây company phân biệt bằng `created_at.isoformat(microseconds)` — hỏng theo HAI đường:
    #:  * `datetime.now()` trên Windows có bước ~15,6 ms (đo 2026-09-09: `timedelta(0)` giữa hai lần gọi liên
    #:    tiếp), nên hai gate mở cách nhau <16 ms có CÙNG dấu thời gian → chung khoá `once` → lần quá hạn của
    #:    gate thứ hai bị nuốt, đúng thứ TRAPS §1 khuôn 3 mà khoá ấy sinh ra để chặn;
    #:  * lúc phát lại, gate được `request()` lại nên `created_at` là BÂY GIỜ, không phải mốc gốc — khoá đổi
    #:    sau mỗi lần restart, và một gate đã escalate rồi lại escalate lần nữa.
    #: Bộ đếm không mắc cả hai: nó không đọc đồng hồ, và phát lại cùng một log theo cùng thứ tự thì gate thứ
    #: ba vẫn là gate thứ ba.
    seq: int = 0


def approvers(env_var: str, cfg: Any = None, cfg_path: tuple[str, ...] = ("gate", "approvers")) -> frozenset[str]:
    """Danh sách người được duyệt: biến môi trường `env_var` thắng, sau đó cấu hình của công ty theo `cfg_path`.

    Biến ĐẶT NHƯNG RỖNG (`""`) khác với KHÔNG ĐẶT: rỗng nghĩa là "không giới hạn ai" (và cố ý bỏ qua cấu hình),
    không đặt mới rơi xuống cấu hình. Đó là hành vi sẵn có của studio, giữ nguyên."""
    raw = os.environ.get(env_var)
    if raw is not None:
        return frozenset(x.strip() for x in raw.split(",") if x.strip())
    node: Any = cfg
    for part in cfg_path[:-1]:
        node = getattr(node, part, None) or {}
    values = node.get(cfg_path[-1]) if isinstance(node, dict) else None
    return frozenset(str(x) for x in (values or []))


class HumanGate:
    """Không bao giờ tự đi tiếp. Separation of duties: `decided_by != created_by`; `approvers` (nếu đặt) giới
    hạn ai được duyệt. `approvers` rỗng = hành vi cũ của company (chỉ four-eyes)."""

    #: Thông điệp lỗi khi người duyệt không nằm trong allowlist — lớp con nói rõ nguồn danh sách của mình.
    APPROVERS_SOURCE = "danh sách người duyệt"

    def __init__(self, timeout: timedelta = timedelta(hours=24), remind_at: timedelta = timedelta(hours=12),
                 approvers: frozenset[str] | set[str] | None = None) -> None:
        self.timeout, self.remind_at = timeout, remind_at
        self.approvers = frozenset(approvers or ())
        self.pending: dict[str, GateRequest] = {}
        self.history: list[GateRequest] = []
        self._seq = 0

    def request(self, req: GateRequest) -> GateRequest:
        # `created_by` rỗng/None làm ngắn mạch kiểm four-eyes ở decide() (`if req.created_by and ...`),
        # cho phép người tạo tự duyệt gate của chính mình — chặn ngay tại nguồn, đừng để lộ ở decide().
        if not (req.created_by or "").strip():
            raise PermissionError("gate phải có created_by (actor thật) — four-eyes cần biết ai đã tạo")
        self._seq += 1; req.seq = self._seq
        self.pending[req.subject_id] = req; return req

    def decide(self, subject_id: str, decision: str, by: str, reason: str = "", *, enforce: bool = True) -> GateRequest:
        req = self.pending[subject_id]
        if req.created_by and req.created_by == by:
            raise PermissionError("người duyệt phải khác người tạo (four-eyes)")
        if enforce and self.approvers and by not in self.approvers:  # replay lịch sử không kiểm lại (danh sách có thể đã đổi)
            raise PermissionError(f"{by} không nằm trong {self.APPROVERS_SOURCE}")
        req.decision, req.decided_by, req.reason = decision, by, reason
        self.history.append(self.pending.pop(subject_id)); return req

    def overdue(self, now: datetime | None = None) -> list[GateRequest]:
        """Gate quá hạn, để orchestrator escalate — quá hạn không bao giờ tự đi tiếp, nhưng cũng không im lặng."""
        return [r for r in self.pending.values() if (now or datetime.now(UTC)) - r.created_at > self.timeout]

    def due(self, now: datetime | None = None) -> tuple[list[str], list[str]]:
        now = now or datetime.now(UTC); remind: list[str] = []; overdue: list[str] = []
        for sid, r in self.pending.items():
            age = now - r.created_at
            if age > self.timeout: overdue.append(sid)
            elif age > self.remind_at: remind.append(sid)
        return remind, overdue

    def is_approved(self, subject_id: str) -> bool:
        return any(r.subject_id == subject_id and r.decision == "approve" for r in self.history)
