"""Dòng thời gian MỘT chủ thể của phòng ban video — một video, một kế hoạch `PLAN-…`, hay cả một kênh (4L-7).

Company có `company.trace` từ B7; studio thì không, nên người trực muốn biết "video này đã đi qua những gì, ai
làm, chờ người bao lâu, ai ký gate publish và vì lý do gì" là phải mở SQLite ra tra tay. Lệnh này in đúng dòng
thời gian đó, mỗi mốc một dòng:

    thời điểm · (+chờ từ mốc trước) · topic hoặc audit action · agent · tier/model · token/USD · tool đã gọi ·
    gate mở/quyết (ai, lý do) · retry

    python -m studio.trace <VIDEO_ID | PLAN-… | CHANNEL_ID> [--db studio.sqlite] [--json]

Cấu trúc dòng, cách đọc `audit-log`, tổng kết và cách in dùng chung với company ở `xagents_core.trace`. Ở lại
đây đúng hai thứ riêng của studio: **chủ thể là gì + event nào thuộc về nó** (`resolve`, `_belongs` — đọc theo
`video_id`/`channel_id`/`plan_id`, gate `PUB-`/`ESC-`/`REP-`), và **dòng của topic riêng studio** (`_domain`).

Chỉ đọc thật: `open_read_only` mở SQLite bằng URI `mode=ro` rồi nạp vào một `InMemoryBus` — không DDL, không
PRAGMA, không `publish`. Chạy lệnh này trên file bus của một orchestrator đang chạy không có đường nào ghi nhầm.
Chủ thể không có trong bus → exit 1 với thông báo nói rõ đã tìm ở đâu, không traceback.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

from xagents_core.trace import ERROR_ACTIONS as CORE_ERROR_ACTIONS
from xagents_core.trace import SKIP_ACTIONS as SKIP_ACTIONS
from xagents_core.trace import TraceError as TraceError
from xagents_core.trace import _hms as _hms
from xagents_core.trace import _wait as _wait
from xagents_core.trace import build, evidence, summarize
from xagents_core.trace import render as _render
from xagents_core.trace import run as _run

from .bus import InMemoryBus
from .events import Envelope

#: Lỗi riêng của studio (render, upload, kéo số liệu) cộng với bộ chung của core.
ERROR_ACTIONS = CORE_ERROR_ACTIONS | {"agent_failed", "render_failed", "orchestrator_failed", "sync.failed",
                                      "platform.upload_failed", "platform.reply_failed"}


class TraceOpenError(Exception): ...


def open_read_only(db: Path) -> InMemoryBus:
    """Nạp toàn bộ log SQLite vào một `InMemoryBus` qua kết nối `mode=ro` (đối xứng `company.gate_brief`)."""
    if not db.is_file():
        raise TraceOpenError(f"không có bus SQLite: {db}")
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT body FROM events ORDER BY seq").fetchall()
    finally:
        con.close()
    bus = InMemoryBus(enforce_owners=False)
    bus._log = [Envelope.model_validate_json(body) for (body,) in rows]
    return bus


def _reply_of(vids: set[str], sid: Any) -> bool:
    """Lô trả lời bình luận: `REP-<video_id>-<n>` (xem `_collect_replies`) — gate của nó thuộc về video ấy."""
    return isinstance(sid, str) and any(sid.startswith(f"REP-{v}-") for v in vids)


def resolve(bus: InMemoryBus, subject: str) -> dict[str, Any]:
    """Chủ thể là gì (video/plan/channel) và những id nào cùng một câu chuyện với nó.

    Video: chính nó + kênh của nó + mọi kế hoạch có nó. Kế hoạch: các video trong kế hoạch + kênh.
    Kênh: mọi video, mọi kế hoạch của kênh."""
    videos: dict[str, str] = {}                 # video → kênh
    plans: dict[str, dict[str, Any]] = {}       # plan_id → evidence của `plan.proposed`
    channels: set[str] = set()
    for e in bus.replay():
        p = e.payload
        cid = p.get("channel_id")
        if isinstance(cid, str) and cid: channels.add(cid)
        if e.topic == "video-briefs": videos.setdefault(str(p.get("video_id") or e.key), str(cid or ""))
        elif e.topic == "audit-log" and p.get("action") == "plan.proposed":
            d = evidence(p)
            if d.get("plan_id"): plans.setdefault(str(d["plan_id"]), d)
    if subject in videos:
        cid = videos[subject]; vids = {subject}; kind = "video"
        pids = {pid for pid, d in plans.items() if any(b.get("video_id") == subject for b in d.get("briefs") or [])}
    elif subject in plans:
        d = plans[subject]; cid = str(d.get("channel_id") or ""); pids = {subject}; kind = "plan"
        vids = {str(b["video_id"]) for b in d.get("briefs") or [] if b.get("video_id")}
    elif subject in channels:
        cid = subject; kind = "channel"
        vids = {v for v, c in videos.items() if c == cid}
        pids = {pid for pid, d in plans.items() if d.get("channel_id") == cid}
    else:
        raise TraceError(f"không có chủ thể {subject!r} trong bus: không phải video (topic video-briefs: {len(videos)}), "
                         f"kế hoạch (plan.proposed: {len(plans)}) hay kênh ({', '.join(sorted(channels)) or 'chưa có'})")
    return {"kind": kind, "subject": subject, "channel_id": cid, "videos": sorted(vids), "plans": sorted(pids)}


def _belongs(e: Envelope, scope: dict[str, Any]) -> bool:
    vids = set(scope["videos"]); pids = set(scope["plans"]); cid = scope["channel_id"]
    ids = {cid, *vids, *pids, *(f"PUB-{v}" for v in vids), *(f"ESC-{v}" for v in vids)} - {""}
    p = e.payload; d = evidence(p) if e.topic == "audit-log" else {}
    if e.topic == "audit-log" and str(p.get("action", "")) in SKIP_ACTIONS: return False
    # Video khác trong cùng kênh là nhiễu khi trace MỘT video (hoặc một kế hoạch): loại theo video_id trước.
    for src in (p, d):
        v = src.get("video_id")
        if isinstance(v, str) and v and v not in vids and scope["kind"] != "channel": return False
    cands = [e.key, p.get("video_id"), p.get("channel_id"), d.get("video_id"), d.get("channel_id"),
             d.get("subject_id"), d.get("plan_id"), d.get("batch_id")]
    if any(c in ids for c in cands if isinstance(c, str)): return True
    return _reply_of(vids, d.get("subject_id")) or _reply_of(vids, d.get("batch_id")) or _reply_of(vids, e.key)


def _domain(row: dict[str, Any], e: Envelope) -> bool:
    """Topic riêng của studio: brief mang tiêu đề đang làm, media-asset mang loại file vừa dựng."""
    p = e.payload
    if e.topic == "video-briefs":
        row["retry"] = int(p.get("retry") or 0)
        row["note"] = str(p.get("hint") or p.get("working_title") or "")[:120] or None
        return True
    if e.topic == "media-assets":
        row["note"] = f"{p.get('kind')} {Path(str(p.get('path') or '')).name}".strip()[:120] or None
        return True
    return False


def trace(bus: InMemoryBus, subject: str, agents: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dòng thời gian + tổng kết của một chủ thể. `agents`: registry để điền tier (mặc định nạp `load_agents`)."""
    if agents is None:
        from .registry import load_agents
        agents = load_agents()
    scope = resolve(bus, subject)
    rows = build((e for e in bus.replay() if _belongs(e, scope)), agents, _domain, ERROR_ACTIONS)
    summary = {**summarize(rows),
               "brief_retries": max((r["retry"] for r in rows if r["topic"] == "video-briefs"), default=0),
               "published": [r["note"] for r in rows if r["topic"] == "publish-events"
                             and str(r["note"]) in {"scheduled", "published"}]}
    return {"schema_version": 1, **scope, "rows": rows, "summary": summary}


def render(t: dict[str, Any]) -> str:
    s = t["summary"]
    header = [f"# trace {t['subject']} ({t['kind']}) — kênh {t['channel_id'] or '?'}; video {', '.join(t['videos']) or '-'}; "
              f"kế hoạch {', '.join(t['plans']) or '-'}",
              f"# {s['rows']} mốc, {s['span_s']:.0f}s; {s['tokens']} token, {s['cost_usd']:.4f} USD; gate mở {s['gates_opened']} / "
              f"quyết {s['gates_decided']} (chờ tối đa {s['gate_wait_s_max']:.0f}s); làm lại {s['brief_retries']}, "
              f"retry model {s['llm_retries']}, lỗi {s['errors']}; đăng: {', '.join(s['published']) or 'chưa'}"]
    return _render(header, t["rows"])


def run(bus: InMemoryBus, subject: str, as_json: bool = False, agents: dict[str, Any] | None = None) -> int:
    """In ra stdout; chủ thể không có trong bus → thông báo rõ ở stderr và exit 1."""
    return _run(lambda: trace(bus, subject, agents), as_json, render, sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dòng thời gian một video / kế hoạch / kênh (chỉ đọc)")
    ap.add_argument("subject"); ap.add_argument("--db", type=Path, default=Path("studio.sqlite"))
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"): stream.reconfigure(encoding="utf-8")
    try:
        bus = open_read_only(ns.db)
    except TraceOpenError as e:
        print(str(e), file=sys.stderr); return 3
    return run(bus, ns.subject, ns.json)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
