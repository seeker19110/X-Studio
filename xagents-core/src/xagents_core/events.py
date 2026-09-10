"""Khung event chung của hai công ty (K3.5a của ADR gốc 0001).

**Chỉ có KHUNG ở đây, không có model miền.** `events.py` của mỗi công ty phần lớn là nghĩa — `Task` vs
`VideoBrief`, `PullRequest` vs `SceneManifest`, `Topic`/`Namespace` Literal, `PAYLOAD_MODELS`, `TRANSITIONS`.
Đó là lý do `difflib` trên cả file chỉ ra **0.14**: hai file khác nhau vì chúng NÓI VỀ hai thứ khác nhau, và
gộp chúng là gộp hai miền nghiệp vụ. Thứ thật sự chung chỉ có năm: `Envelope`, `SharedContext`, `AuditLog`,
`SupervisorAction`, `can_transition`.

**Vì sao là LỚP CƠ SỞ chứ không phải lớp dùng thẳng** (tiền lệ: `LLMConfig` ở K3.3b). Đo từng symbol:
`Envelope` 0.43, `SharedContext` 0.45, `AuditLog` 0.38, `SupervisorAction` 0.62, `can_transition` 1.00. Chỗ
lệch không phải "một bên thiếu" mà là **trường phạm vi của từng miền**: `AuditLog` của company có
`ticket_id`/`project_id`, của studio có `video_id`/`channel_id`. Đưa cả bốn lên core là bắt company mang một
trường `video_id` nó không bao giờ ghi — đúng thứ nguyên tắc 1 cấm (core không biết tên công ty nào). Nên core
giữ phần chung, mỗi công ty **kế thừa** và thêm trường của mình.

Cùng lý do, `topic` và `namespace` ở đây là `str` chứ không phải Literal: `Topic` là danh sách topic CỦA MỘT
công ty. Lớp con thu hẹp lại thành Literal của mình, nên company vẫn đỏ khi ai đó publish một topic lạ — kiểm
tra không mất, nó chỉ chuyển xuống nơi biết đủ để làm việc ấy.

`child()` dùng `type(self)` chứ không phải `Envelope`: lớp con phải sinh ra lớp con. Viết cứng tên lớp ở đây là
mỗi chuỗi nhân quả trong company lại trả về một `Envelope` core không có `topic: Topic` — mất kiểm tra topic ở
đúng chỗ nó vừa được thêm vào.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

__all__ = [
    "ALWAYS_REACHABLE",
    "SCHEMA_VERSION",
    "AuditLog",
    "Envelope",
    "SharedContext",
    "SupervisorAction",
    "SupervisorActionKind",
    "can_transition",
]

SCHEMA_VERSION = 1  # tăng khi envelope hoặc payload của topic đổi không tương thích ngược


class Envelope(BaseModel):
    """Một event trên bus. Ba trường nhân quả (`schema_version`, `correlation_id`, `causation_id`) đến từ bản
    company; chúng có default nên **mọi envelope đã ghi đĩa trước K3.5a vẫn nạp được** — bus SQLite lưu envelope
    dưới dạng JSON và đọc lại bằng `model_validate_json`, nên thiếu trường là nhận default, không phải lỗi."""

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    topic: str          # lớp con thu hẹp thành `Topic` Literal của công ty mình
    key: str
    actor: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any]
    schema_version: int = SCHEMA_VERSION
    correlation_id: str | None = None  # event gốc của chuỗi nhân quả (mặc định = chính event_id khi không có cha)
    causation_id: str | None = None    # event trực tiếp sinh ra event này

    def model_post_init(self, _ctx: Any) -> None:
        if self.correlation_id is None:
            self.correlation_id = self.event_id

    def child(self, **kw: Any) -> Envelope:
        """Envelope mới trong cùng chuỗi nhân quả: kế thừa `correlation_id`, `causation_id` = event này."""
        return type(self)(correlation_id=self.correlation_id, causation_id=self.event_id, **kw)


class SharedContext(BaseModel):
    """Một mục trên blackboard.

    **K3.6b sửa lại một quyết định của K3.5a, có lý do.** K3.5a xếp `project_id` và `content` chung với
    `rulings` vào "thứ company thêm", vì lúc ấy nó chỉ nhìn *model* `SharedContext` và ba trường đều là trường
    company có mà studio không. K3.6b chuyển chính `blackboard.py` lên core, và nhìn từ đó thì hai trường ấy
    không phải trường của một miền: chúng LÀ hai cơ chế của blackboard — *phân vùng* (`project_id`) và *toàn
    văn thay vì con trỏ* (`content`, ADR-0012). Một blackboard chung không đọc được chúng thì phần lớn thân nó
    phải đi qua hook, tức là cơ chế bị xé ra làm hai chỗ.

    `rulings` (ADR-0030 của company) ở lại lớp con: đó mới thật là tên gọi của một miền.

    Studio nhận hai trường luôn `None`. Payload của studio vì thế mang thêm hai khoá null; schema
    `shared-context` của studio là `additionalProperties: true` ở tầng payload nên không có gì đỏ, và hai
    trường đã được khai thẳng vào schema ấy để nó nói đúng thứ đi qua nó."""

    namespace: str      # lớp con thu hẹp thành `Namespace` Literal của công ty mình
    version: int
    content_ref: str
    summary: str = ""
    project_id: str | None = None  # None = phạm vi toàn công ty (vd. `knowledge`); dự án khác nhau không ghi đè nhau
    content: str | None = None     # toàn văn artifact; bus là nguồn sự thật, artifact store chỉ mirror ra file


class AuditLog(BaseModel):
    """Sổ ghi việc đã làm. Trường PHẠM VI (`ticket_id`/`project_id` của company, `video_id`/`channel_id` của
    studio) ở lớp con: chúng là tên gọi của miền, không phải cơ chế."""

    actor: str
    action: str
    evidence: str | None = None
    tokens: int = 0


SupervisorActionKind = Literal["pause", "resume", "escalate", "budget_cut", "warn"]


class SupervisorAction(BaseModel):
    target: str
    action: SupervisorActionKind
    reason: str
    evidence: str | None = None


# Trạng thái tới được từ BẤT KỲ đâu. Cả hai công ty dùng đúng hai tên này (đo được: `can_transition` giống nhau
# 1.00), nhưng chúng vẫn là tham số chứ không viết cứng — một công ty thứ ba đặt tên khác thì đổi đối số, không
# phải sửa core (nguyên tắc 1: core không biết tên công ty nào, kể cả qua vốn từ của nó).
ALWAYS_REACHABLE = frozenset({"blocked", "escalated"})


def can_transition(src: str, dst: str, transitions: Mapping[str, set[str]],
                   always: frozenset[str] = ALWAYS_REACHABLE) -> bool:
    """Máy trạng thái có cho đi từ `src` sang `dst` không? Bảng `transitions` là của từng công ty (`CORE`).

    Cửa thoát `always` là CÓ CHỦ Ý và phải giữ: một ticket/video đang ở bất kỳ đâu cũng phải chặn hoặc escalate
    được. Bỏ nó đi thì một lượt hỏng ở giữa dây chuyền không còn đường nào dừng lại — đo được ngay khi làm K3.5a:
    quên vế này làm 12 ca của company đỏ với `không thể changes_requested → blocked`."""
    return dst in always or dst in transitions.get(src, set())
