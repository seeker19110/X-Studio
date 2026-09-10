"""Ngữ cảnh có hạn mức (ADR-0012 của software-company, chuyển sang core ở K3.1).

Module này trung lập tuyệt đối — không đọc cấu hình, không biết tên công ty — nên nó là bước ĐẦU của K3: nếu
một bước chuyển mã có thể hỏng thì hỏng ở đây là rẻ nhất. Test chuyển sang cùng PR với mã (bất biến 2 của
kịch bản B: dòng nào chuyển package thì test phủ dòng đó chuyển theo).
"""
from __future__ import annotations

import json

from xagents_core.context import (
    CHARS_PER_TOKEN,
    MIN_KEEP,
    ContextBudget,
    _prune,
    chars_counter,
    cut_middle,
    fit,
    trim_payload,
)


def _turn(idx: int, tool_content: str = "ket qua") -> list[dict]:
    """Một 'lượt' vòng tool: một assistant gọi một tool, kèm phản hồi role=tool tương ứng."""
    return [{"role": "assistant", "content": "", "tool_calls": [{"id": f"c{idx}", "name": "read_file", "args": {"path": f"f{idx}.py"}}]},
            {"role": "tool", "tool_call_id": f"c{idx}", "content": tool_content}]


def _msgs(n_turns: int, tool_content: str = "ket qua") -> list[dict]:
    msgs = [{"role": "user", "content": "yeu cau goc"}]
    for i in range(1, n_turns + 1):
        msgs += _turn(i, tool_content)
    return msgs


def test_prune_giu_k_luot():
    """5 lượt, keep_turns=3: 2 lượt đầu (1, 2) bị tỉa, 3 lượt cuối (3, 4, 5) còn nguyên."""
    msgs = _msgs(5, "x" * 100)
    out, dropped = _prune(msgs, keep_turns=3)
    assert dropped > 0
    tool_msgs = [m for m in out if m["role"] == "tool"]
    assert tool_msgs[0]["content"].startswith("[đã cắt:") and tool_msgs[1]["content"].startswith("[đã cắt:")
    assert tool_msgs[2]["content"] == "x" * 100 and tool_msgs[3]["content"] == "x" * 100 and tool_msgs[4]["content"] == "x" * 100


def test_khong_cat_duoi_k():
    """Đúng hoặc ít hơn keep_turns lượt: không có gì để tỉa, msgs không đổi."""
    msgs = _msgs(3, "x" * 100)
    out, dropped = _prune(msgs, keep_turns=3)
    assert dropped == 0 and out == msgs


def test_giu_user_dau_va_tool_calls():
    """msgs[0] (yêu cầu gốc) không bao giờ bị tỉa; `tool_calls` của assistant vẫn nguyên vẹn dù tool cũ bị tỉa."""
    msgs = _msgs(6, "y" * 200)
    out, _dropped = _prune(msgs, keep_turns=3)
    assert out[0] == {"role": "user", "content": "yeu cau goc"}
    assistants = [m for m in out if m["role"] == "assistant"]
    assert all(m.get("tool_calls") for m in assistants), "tool_calls không bị đụng, kể cả ở lượt bị tỉa"
    assert len(assistants) == 6


def test_fit_cat_payload_truoc_roi_toi_context_va_gan_nhan():
    system = "x" * 1_000
    payload = {"ticket_id": "T1", "diff": "a" * 50_000, "summary": "s"}
    ctx = {"prd": {"version": 1, "content_ref": "docs/prd.md", "summary": "PRD", "content": "p" * 30_000},
           "glossary": {"version": 1, "content_ref": "g.md", "summary": "g", "content": "g" * 500}}
    p, c, b = fit(system, payload, ctx, max_input_chars=20_000, paths={"prd": "store/prd/latest.md"})
    assert b.trimmed_payload > 0 and "cắt" in p["diff"] and p["summary"] == "s" and p["diff"].startswith("aaa")
    assert c["glossary"]["content"] == "g" * 500, "namespace ngắn giữ nguyên, phần thừa nhường cho namespace dài"
    assert "store/prd/latest.md" in c["prd"]["content"] and b.trimmed_context["prd"] > 0
    assert b.system_chars + b.payload_chars + b.context_chars <= 20_000 and b.est_tokens > 0
    _, c2, b2 = fit(system, {"a": "b"}, ctx, max_input_chars=200_000)
    assert not b2.trimmed and c2["prd"]["content"] == "p" * 30_000, "đủ chỗ thì không cắt gì"
    assert cut_middle("abcdef", 100) == "abcdef" and trim_payload({"x": "y"}, 5)[1] == 0


def test_trim_payload_di_sau_vao_list():
    """`_strings` phải đệ quy cả vào phần tử của list, không chỉ dict — payload có list chuỗi dài."""
    payload = {"logs": ["a" * 1000, "b" * 1000]}
    trimmed, cut = trim_payload(payload, 500)
    assert cut > 0
    assert any("cắt" in s for s in trimmed["logs"]), trimmed["logs"]


def test_khong_du_cho_giu_nghia_thi_bo_han_noi_dung_nhung_giu_duong_dan():
    """Hạn mức chia cho NHIỀU namespace tới mức mỗi phần < `MIN_KEEP`: giữ một khúc cụt vài trăm ký tự là vô
    nghĩa, nên bỏ hẳn nội dung — nhưng nhãn phải chỉ đúng chỗ đọc đầy đủ. Agent có tool đọc artifact; thứ nó cần
    là ĐƯỜNG DẪN, không phải một mẩu vụn.

    Một namespace duy nhất thì `alloc` luôn ≥ `MIN_KEEP` nên không vào được nhánh này — ngưỡng ở đây đo bằng
    `fit` thật, không suy từ công thức."""
    ctx = {f"ns{i}": {"version": 1, "content_ref": f"{i}.md", "summary": "s", "content": "p" * 5_000}
           for i in range(10)}
    _, c, b = fit("s" * 100, {"a": "b"}, ctx, max_input_chars=2_000, paths={"ns0": "store/ns0.md"})
    assert c["ns0"]["content"] == "… (bỏ 5000 ký tự; đọc đầy đủ ở store/ns0.md) …"
    assert len(b.trimmed_context) == 10 and b.trimmed


def test_khong_co_paths_thi_nhan_noi_artifact_nam_tren_blackboard():
    """Nhãn mặc định khi nơi gọi không truyền `paths`: vẫn phải nói được đọc thêm ở đâu."""
    ctx = {f"ns{i}": {"version": 1, "content_ref": f"{i}.md", "summary": "s", "content": "p" * 5_000}
           for i in range(10)}
    _, c, _ = fit("s" * 100, {"a": "b"}, ctx, max_input_chars=2_000)
    assert "artifact đầy đủ trên blackboard" in c["ns0"]["content"]


def test_namespace_khong_co_content_giu_nguyen_phan_con_lai():
    """Blackboard có thể chỉ có `summary` + `content_ref` (bản ghi chưa nạp toàn văn) — không được vỡ, và không
    được bịa ra khoá `content`."""
    ctx = {"prd": {"version": 3, "content_ref": "docs/prd.md", "summary": "PRD"}}
    _, c, b = fit("s", {"a": "b"}, ctx, max_input_chars=50_000)
    assert c["prd"] == {"version": 3, "content_ref": "docs/prd.md", "summary": "PRD"}
    assert "content" not in c["prd"] and not b.trimmed


def test_bao_cao_ngan_sach_du_truong_cho_audit():
    """`report()` là thứ đi vào audit `context_trimmed`; thiếu trường là người đọc audit mất manh mối."""
    b = ContextBudget(max_input_chars=10_000, system_chars=100, payload_chars=200, context_chars=300)
    r = b.report()
    assert set(r) == {"max_input_chars", "system", "payload", "context", "trimmed_payload", "trimmed_context",
                      "est_tokens", "counted_tokens", "actual_tokens", "estimate_error"}
    assert r["est_tokens"] == int(600 / CHARS_PER_TOKEN) and r["trimmed_context"] == {}
    assert not b.trimmed, "chưa cắt gì thì `trimmed` phải False"


def test_cut_middle_giu_dau_va_cuoi():
    s = "A" * 500 + "Z" * 500
    out = cut_middle(s, 400)
    assert out.startswith("A") and out.endswith("Z") and "cắt" in out and len(out) < len(s)


def test_strings_bo_qua_gia_tri_khong_phai_chuoi_dict_list():
    """Payload JSON có cả số, bool và `null` — `_strings` phải rơi thẳng xuống `return`, không nổ.

    Ba nhánh `isinstance` đều trượt là ca THƯỜNG GẶP (`{"exit_code": 0, "ok": true}`), nên nếu hàm giả định
    luôn rơi vào một trong ba thì `trim_payload` hỏng với gần như mọi payload thật."""
    # `limit` phải NHỎ hơn payload, nếu không `trim_payload` thoát ở vòng đầu và `_strings` không hề chạy.
    payload = {"exit_code": 0, "ok": True, "ghi_chu": None, "log": "x" * (MIN_KEEP * 4)}

    data, removed = trim_payload(payload, limit=MIN_KEEP)

    assert removed > 0                                   # chuỗi dài đã bị cắt
    assert (data["exit_code"], data["ok"], data["ghi_chu"]) == (0, True, None)   # ba giá trị kia nguyên vẹn


def test_trim_payload_dung_sau_64_vong_khi_khong_the_nho_hon():
    """Trần 64 vòng là chốt chống lặp vô hạn — ca này đi HẾT 64 vòng, không `break` giữa chừng.

    Mỗi vòng cắt đúng chuỗi dài nhất xuống `MIN_KEEP`, nên nó rời khỏi tập ứng viên (`len(s) > MIN_KEEP`).
    Với hơn 64 chuỗi cùng cỡ, tập ứng viên vẫn còn khi vòng thứ 64 kết thúc: `break` ở `size <= limit` không
    bao giờ đúng, `break` ở `not strs` cũng chưa tới. Không có trần này thì hàm treo hẳn."""
    payload = {f"k{i}": "x" * (MIN_KEEP + 10) for i in range(70)}

    data, removed = trim_payload(payload, limit=100)

    assert removed > 0
    assert sum(1 for v in data.values() if len(v) > MIN_KEEP) > 0   # còn ứng viên ⇒ đã hết 64 vòng
# ---------- p3.2a: đo bằng token thật thay vì len(str) ----------

def _golden_case():
    """Ca chuẩn của `fit`: payload nhiều chuỗi dài, ba namespace ngắn/vừa/dài (water-filling đi qua cả ba nhánh)."""
    system = "S" * 1_000
    payload = {"ticket_id": "T1", "diff": "a" * 50_000, "summary": "s", "notes": ["n" * 3_000, "m" * 900]}
    ctx = {"prd": {"version": 1, "content_ref": "docs/prd.md", "summary": "PRD", "content": "p" * 30_000},
           "glossary": {"version": 1, "content_ref": "g.md", "summary": "g", "content": "g" * 500},
           "arch": {"version": 2, "content_ref": "a.md", "summary": "a", "content": "A" * 9_000}}
    return system, payload, ctx


def test_fit_khong_counter_giong_hom_nay_tung_byte():
    """CHIỀU NGƯỢC 1 — ràng buộc số một của p3.2.

    `fit` sinh ra `context` đi thẳng vào `build_user_message`; lệch một byte là mọi bản ghi eval lệch theo
    (TRAPS.md:102). Chuỗi vàng chốt cứng: băm SHA-256 của (payload, context) đo TRƯỚC khi thêm `counter`.
    Đổi số này là tuyên bố phải ghi lại 20 bản ghi eval bằng model thật — không được sửa cho test xanh.
    """
    import hashlib
    p, c, b = fit(*_golden_case(), max_input_chars=20_000, paths={"prd": "store/prd/latest.md"})
    blob = json.dumps([p, c], ensure_ascii=False, sort_keys=True)
    assert hashlib.sha256(blob.encode()).hexdigest() == "e7008575f2c19b9d6e23b9f5cf48a250067d81bd9982a57b58d5cdf3961f5ce1"
    # các trường cũ của report cũng không được đổi nghĩa
    assert {k: v for k, v in b.report().items() if k in {"payload", "context", "trimmed_payload", "trimmed_context", "est_tokens"}} == {
        "payload": 10_422, "context": 7_108, "trimmed_payload": 43_568,
        "trimmed_context": {"prd": 26_855, "arch": 5_855}, "est_tokens": 5_790}


def test_chars_counter_la_hanh_vi_mac_dinh():
    """`chars_counter` phải đo ĐÚNG đơn vị của `max_input_chars` (ký tự). Chia cho CHARS_PER_TOKEN ở đây sẽ
    nới hạn mức lên 3.2 lần — đổi hành vi, không phải giữ nguyên."""
    assert chars_counter("abcđ") == 4
    p1, c1, _ = fit(*_golden_case(), max_input_chars=20_000, counter=chars_counter)
    p2, c2, _ = fit(*_golden_case(), max_input_chars=20_000)
    assert (p1, c1) == (p2, c2)


def test_counter_tuy_bien_cat_nhieu_hon():
    """Counter giả báo 10× → cùng `max_input_chars` nhưng ngân sách thật hẹp đi 10 lần → cắt nhiều hơn thấy rõ."""
    system, payload, ctx = _golden_case()
    _p0, _c0, b0 = fit(system, payload, ctx, max_input_chars=20_000)
    p, _c, b = fit(system, payload, ctx, max_input_chars=20_000, counter=lambda s: len(s) * 10)
    assert b.trimmed_payload > b0.trimmed_payload, "counter đắt hơn thì payload phải bị cắt nhiều hơn"
    assert len(json.dumps(p, ensure_ascii=False)) < len(json.dumps(_p0, ensure_ascii=False))
    assert sum(b.trimmed_context.values()) > sum(b0.trimmed_context.values())
    assert b.counted_tokens == 10 * (b.system_chars + b.payload_chars + b.context_chars)


def test_counted_tokens_mac_dinh_la_uoc_luong_theo_ky_tu():
    _p, _c, b = fit(*_golden_case(), max_input_chars=20_000)
    assert b.counted_tokens == b.est_tokens


def test_estimate_error():
    """counted 1000 / actual 800 → +0.25; actual == 0 → 0.0 (không chia cho 0, không báo sai số bịa)."""
    b = ContextBudget(max_input_chars=1, system_chars=0, counted_tokens=1_000, actual_tokens=800)
    assert b.estimate_error == 0.25
    assert ContextBudget(max_input_chars=1, system_chars=0, counted_tokens=1_000).estimate_error == 0.0
    assert ContextBudget(max_input_chars=1, system_chars=0, counted_tokens=600, actual_tokens=800).estimate_error == -0.25
    assert b.report()["counted_tokens"] == 1_000 and b.report()["actual_tokens"] == 800
    assert b.report()["estimate_error"] == 0.25


def test_estimate_error_lam_tron_dung_4_chu_so():
    """Mọi ca khác dùng số tròn (±0.25) nên không phân biệt được "làm tròn 4" với 2 hay với không làm tròn.
    counted 1000 / actual 810 → 190/810 = 0.234567901…: đúng 0.2346, không phải 0.23 và không phải số đầy đủ."""
    b = ContextBudget(max_input_chars=1, system_chars=0, counted_tokens=1_000, actual_tokens=810)
    assert b.estimate_error == 0.2346
    assert b.estimate_error != round(190 / 810, 2) and b.estimate_error != 190 / 810
    assert b.report()["estimate_error"] == 0.2346
