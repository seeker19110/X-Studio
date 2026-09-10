"""Cơ chế chung của supervisor hai công ty (K3.7 của ADR gốc 0001).

**Chỉ CƠ CHẾ ở đây, không đổi tên method của ai.** `report()` của studio và `sprint_report()` của company
trông giống nhau nhưng nói về hai thứ khác nhau (video vs ticket, không có estimate/PR/nợ kiến trúc ở studio),
nên chúng Ở LẠI từng công ty. Thứ lên core là bốn cơ chế không mang vốn từ miền nào:

* `Budget` — sổ token/tiền của một đơn vị công việc. `ratio` so `output_used` với trần (đo được 2026-09-04:
  đếm cả token đầu vào thì mọi ticket dùng nhiều lượt tool đều bị cắt oan); studio đo bằng `used` nên nó ghi
  đè đúng một property, không phải chép lại cả dataclass.
* `_act_once` — mỗi (đích, hành động) chỉ phát một lần, để mọi audit-log sau ngưỡng không thành spam.
* `escalate_gate` — gate quá hạn cũng là bế tắc. Chống lặp là TUỲ CHỌN (`escalate_once`): company chống ở đây
  bằng khoá `once_key` do orchestrator đặt theo THẾ HỆ của gate, studio cố ý chống ở khoá `once` bền của
  orchestrator chứ không bằng cờ RAM (RAM nói "đã escalate rồi" sai bét sau mỗi lần mở lại bus — TRAPS khuôn 2).
* `_count_debt`/`debt_table` — đếm mã nợ kiến trúc treo theo (dự án, nguồn review) (ADR-0032 của company).
  Studio chưa có review-results mang mã nợ; cơ chế nằm sẵn ở đây, không ép ai gọi.

`_act` là điểm trừu tượng duy nhất: lớp con biết `SupervisorAction` của mình và actor tên gì.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .events import Envelope, SupervisorActionKind

__all__ = ["DEBT_HINT", "DEBT_RE", "Budget", "SupervisorBase", "debt_ids"]

# ADR-0032: mã "nợ kiến trúc treo" trong finding của review (threat-model, schema, infra, code review): `DEF-01`,
# `SD-3`, hoặc `debt: <mã>` viết tự do. Cùng một mã nhắc ≥ `debt_threshold` review LIÊN TIẾP của cùng nguồn
# trong cùng dự án là quyết định đang bị né qua từng ticket — không đợi người tình cờ đọc finding thứ n.
DEBT_RE = re.compile(r"\b((?:DEF|SD)-\d+)\b|\bdebt:\s*([A-Za-z0-9_.-]+)", re.IGNORECASE)
DEBT_HINT = "cần ticket ADR + người ký: quyết định kiến trúc này đang bị né qua từng ticket"


def debt_ids(review: dict[str, Any]) -> set[str]:
    """Mã nợ nhắc trong một review-results: mọi `findings[].text` + `root_cause`."""
    texts = [str(f.get("text") or "") for f in review.get("findings") or [] if isinstance(f, dict)]
    if review.get("root_cause"): texts.append(str(review["root_cause"]))
    out = set()
    for m in DEBT_RE.finditer("\n".join(texts)):
        out.add((m.group(1) or m.group(2)).upper())
    return out


@dataclass
class Budget:
    limit: int
    used: int = 0            # TỔNG token của agent làm ticket (input + output) — cho báo cáo và chi phí
    # Token ĐẦU RA, và đây mới là thứ so với `limit`. `used` phình theo số lượt tool (mỗi lượt gửi lại cả hội
    # thoại) nên nó không đo được khối lượng công việc — thứ mà delivery-lead ước lượng khi đặt `budget_tokens`.
    # Đo được khi chạy thật (2026-09-04): ticket QLKH-001 có output 18868 nhưng tổng 734862; ngân sách 90000 bị
    # coi là cạn sạch, ticket bị cắt giữa chừng và công sức bị `workspace_reset` xoá, lặp nhiều lần.
    output_used: int = 0
    review_used: int = 0     # token của reviewer/QA/security cho ticket — theo dõi, không trừ vào `limit`
    limit_usd: float | None = None  # trần tiền của ticket (Task.budget_usd), ngoài trần token
    cost_usd: float = 0.0
    @property
    def ratio(self) -> float: return self.output_used / self.limit if self.limit else 0.0
    @property
    def ratio_usd(self) -> float: return self.cost_usd / self.limit_usd if self.limit_usd else 0.0


class SupervisorBase:
    """Cơ chế chung; lớp con của mỗi công ty giữ nguyên tên method và vốn từ của mình."""

    WARN_AT, CUT_AT = 0.8, 1.0
    #: Company chống lặp escalate ngay tại đây (khoá theo thế hệ gate); studio cố ý chống ở orchestrator.
    escalate_once = True

    def __init__(self, debt_threshold: int = 3) -> None:
        self.debt_threshold = max(1, int(debt_threshold))
        # Mọi thứ dưới đây là hàm thuần của bus (đếm lại khi replay) — không có gì chỉ sống trong RAM
        # (khuôn 2, TRAPS.md). project_id → debt_id → sổ: tổng lần nhắc, ticket, nguồn, chuỗi liên tiếp theo
        # nguồn, và `fired` = số lần đã chạm ngưỡng (thế hệ của khoá once, khuôn 3).
        self.debt: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.debt_due: list[dict[str, Any]] = []  # mỗi lần một mã nợ chạm ngưỡng: orchestrator mở gate escalation dự án
        self.ticket_project: dict[str, str] = {}  # review-results có thể thiếu project_id; học từ `tasks`
        self.notified: dict[str, set[str]] = defaultdict(set)  # đích → hành động đã phát: mỗi ngưỡng đúng một lần
        self._escalated_once: set[str] = set()

    def _act(self, target: str, action: SupervisorActionKind, reason: str, evidence: str | None = None) -> None:
        raise NotImplementedError  # pragma: no cover — lớp con luôn cài đặt

    def _act_once(self, target: str, action: SupervisorActionKind, reason: str) -> None:
        # Mỗi audit-log sau ngưỡng đều qua đây; chỉ phát hành động lần đầu, tránh spam warn/budget_cut lên bus.
        if action in self.notified[target]: return
        self.notified[target].add(action); self._act(target, action, reason)

    def escalate_gate(self, subject_id: str, reason: str, once_key: str | None = None) -> None:
        """Gate quá hạn: người duyệt im lặng cũng là một dạng bế tắc, phải hiện ra như mọi bế tắc khác."""
        if self.escalate_once:
            key = once_key or f"gate:{subject_id}"
            if key in self._escalated_once: return
            self._escalated_once.add(key)
        self._act(subject_id, "escalate", reason)

    def _count_debt(self, env: Envelope) -> None:
        """ADR-0032: đếm mã nợ theo (dự án, nguồn review). Review của một nguồn KHÔNG nhắc mã nợ nó từng nhắc → chuỗi
        của nguồn đó về 0 (nợ đã trả hoặc đã có ADR). Chạm bội số của ngưỡng → một mục `debt_due` mang `times`
        (lần thứ mấy): lần sau nợ tăng tiếp vẫn mở gate mới, không bị khoá once của lần trước nuốt."""
        p = env.payload
        pid = p.get("project_id") or self.ticket_project.get(str(p.get("ticket_id") or env.key))
        if not pid: return
        src = str(p.get("source") or env.actor); tid = str(p.get("ticket_id") or env.key)
        ids = debt_ids(p); book = self.debt[str(pid)]
        for did, rec in book.items():
            if did not in ids and src in rec["streak"]: rec["streak"][src] = 0
        for did in sorted(ids):
            rec = book.setdefault(did, {"mentions": 0, "tickets": [], "sources": [], "streak": {}, "fired": 0})
            rec["mentions"] += 1
            if tid not in rec["tickets"]: rec["tickets"].append(tid)
            if src not in rec["sources"]: rec["sources"].append(src)
            rec["streak"][src] = rec["streak"].get(src, 0) + 1
            if rec["streak"][src] % self.debt_threshold == 0:
                rec["fired"] += 1
                self.debt_due.append({"project_id": str(pid), "debt_id": did, "times": rec["fired"],
                                      "consecutive": rec["streak"][src], "source": src, "mentions": rec["mentions"],
                                      "tickets": list(rec["tickets"]), "hint": DEBT_HINT})

    def debt_table(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Bảng nợ kiến trúc đã đếm sẵn (cho báo cáo sprint, `status`, và prompt supervisor): mỗi dòng một mã nợ."""
        rows = []
        for pid, book in sorted(self.debt.items()):
            if project_id and pid != project_id: continue
            for did, rec in sorted(book.items()):
                rows.append({"project_id": pid, "debt_id": did, "mentions": rec["mentions"],
                             "consecutive": max(rec["streak"].values(), default=0), "tickets": list(rec["tickets"]),
                             "sources": list(rec["sources"]), "escalated": rec["fired"], "threshold": self.debt_threshold})
        return rows
