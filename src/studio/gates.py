"""Gate của phòng ban video = cơ chế chung ở `xagents_core.gates` + vốn từ của miền (K3.7).

Cơ chế (sổ `pending`, four-eyes, `due`/`overdue`, allowlist người duyệt) ở core; ở đây chỉ còn `GateKind`,
`Decision`, `triggered_by` và tên biến môi trường. `STUDIO_GATE_APPROVERS` giữ NGUYÊN hành vi cũ — nó chỉ đi
qua hàm `approvers()` tổng quát của core thay vì một bản đọc `os.environ` riêng.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from xagents_core.gates import GateRequest as CoreGateRequest
from xagents_core.gates import HumanGate as CoreHumanGate
from xagents_core.gates import approvers

APPROVERS_ENV = "STUDIO_GATE_APPROVERS"  # "human:owner,human:editor" — rỗng = ai cũng duyệt được (four-eyes vẫn áp)

# plan: kế hoạch biên tập; publish: gói nội dung trước khi lên lịch (approval-first); replies: trả lời bình luận;
# escalation: video bị block / supervisor escalate.
GateKind = Literal["plan", "publish", "replies", "escalation"]
Decision = Literal["approve", "request_changes", "reject", "hold", "rollback", "pending"]


@dataclass
class GateRequest(CoreGateRequest):
    triggered_by: str | None = None  # người (nếu biết) đã kích hoạt bước tạo ra gate này, vd. người duyệt plan/nạp bình luận


def gate_approvers(cfg: Any = None) -> frozenset[str]:
    """Danh sách người được duyệt: env STUDIO_GATE_APPROVERS thắng, sau đó media.yaml `gate.approvers`."""
    return approvers(APPROVERS_ENV, cfg)


class HumanGate(CoreHumanGate):
    """Không bao giờ tự đi tiếp. Separation of duties: decided_by != created_by; `approvers` (nếu đặt) giới hạn ai được duyệt."""

    APPROVERS_SOURCE = f"danh sách người duyệt ({APPROVERS_ENV} / media.yaml gate.approvers)"

    # Thu hẹp kiểu về `GateRequest` của studio (core khai lớp cơ sở): nơi gọi vẫn nhận `triggered_by`.
    pending: dict[str, GateRequest]  # type: ignore[assignment]
    history: list[GateRequest]  # type: ignore[assignment]
