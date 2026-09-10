"""`xagents_core.trace` — dòng thời gian một chủ thể, phần dùng chung hai công ty (4L-7).

Chỉ canh phần KHUNG: cấu trúc một dòng, cách đọc tên action CHUNG của `audit-log`, tổng kết, cách in, định
dạng thời gian. Việc "chủ thể là gì, event nào thuộc về nó" là hook của từng miền và được canh ở đó — ca nào
ở đây mà nhắc tên topic của một công ty (`tasks`, `video-briefs`…) nghĩa là nghĩa đã rò lên core.

Bẫy được canh riêng: `build` KHÔNG sort lại — nó phải giữ đúng thứ tự đầu vào (thứ tự nhân quả của
`bus.replay()`), kể cả khi timestamp giảm dần.
"""
from __future__ import annotations

import io
import json

import pytest

from xagents_core.events import Envelope
from xagents_core.trace import (
    TraceError,
    _gop_lap,
    _hms,
    _wait,
    audit_row,
    base_row,
    build,
    evidence,
    render,
    run,
    summarize,
)


class _Spec:
    model_tier = "heavy"


def _env(topic="ban-tin", actor="a", payload=None, **kw):
    return Envelope(topic=topic, key="K1", actor=actor, payload=payload or {}, **kw)


def _audit(action: str, data: dict, **p):
    return _env("audit-log", "orchestrator", {"action": action, "evidence": json.dumps(data), **p})


# ---------- 1. đọc evidence ----------

def test_evidence_hong_hoac_khong_phai_dict_thi_rong():
    assert evidence({"evidence": '{"a": 1}'}) == {"a": 1}
    assert evidence({"evidence": "{khong-phai-json"}) == {}
    assert evidence({"evidence": "[1, 2]"}) == {}
    assert evidence({}) == {}


# ---------- 2. khung một dòng ----------

def test_base_row_gan_tier_khi_actor_la_agent():
    e = _env(actor="script-writer")
    r = base_row(e, None, {"script-writer": _Spec()})
    assert r["agent"] == "script-writer" and r["tier"] == "heavy" and r["wait_s"] == 0.0
    assert base_row(e, None, {})["agent"] is None, "không có registry thì không đoán agent/tier"


def test_base_row_cho_tu_moc_truoc():
    e1, e2 = _env(), _env()
    e2.ts = e1.ts.replace(microsecond=0) + __import__("datetime").timedelta(seconds=90)
    assert base_row(e2, e1.ts, {})["wait_s"] > 0


# ---------- 3. `audit-log`: tên action chung hai công ty ----------

@pytest.mark.parametrize("action, data, kiem", [
    ("produced:x", {"model": "m1", "duration_ms": 12, "turns": 2}, lambda r: r["model"] == "m1" and "12 ms" in r["note"]),
    ("tools_used", {"calls": {"read": 3}}, lambda r: r["tools"] == {"read": 3}),
    ("tools_used", {"calls": "khong-phai-dict"}, lambda r: r["tools"] is None),
    ("tools_trace", {"mode": "cli", "calls": []}, lambda r: r["note"] == "(tool do CLI chạy, không có vết)"),
    ("tools_trace", {"calls": []}, lambda r: r["note"] is None and r["sub"] is None),
    ("llm_retry", {"attempts": 2, "notes": ["429"]}, lambda r: r["retry"] == 2 and r["note"] == "429"),
    ("llm_retry", {}, lambda r: r["retry"] == 1 and r["note"] is None),
    ("gate.request", {"subject_id": "S1", "kind": "k", "created_by": "human:x"},
     lambda r: r["gate"]["by"] == "human:x" and r["gate"]["reason"] is None),
    ("llm_error", {"error": "hết quota"}, lambda r: r["error"] == "hết quota"),
    ("chuyen-la", {}, lambda r: r["note"] == '{}'),
])
def test_audit_row_tung_ho_action(action, data, kiem):
    p = {"action": action, "evidence": json.dumps(data), "tokens": 7, "cost_usd": 0.5}
    r = audit_row(base_row(_audit(action, data), None, {}), p)
    assert r["tokens"] == 7 and r["cost_usd"] == 0.5
    assert kiem(r), r


def test_audit_row_loi_khong_co_evidence_thi_lay_ten_action():
    r = audit_row(base_row(_env(), None, {}), {"action": "llm_error", "evidence": ""})
    assert r["error"] == "llm_error"


def test_audit_row_bo_loi_rieng_mien_qua_tham_so():
    p = {"action": "render_failed", "evidence": json.dumps({"error": "ffmpeg chết"})}
    assert audit_row(base_row(_env(), None, {}), p)["error"] is None, "mặc định core không biết action riêng miền"
    r = audit_row(base_row(_env(), None, {}), p, frozenset({"render_failed"}))
    assert r["error"] == "ffmpeg chết"


# ---------- 4. gộp lời gọi tool lặp ----------

def test_gop_lap_chi_gop_tu_ba_lan_lien_tiep():
    def c(name, out="o"):
        return {"name": name, "args_hash": "a", "out_hash": out, "ok": True, "chars": 1, "ms": 2, "args": {}}
    calls = [c("poll"), c("poll"), c("read"), c("poll"), c("poll"), c("poll")]
    out = _gop_lap(calls)
    assert [x["name"] for x in out] == ["poll", "poll", "read", "poll"]
    assert out[-1]["n"] == 3 and "n" not in out[0]


# ---------- 5. build: thứ tự, hook miền, kind của gate ----------

def test_build_giu_dung_thu_tu_dau_vao_khong_sort_theo_ts():
    import datetime as dt
    e1, e2 = _env(payload={"status": "sau"}), _env(payload={"status": "truoc"})
    e1.ts = e2.ts + dt.timedelta(seconds=10)          # event ĐẦU có timestamp LỚN hơn
    rows = build([e1, e2], {})
    assert [r["note"] for r in rows] == ["sau", "truoc"], "core sort lại là đảo nhân quả thật"
    assert rows[1]["wait_s"] < 0


def test_build_hook_mien_thay_the_ghi_chu_chung():
    def domain(row, e):
        if e.topic != "rieng": return False
        row["note"] = "của miền"; return True
    rows = build([_env("rieng", payload={"status": "x"}), _env(payload={"verdict": "pass"}),
                  _env(payload={})], {}, domain)
    assert [r["note"] for r in rows] == ["của miền", "pass", None]


def test_build_gate_decide_muon_kind_cua_gate_request():
    rows = build([_audit("gate.request", {"subject_id": "S1", "kind": "publish"}),
                  _audit("gate.decide", {"subject_id": "S1", "decision": "approve", "by": "human:x", "reason": "ok"}),
                  _audit("gate.decide", {"subject_id": "KHAC", "decision": "reject"})], {})
    assert [r["gate"]["kind"] for r in rows] == ["publish", "publish", None]


# ---------- 6. tổng kết ----------

def test_summarize_dem_gate_token_loi_va_khoang_thoi_gian():
    rows = build([_audit("gate.request", {"subject_id": "S1", "kind": "k"}),
                  _audit("gate.decide", {"subject_id": "S1", "decision": "approve", "by": "h"}),
                  _audit("llm_retry", {"attempts": 3}),
                  _audit("llm_error", {"error": "x"})], {})
    s = summarize(rows)
    assert s["rows"] == 4 and s["gates_opened"] == 1 and s["gates_decided"] == 1
    assert s["llm_retries"] == 3 and s["errors"] == 1 and len(s["gates"]) == 2
    assert s["span_s"] >= 0 and s["gate_wait_s_max"] >= 0
    assert summarize([])["span_s"] == 0.0 and summarize([])["rows"] == 0


# ---------- 7. in ra ----------

def test_wait_va_hms_doi_don_vi():
    assert _wait(5) == "+5s" and _wait(600) == "+10m" and _wait(7200) == "+2.0h"
    assert _hms("2026-09-09T01:02:03+00:00").endswith("01:02:03")


def test_render_du_moi_manh_cua_mot_dong():
    calls = [{"name": "read", "args": {"path": "a"}, "args_hash": "h", "out_hash": "o", "ok": False,
              "chars": 5, "ms": 3}] * 3
    rows = build([_env(actor="script-writer", payload={"summary": "tóm tắt"}),
                  _audit("produced:x", {"model": "m1"}, tokens=10, cost_usd=0.25),
                  _audit("tools_used", {"calls": {"read": 2}}),
                  _audit("tools_trace", {"calls": calls}),
                  _audit("gate.decide", {"subject_id": "S1", "decision": "approve", "by": "human:x", "reason": "ok"}),
                  _audit("llm_retry", {"attempts": 2}),
                  _audit("llm_error", {"error": "hết quota"})], {"script-writer": _Spec()})
    md = render(["# đầu đề"], rows)
    assert md.splitlines()[0] == "# đầu đề" and md.splitlines()[1] == ""
    assert "[heavy]" in md and "[?/m1]" in md and "10 tok $0.2500" in md
    assert "tool read×2" in md and "↳ read(path=a) LỖI 5c 3ms ×3" in md
    assert "gate  S1 quyết approve by human:x: ok" in md
    assert "retry=2" in md and "LỖI hết quota" in md and "tóm tắt" in md


def test_render_gate_chua_quyet_thi_ghi_mo():
    rows = build([_audit("gate.request", {"subject_id": "S1", "kind": "publish"})], {})
    assert "gate publish S1 mở by ?" in render([], rows)


# ---------- 8. run: JSON / text / chủ thể không có ----------

def test_run_in_json_in_text_va_exit_1_khi_khong_co_chu_the(capsys):
    err = io.StringIO()
    assert run(lambda: {"a": 1}, True, lambda t: "text", err) == 0
    assert json.loads(capsys.readouterr().out) == {"a": 1}
    assert run(lambda: {"a": 1}, False, lambda t: "text", err) == 0
    assert capsys.readouterr().out.strip() == "text"

    def no():
        raise TraceError("không có chủ thể 'X'")
    assert run(no, False, lambda t: "text", err) == 1
    assert "không có chủ thể 'X'" in err.getvalue()
