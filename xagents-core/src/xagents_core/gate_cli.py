"""Gate bền vững + phép kiểm "quyết định này có đáng tin không" (K3.7 của ADR gốc 0001).

Hai công ty có hai bản `trusted_decision` gần như y hệt, và cả hai đều mang cùng MỘT bản vá bảo mật
(2026-09-09, `sc-security` phát hiện ở PR 4L-6): `audit-log` là topic MỞ — mọi actor ghi được — nên người
tiêu thụ KHÔNG được đọc `evidence.by` như thể nó là chữ ký. Thứ bus thật sự kiểm là `env.actor` (ACL producer
lúc publish). Vì vậy đây là ALLOWLIST mặc định TỪ CHỐI, không phải danh sách chặn. Một lỗ hổng đã vá hai lần ở
hai chỗ là lý do mạnh nhất để nó chỉ còn MỘT chỗ.

`PersistentGate` là cùng một cơ chế ở cả hai bên: replay `audit-log` khi mở, subscribe để nhận quyết định từ
tiến trình khác, và ghi lại mọi `request`/`decide`. Cái khác là LỚP dữ liệu của mỗi công ty (`Envelope`,
`AuditLog`, `GateRequest`), nên chúng là tham số của constructor chứ không viết cứng — nếu không, `replay()`
của company trả về envelope core và mọi kiểm tra `topic: Topic` biến mất (cùng lý do `bus.py` generic ở K3.5b).
"""
from __future__ import annotations

import json
from typing import Any, Generic, TypeVar

from .bus import InMemoryBus, is_human
from .events import AuditLog, Envelope
from .gates import GateRequest, HumanGate

__all__ = ["SYSTEM_GATE_ACTOR", "PersistentGate", "trusted_decision"]

# Actor hệ thống duy nhất được ghi `gate.decide` thay người: orchestrator đóng gate nghiệm thu (`UAT-*`) bằng
# chính chữ ký khách trong `acceptance-results` (`signed_by` là chữ tự do, không phải id actor).
SYSTEM_GATE_ACTOR = "orchestrator"

E = TypeVar("E", bound=Envelope)
A = TypeVar("A", bound=AuditLog)


def trusted_decision(env: Envelope, *, uat_prefix: str | None = "UAT-") -> dict[str, Any] | None:
    """Đọc một quyết định gate từ envelope `audit-log`; trả `None` nếu KHÔNG đáng tin.

    Chỉ tin hai trường hợp: (1) `env.actor` là NGƯỜI thật sự (`is_human`) và trùng `by`; (2) `env.actor` là
    actor hệ thống đóng gate nghiệm thu (`subject_id` bắt đầu bằng `uat_prefix`) — công ty không có khái niệm
    nghiệm thu truyền `uat_prefix=None` để tắt hẳn nhánh này. Actor không hình người (`"orchestrator"`,
    `"desk"`, chuỗi bất kỳ) mang `by="human:x"` giả KHÔNG qua được: trước bản vá 2026-09-09, phép kiểm cũ chỉ
    CHẶN khi actor hình người mà lệch `by`, tức mặc định TIN mọi actor khác."""
    if env.topic != "audit-log" or env.payload.get("action") != "gate.decide": return None
    try: d = json.loads(env.payload.get("evidence") or "{}")
    except (ValueError, TypeError): return None
    if not isinstance(d, dict): return None
    sid, by = d.get("subject_id"), d.get("by")
    if not isinstance(sid, str) or not sid: return None
    if not isinstance(by, str) or not by: return None
    if not isinstance(d.get("decision"), str): return None
    if is_human(env.actor) and env.actor == by: return d
    if uat_prefix is not None and env.actor == SYSTEM_GATE_ACTOR and sid.startswith(uat_prefix): return d
    return None


class PersistentGate(HumanGate, Generic[E, A]):
    """HumanGate + ghi mọi request/decision lên bus (`audit-log`) và dựng lại từ replay khi mở."""

    #: Tiền tố subject của gate nghiệm thu; `None` = công ty này không có khái niệm ấy (xem `trusted_decision`).
    UAT_PREFIX: str | None = "UAT-"

    #: Actor được phép TẠO gate. `None` = không giới hạn (hành vi trước ADR-0008). Người (`is_human`) luôn được
    #: phép và không cần có tên trong danh sách: gate CLI là đường của người, danh sách này nói về AGENT nào có
    #: quyền mở một gate — allowlist theo vai, vì `gate.request` là việc hợp lệ của agent (khác `gate.decide`,
    #: chỉ người). Lớp con của mỗi công ty đặt danh sách của mình; core không biết tên vai nào (ADR-0001 §2).
    REQUEST_ACTORS: frozenset[str] | None = None

    def __init__(self, bus: InMemoryBus[E], *, envelope_cls: type[E], audit_cls: type[A],
                 request_cls: type[GateRequest] = GateRequest, **kw: Any) -> None:
        super().__init__(**kw)
        self.bus = bus
        self.envelope_cls, self.audit_cls, self.request_cls = envelope_cls, audit_cls, request_cls
        for env in bus.replay(topic="audit-log"):
            self.apply(env)
        bus.subscribe("audit-log", self.apply)  # quyết định từ tiến trình khác (gate CLI) đến qua bus.poll()

    def _request_actor_allowed(self, actor: str) -> bool:
        """Actor này có quyền TẠO gate không. Người luôn có; agent phải nằm trong `REQUEST_ACTORS` (nếu đặt)."""
        return self.REQUEST_ACTORS is None or is_human(actor) or actor in self.REQUEST_ACTORS

    def _trusted(self, env: E) -> dict[str, Any] | None:
        """Điểm mở duy nhất của phép kiểm tin cậy: studio siết thêm `decision` phải nằm trong Literal của mình."""
        return trusted_decision(env, uat_prefix=self.UAT_PREFIX)

    def _request_kwargs(self, d: dict[str, Any]) -> dict[str, Any]:
        """Trường của `GateRequest` lấy từ evidence — lớp con thêm trường miền của mình (vd. `triggered_by`)."""
        return {"kind": d["kind"], "subject_id": d["subject_id"], "checklist": d.get("checklist", []),
                "created_by": d.get("created_by")}

    def _request_payload(self, req: GateRequest) -> dict[str, Any]:
        return {"kind": req.kind, "subject_id": req.subject_id, "checklist": req.checklist,
                "created_by": req.created_by}

    def apply(self, env: E) -> None:
        """Áp một bản ghi gate.request/gate.decide vào trạng thái; idempotent (bỏ qua nếu đã áp)."""
        if env.topic != "audit-log": return
        a = self.audit_cls.model_validate(env.payload)
        if a.action not in {"gate.request", "gate.decide"}:
            return
        # Bản ghi dị thường (evidence hỏng hoặc thiếu khoá) chỉ bị bỏ qua: một dòng log xấu
        # không được làm sập replay của cả gate — `gate_cli list` và console đều đi qua đây.
        try: d = json.loads(a.evidence or "{}")
        except (ValueError, TypeError): return
        if not isinstance(d, dict): return
        sid = d.get("subject_id")
        if not isinstance(sid, str) or not sid: return
        if a.action == "gate.request":
            if not isinstance(d.get("kind"), str): return
            if sid not in self.pending and not any(r.subject_id == sid and r.created_at == env.ts for r in self.history):
                # `created_by` LẤY TỪ `env.actor`, không phải từ evidence tự khai (ADR-0002): `audit-log` là
                # topic mở, nên evidence là lời khai của người ghi, còn `env.actor` là thứ bus thật sự kiểm —
                # cùng một bất biến `trusted_decision` áp cho `gate.decide`, nay áp nốt cho `gate.request`.
                # Người ghi bịa `created_by` của người khác thì four-eyes ở `decide()` bị vô hiệu.
                # Actor lạ KHÔNG mở được gate: bỏ qua như mọi bản ghi dị thường khác (im lặng, không ném —
                # replay của cả sổ gate không được sập vì một dòng log xấu). Chiều ghi thì ném ở `request()`.
                if not self._request_actor_allowed(env.actor): return
                kw = {**self._request_kwargs(d), "created_by": env.actor}
                super().request(self.request_cls(created_at=env.ts, **kw))
        elif sid in self.pending:
            # `_trusted` (không phải đọc `d` thô ở trên): actor không đáng tin thì KHÔNG được đóng gate, dù
            # `apply` chạy trong tiến trình nào (CLI, orchestrator, replay lúc mở bus) — bản ghi mạo danh trước
            # bản vá 2026-09-09 vẫn đóng được gate thật, `history` mang `decided_by` giả.
            if self._trusted(env) is None: return
            # `enforce=False`: replay lịch sử không kiểm lại allowlist (danh sách người duyệt có thể đã đổi).
            super().decide(sid, d["decision"], by=d["by"], reason=d.get("reason", ""), enforce=False)

    def _envelope(self, actor: str, action: str, data: dict[str, Any], *, by: str | None = None) -> E:
        a = self.audit_cls(actor=by or actor, action=action, evidence=json.dumps(data, ensure_ascii=False))
        return self.envelope_cls(topic="audit-log", key=actor, actor=actor, payload=a.model_dump())

    def _log(self, actor: str, action: str, data: dict[str, Any], *, by: str | None = None) -> None:
        self.bus.publish(self._envelope(actor, action, data, by=by))

    def request(self, req: GateRequest) -> GateRequest:
        # Chiều GHI ném lỗi thay vì im lặng: một vai không có quyền mở gate mà gọi `request()` là bug ở call
        # site, phải lộ ngay — nếu chỉ bỏ qua ở `apply()` thì gate sống trong RAM của tiến trình này và biến
        # mất khi tiến trình khác dựng lại từ replay (khuôn 2 `TRAPS.md`: state chỉ sống trong RAM).
        if not self._request_actor_allowed(req.created_by or ""):
            raise PermissionError(f"{req.created_by} không có quyền tạo gate (không phải người, không trong REQUEST_ACTORS)")
        r = super().request(req)
        env = self._envelope(req.created_by or "human", "gate.request", self._request_payload(req))
        # `created_at` phải là ts của CHÍNH envelope `gate.request`, không phải thời điểm dựng dataclass.
        # Tiến trình khác dựng lại gate từ replay bằng `created_at=env.ts` (xem `apply`), nên giữ mốc khởi tạo
        # ở đây là cùng một gate mang HAI mốc lệch nhau vài trăm micro giây tuỳ tiến trình nào đang đọc. Mọi
        # khoá `once` lấy `created_at` làm THẾ HỆ vì thế đổi sau mỗi lần mở lại bus: nhắc lại, escalate lại một
        # gate đã nhắc rồi (TRAPS §1 khuôn 2 + khuôn 3). Gán TRƯỚC `publish`: `publish` gọi subscriber đồng bộ.
        r.created_at = env.ts
        self.bus.publish(env)
        return r

    def decide(self, subject_id: str, decision: str, by: str, reason: str = "", actor: str | None = None,
               *, enforce: bool = True) -> GateRequest:
        """`actor` là actor của envelope ghi lên bus (mặc định = `by`). Orchestrator đóng gate nghiệm thu truyền
        `actor=SYSTEM_GATE_ACTOR` vì `by` là chữ ký khách, không phải id actor — xem `trusted_decision`."""
        r = super().decide(subject_id, decision, by=by, reason=reason, enforce=enforce)
        if actor is None:  # chữ ký khách trên gate nghiệm thu không phải actor người của bus → ghi dưới actor hệ thống
            uat = self.UAT_PREFIX is not None and subject_id.startswith(self.UAT_PREFIX)
            actor = by if is_human(by) or not uat else SYSTEM_GATE_ACTOR
        self._log(actor, "gate.decide", {"subject_id": subject_id, "decision": decision, "by": by, "reason": reason}, by=by)
        return r
