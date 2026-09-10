"""Dòng thời gian MỘT chủ thể dựng từ audit-log, dùng chung hai công ty (4L-7, tiếp ADR gốc 0001).

`company/trace.py` (B7, đã qua 4L-2) là bản gốc; studio trước 4L-7 không có gì tương đương — người trực không
xem được lịch sử một video từ CLI. Bước này tách bản company làm hai:

**Chung (ở đây)** — cấu trúc một dòng, cách đọc `audit-log`, tổng kết, cách in, định dạng thời gian:

| phần | vì sao chung |
|---|---|
| `base_row` / `audit_row` | tên action của audit là CHUNG hai công ty (`produced:*`, `tools_used`, `tools_trace`, `llm_retry`, `gate.request`, `gate.decide`) vì cùng một `AgentRunner`/`PersistentGate` sinh ra |
| `_gop_lap` | gộp lời gọi tool lặp liên tiếp — thuộc về hình dạng của `tools_trace`, không thuộc miền nào |
| `summarize` | token/USD/gate/chờ/retry/lỗi/khoảng thời gian — đếm trên dòng, không cần biết ticket hay video |
| `_hms`, `_wait`, `render` | trình bày |
| `run` | in JSON hay text, `TraceError` → exit 1 |

**Riêng (ở nơi gọi)** — hai thứ, và chỉ hai:

1. **Chủ thể là gì và event nào thuộc về nó.** Company tra theo `ticket_id`/`release_id`/`project_id` trên topic
   `tasks`/`release-candidates`; studio tra theo `video_id`/`channel_id` trên `video-briefs`. Core KHÔNG được
   biết tên topic nào (`tasks`, `video-briefs`…): `build()` nhận vào một danh sách envelope **đã lọc sẵn**.
2. **Dòng của topic riêng miền.** `tasks` có `retry`/`human_hint`, `release-events` có `env`/`status`,
   `video-briefs` có `title` — vào bằng hook `domain`, không vào bằng `if e.topic == ...` trong core.

Thứ tự: duyệt ĐÚNG thứ tự `bus.replay()` trả về (thứ tự nhân quả thật). KHÔNG sort lại theo timestamp — hai
envelope cùng micro giây sẽ đảo chỗ và người đọc thấy "quyết định trước khi mở gate".
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, TypeVar

from .events import Envelope

E = TypeVar("E", bound=Envelope)

__all__ = ["ERROR_ACTIONS", "SKIP_ACTIONS", "TraceError", "audit_row", "base_row", "build", "evidence",
           "render", "run", "summarize"]

#: Audit chỉ nói về việc nội bộ của orchestrator, không nói gì về chủ thể: bỏ khỏi dòng thời gian.
SKIP_ACTIONS = frozenset({"once", "orchestrated"})
#: Action mà cả hai công ty coi là lỗi. Mỗi công ty thêm tên riêng của mình qua tham số `error_actions`.
ERROR_ACTIONS = frozenset({"agent_error_unhandled", "handler_error", "invalid_output", "llm_error"})


class TraceError(Exception): ...


def evidence(a: dict[str, Any]) -> dict[str, Any]:
    """`evidence` của một `audit-log` là JSON dạng chuỗi; hỏng thì trả rỗng, không làm chết lệnh chỉ đọc."""
    try: d = json.loads(a.get("evidence") or "{}")
    except json.JSONDecodeError: return {}
    return d if isinstance(d, dict) else {}


def _gop_lap(calls: list[Any]) -> list[dict[str, Any]]:
    """Gộp các lần gọi LIÊN TIẾP cùng bộ ba (name, args_hash, out_hash) — vd. vòng lặp tool poll trạng thái gọi
    lại y hệt nhiều lần — thành MỘT dòng `×N` khi N ≥ 3. Dưới 3 lần thì để riêng: 2 lần giống nhau vẫn còn ít để
    người đọc tự thấy, gộp sớm chỉ làm mất thứ tự thật."""
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(calls):
        j = i + 1
        key = (calls[i].get("name"), calls[i].get("args_hash"), calls[i].get("out_hash"))
        while j < len(calls) and (calls[j].get("name"), calls[j].get("args_hash"), calls[j].get("out_hash")) == key:
            j += 1
        n = j - i
        if n >= 3:
            row = dict(calls[i]); row["n"] = n
            out.append(row)
        else:
            out.extend(calls[i:j])
        i = j
    return out


def base_row(e: Envelope, prev: datetime | None, agents: dict[str, Any]) -> dict[str, Any]:
    """Khung một dòng: thời điểm, khoảng chờ từ mốc trước, topic/actor, và tier của agent nếu actor là agent."""
    row: dict[str, Any] = {"at": e.ts.isoformat(), "wait_s": round((e.ts - prev).total_seconds(), 3) if prev else 0.0,
                           "topic": e.topic, "action": None, "actor": e.actor, "agent": None, "tier": None, "model": None,
                           "tokens": 0, "cost_usd": 0.0, "tools": None, "sub": None, "gate": None, "retry": None,
                           "error": None, "note": None, "event_id": e.event_id}
    spec = agents.get(e.actor)
    if spec is not None:
        row["agent"] = e.actor; row["tier"] = getattr(spec, "model_tier", None)
    return row


def audit_row(row: dict[str, Any], p: dict[str, Any], error_actions: frozenset[str] = ERROR_ACTIONS) -> dict[str, Any]:
    """Điền phần `audit-log` vào một dòng đã có khung. Tên action ở đây là tên CHUNG hai công ty."""
    act = str(p.get("action", "")); d = evidence(p); row["action"] = act
    row["tokens"] = int(p.get("tokens") or 0); row["cost_usd"] = round(float(p.get("cost_usd") or 0.0), 6)
    if act.startswith("produced:"):
        row["model"] = d.get("model"); row["note"] = f"{d.get('duration_ms', 0)} ms, {d.get('turns', 0)} lượt"
    elif act == "tools_used":
        calls = d.get("calls") or {}
        row["tools"] = {str(k): int(v) for k, v in calls.items()} if isinstance(calls, dict) else None
    elif act == "tools_trace":
        # 4L-2: vết TỪNG lời gọi (`ToolBox.trace()`), một dòng `↳` mỗi call ở render() — riêng với `tools_used`
        # (đếm gộp) ở trên. mode "cli" (ADR-0023) không đi qua `ToolBox` → `calls` rỗng: nói rõ bằng `note`,
        # không im lặng in một khối `sub` rỗng.
        calls = d.get("calls") or []
        if isinstance(calls, list) and calls:
            row["sub"] = _gop_lap(calls)
        elif d.get("mode") == "cli":
            row["note"] = "(tool do CLI chạy, không có vết)"
    elif act == "llm_retry":
        row["retry"] = int(d.get("attempts") or 1); row["note"] = "; ".join(str(x) for x in d.get("notes") or [])[:120] or None
    elif act in {"gate.request", "gate.decide"}:
        row["gate"] = {"subject_id": d.get("subject_id"), "kind": d.get("kind"), "decision": d.get("decision"),
                       "by": d.get("by") or d.get("created_by"), "reason": str(d.get("reason") or "")[:200] or None}
    elif act in error_actions:
        row["error"] = str(d.get("error") or p.get("evidence") or act)[:200]
    else:
        row["note"] = str(p.get("evidence") or "")[:120] or None
    return row


def build(events: Iterable[E], agents: dict[str, Any] | None = None,
          domain: Callable[[dict[str, Any], E], bool] | None = None,
          error_actions: frozenset[str] = ERROR_ACTIONS) -> list[dict[str, Any]]:
    """Dựng các dòng từ danh sách envelope ĐÃ LỌC SẴN theo chủ thể — core không lọc, không sort.

    `domain(row, env) -> bool` là hook của miền cho topic KHÔNG phải `audit-log`: trả `True` nghĩa là đã điền
    xong dòng đó, core không đụng nữa; trả `False` thì core điền `note` chung (status/verdict/decision/summary).
    `gate.decide` không ghi `kind`, nên lấy `kind` từ `gate.request` cùng `subject_id` đứng trước nó.
    """
    agents = agents or {}
    rows: list[dict[str, Any]] = []
    prev: datetime | None = None
    gate_kind: dict[str, Any] = {}
    for e in events:
        p = e.payload
        row = base_row(e, prev, agents); prev = e.ts
        if e.topic == "audit-log":
            audit_row(row, p, error_actions)
            if row["gate"]:
                if row["gate"]["kind"]: gate_kind[str(row["gate"]["subject_id"])] = row["gate"]["kind"]
                else: row["gate"]["kind"] = gate_kind.get(str(row["gate"]["subject_id"]))
        elif domain is None or not domain(row, e):
            note = p.get("status") or p.get("verdict") or p.get("decision") or p.get("summary")
            row["note"] = str(note)[:120] if note else None
        rows.append(row)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Tổng kết đếm được trên dòng, không cần biết miền. Mỗi công ty gộp thêm số riêng của mình vào."""
    gates = [r["gate"] for r in rows if r["gate"]]
    opened = [r for r in rows if r["gate"] and r["action"] == "gate.request"]
    decided = [r for r in rows if r["gate"] and r["action"] == "gate.decide"]
    waits = [r["wait_s"] for r in rows if r["action"] == "gate.decide"]
    return {"rows": len(rows), "tokens": sum(r["tokens"] for r in rows),
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
            "gates_opened": len(opened), "gates_decided": len(decided), "gates": gates,
            "gate_wait_s_max": max(waits, default=0.0),
            "llm_retries": sum(r["retry"] or 0 for r in rows if r["action"] == "llm_retry"),
            "errors": sum(1 for r in rows if r["error"]),
            "span_s": round((datetime.fromisoformat(rows[-1]["at"]) - datetime.fromisoformat(rows[0]["at"])).total_seconds(), 3)
            if rows else 0.0}


def _hms(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%m-%d %H:%M:%S")


def _wait(s: float) -> str:
    if s < 60: return f"+{s:.0f}s"
    if s < 3600: return f"+{s / 60:.0f}m"
    return f"+{s / 3600:.1f}h"


def render(header: list[str], rows: list[dict[str, Any]]) -> str:
    """`header`: các dòng `#` do miền tự viết (nó biết ticket/video là gì). Phần thân giống hệt hai bên."""
    out = [*header, ""]
    for r in rows:
        what = f"{r['topic']}/{r['action']}" if r["action"] else r["topic"]
        who = r["agent"] or r["actor"]
        if r["model"]: who += f" [{r['tier'] or '?'}/{r['model']}]"
        elif r["tier"]: who += f" [{r['tier']}]"
        parts = [f"{_hms(r['at'])} {_wait(r['wait_s']):>7}  {what:<34} {who}"]
        if r["tokens"]: parts.append(f"{r['tokens']} tok ${r['cost_usd']:.4f}")
        if r["tools"]: parts.append("tool " + " ".join(f"{k}×{v}" for k, v in sorted(r["tools"].items())))
        if r["gate"]:
            g = r["gate"]
            parts.append(f"gate {g['kind'] or ''} {g['subject_id']} {'quyết ' + str(g['decision']) if g['decision'] else 'mở'} "
                         f"by {g['by'] or '?'}" + (f": {g['reason']}" if g["reason"] else ""))
        if r["retry"]: parts.append(f"retry={r['retry']}")
        if r["error"]: parts.append(f"LỖI {r['error']}")
        if r["note"]: parts.append(r["note"])
        out.append("  | ".join(parts))
        if r["sub"]:
            for c in r["sub"]:
                args = " ".join(f"{k}={v}" for k, v in (c.get("args") or {}).items())
                rep = f" ×{c['n']}" if c.get("n") else ""
                out.append(f"    ↳ {c['name']}({args}) {'ok' if c['ok'] else 'LỖI'} {c['chars']}c {c['ms']}ms{rep}")
    return "\n".join(out)


def run(make: Callable[[], dict[str, Any]], as_json: bool, render_text: Callable[[dict[str, Any]], str],
        err: Any) -> int:
    """Lõi chung của mọi lệnh trace: dựng, in JSON hoặc text, `TraceError` → thông báo rõ + exit 1 (không traceback)."""
    try:
        t = make()
    except TraceError as e:
        print(str(e), file=err); return 1
    print(json.dumps(t, ensure_ascii=False, indent=2) if as_json else render_text(t)); return 0
