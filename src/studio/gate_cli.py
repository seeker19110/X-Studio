"""Human gate CLI: con người duyệt plan / publish / replies / escalation; mọi quyết định ghi vào `audit-log`.

Trạng thái gate không lưu riêng: dựng lại từ replay `audit-log` (action gate.request / gate.decide) trên bus bền vững.

    python -m studio.gate_cli list [--db studio.sqlite] [--full]   # --full: không cắt checklist ở 80 ký tự
    python -m studio.gate_cli approve PUB-V1 --by human:editor --reason "ok"
    python -m studio.gate_cli reject|request_changes|hold|rollback <id> --by <ai> --reason <lý do>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import get_args

from xagents_core.gate_cli import PersistentGate as CorePersistentGate
from xagents_core.gate_cli import trusted_decision as core_trusted_decision
from xagents_core.gates import GateRequest as CoreGateRequest

from .bus import InMemoryBus
from .events import AuditLog, Envelope
from .gates import Decision, GateKind, GateRequest, HumanGate, gate_approvers

DECISIONS: tuple[str, ...] = ("approve", "request_changes", "reject", "hold", "rollback")
ITEM_WIDTH = 80  # `list` cắt mỗi mục checklist (vd. văn bản reply) ở đây trừ khi --full


def format_checklist(items: list[str], full: bool = False) -> str:
    return ",".join(x if full or len(x) <= ITEM_WIDTH else x[:ITEM_WIDTH] + "…" for x in items)


def trusted_decision(env: Envelope) -> dict | None:
    """Đọc một quyết định gate từ envelope `audit-log`; trả `None` nếu KHÔNG đáng tin.

    Allowlist mặc định TỪ CHỐI nằm ở `xagents_core.gate_cli.trusted_decision` từ K3.7 (cùng một logic từng
    phải vá cùng một lỗ hổng hai lần ở hai công ty, 2026-09-09): `audit-log` là topic MỞ, nên `evidence.by`
    chỉ là chuỗi tự do — thứ bus thật sự kiểm là `env.actor`. Hai chỗ studio siết thêm so với core:
    `uat_prefix=None` (phòng ban video không có gate nghiệm thu, nên actor hệ thống KHÔNG bao giờ ký thay
    người) và `decision` phải nằm trong `Decision` của studio."""
    d = core_trusted_decision(env, uat_prefix=None)
    if d is None or d["decision"] not in get_args(Decision): return None
    return d


class PersistentGate(CorePersistentGate[Envelope, AuditLog], HumanGate):
    """HumanGate + ghi mọi request/decision lên bus (audit-log) và dựng lại từ replay khi mở.

    Cơ chế ở core; ở đây chỉ nói core dùng LỚP nào của studio và mang thêm `triggered_by` của miền video."""

    UAT_PREFIX = None  # không có gate nghiệm thu: không actor hệ thống nào ký thay người

    def __init__(self, bus: InMemoryBus, **kw):
        super().__init__(bus, envelope_cls=Envelope, audit_cls=AuditLog, request_cls=GateRequest, **kw)

    def _trusted(self, env: Envelope) -> dict | None:
        return trusted_decision(env)

    def _request_kwargs(self, d: dict) -> dict:
        return {**super()._request_kwargs(d), "triggered_by": d.get("triggered_by")}

    def _request_payload(self, req: CoreGateRequest) -> dict:
        # Chữ ký giữ lớp cơ sở (Liskov); `triggered_by` là trường của studio nên đọc qua `getattr`.
        return {**super()._request_payload(req), "triggered_by": getattr(req, "triggered_by", None)}


def rollback_target(bus: InMemoryBus, vid: str) -> dict | None:
    """publish-event (video) mới nhất đã scheduled/published có platform_ref — thứ duy nhất có thể rút lại."""
    for env in reversed(list(bus.replay(topic="publish-events", key=vid))):
        p = env.payload
        if p.get("kind", "video") == "video" and p.get("status") in {"scheduled", "published"} and p.get("platform_ref"): return p
        if p.get("kind", "video") == "video" and p.get("status") == "rolled_back": return None
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Human gate của phòng ban video")
    ap.add_argument("--db", type=Path, default=Path("studio.sqlite"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list"); ls.add_argument("--full", action="store_true", help="in nguyên văn checklist (vd. toàn bộ reply)")
    rq = sub.add_parser("request"); rq.add_argument("kind", choices=get_args(GateKind)); rq.add_argument("subject_id")
    rq.add_argument("--by", required=True); rq.add_argument("--checklist", default="")
    for d in DECISIONS:
        p = sub.add_parser(d); p.add_argument("subject_id"); p.add_argument("--by", required=True); p.add_argument("--reason", default="")
    ns = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")

    from .media import load_media_config
    from .sqlite_bus import SQLiteBus
    bus = SQLiteBus(ns.db); gate = PersistentGate(bus, approvers=gate_approvers(load_media_config()))
    if ns.cmd == "list":
        remind, overdue = gate.due()
        for sid, r in gate.pending.items():
            flag = " OVERDUE" if sid in overdue else (" remind" if sid in remind else "")
            trig = f" trigger={r.triggered_by}" if r.triggered_by else ""
            print(f"{sid:<14} {r.kind:<10} by={r.created_by or '-':<18}{trig} checklist={format_checklist(r.checklist, ns.full)}{flag}")
        if not gate.pending: print("(không có gate chờ)")
        return 0
    if ns.cmd == "request":
        gate.request(GateRequest(kind=ns.kind, subject_id=ns.subject_id, created_by=ns.by,
                                 checklist=[c for c in ns.checklist.split(",") if c]))
        print(f"requested {ns.kind} {ns.subject_id}"); return 0
    if ns.cmd == "rollback" and ns.subject_id.startswith("PUB-") and rollback_target(bus, ns.subject_id[4:]) is None:
        print(f"không có gì để rollback: {ns.subject_id[4:]} chưa có publish-event scheduled/published với platform_ref", file=sys.stderr)
        return 4
    try:
        done = gate.decide(ns.subject_id, ns.cmd, by=ns.by, reason=ns.reason)
    except KeyError:
        print(f"không có gate chờ: {ns.subject_id}", file=sys.stderr); return 2
    except PermissionError as e:
        print(str(e), file=sys.stderr); return 3
    print(f"{done.subject_id}: {done.decision} by {done.decided_by}"); return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
