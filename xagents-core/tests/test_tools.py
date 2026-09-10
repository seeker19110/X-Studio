"""Khung bảng tool (K3.2). Chỉ đo phần KHUNG — bảng tool thật (`WorkspaceTools` của company, `WebTools` của
studio) là ranh giới bảo mật của từng miền và được đo ở test của chính công ty đó.

Bốn lời hứa của khung, mỗi cái là một cách hệ hỏng nếu mất:

1. Tool ngoài bảng → `ToolError` (ngoại lệ, không phải chuỗi): model bịa tên tool là lỗi lập trình của vòng lặp
   tool, không phải thứ để model "tự sửa".
2. Tool trong bảng mà hỏng → **chuỗi** bắt đầu bằng "lỗi": model đọc rồi thử lại, một tool hỏng không giết cả lượt.
3. Tham số thừa/thiếu bị chặn TRƯỚC khi gọi hàm: bảng tool là ranh giới tin cậy, không phải chỗ dựa vào chữ ký
   Python bắt lỗi hộ.
4. `max_output` cắt được và tắt được — hai công ty cố ý khác nhau ở đúng chỗ này.
"""
from __future__ import annotations

import json

import pytest

from xagents_core.tools import MAX_OUTPUT, OPAQUE_ARGS, ToolBox, ToolCall, ToolError, ToolSpec

SPEC = ToolSpec(name="echo", description="trả lại chuỗi",
                parameters={"type": "object", "properties": {"s": {"type": "string"}}, "required": ["s"]})


def _box(fn=lambda s: s, **kw) -> ToolBox:
    tb = ToolBox(**kw)
    tb.add(SPEC, fn)
    return tb


def test_specs_va_goi_binh_thuong():
    tb = _box()
    assert [s.name for s in tb.specs()] == ["echo"]
    assert tb.call(ToolCall(id="1", name="echo", args={"s": "xin chào"})) == "xin chào"
    c = tb.calls[0]
    assert {k: c[k] for k in ("name", "args", "ok", "chars")} == {"name": "echo", "args": {"s": "xin chào"}, "ok": True, "chars": 8}
    assert set(c) == {"name", "args", "ok", "chars", "args_hash", "out_hash", "ms"}
    assert tb.summary() == {"echo": 1}


def test_tool_ngoai_bang_la_ngoai_le_chu_khong_phai_chuoi():
    with pytest.raises(ToolError, match="tool không tồn tại: rm"):
        _box().call(ToolCall(id="1", name="rm", args={}))


@pytest.mark.parametrize("args,dau_hieu", [
    ({"s": "x", "thua": 1}, "thừa ['thua']"),
    ({}, "thiếu ['s']"),
])
def test_tham_so_thua_hoac_thieu_bi_chan_truoc_khi_goi_ham(args, dau_hieu):
    goi: list[str] = []
    tb = _box(lambda s: goi.append(s) or "")
    out = tb.call(ToolCall(id="1", name="echo", args=args))
    assert out.startswith("lỗi tham số:") and dau_hieu in out
    assert goi == [], "hàm tool không được chạy khi tham số sai"
    assert tb.calls[0]["ok"] is False


def test_args_khong_phai_dict_thi_coi_nhu_rong():
    """Model trả `args` là chuỗi/None thì vẫn phải đi vào đường 'thiếu tham số', không nổ `AttributeError`."""
    out = _box().call(ToolCall(id="1", name="echo", args="không phải dict"))  # type: ignore[arg-type]
    assert "thiếu ['s']" in out


@pytest.mark.parametrize("boom,dau_hieu", [
    (ToolError("hết hạn mức"), "lỗi: hết hạn mức"),
    (ValueError("số âm"), "lỗi tham số: số âm"),
    (TypeError("sai kiểu"), "lỗi tham số: sai kiểu"),
])
def test_tool_hong_tra_chuoi_cho_model_chu_khong_giet_luot(boom, dau_hieu):
    def fn(s):
        raise boom
    out = _box(fn).call(ToolCall(id="1", name="echo", args={"s": "x"}))
    assert out == dau_hieu


def test_dau_ra_khong_phai_chuoi_van_thanh_chuoi():
    assert _box(lambda s: 42).call(ToolCall(id="1", name="echo", args={"s": "x"})) == "42"


def test_cat_theo_max_output_va_noi_ro_con_bao_nhieu():
    tb = _box(lambda s: "x" * 10, max_output=4)
    out = tb.call(ToolCall(id="1", name="echo", args={"s": "x"}))
    assert out == "xxxx\n… (cắt, còn 6 ký tự)"
    assert tb.calls[0]["chars"] == len(out), "`chars` đo cái model THẬT SỰ nhận, không phải cái tool sinh ra"


def test_mac_dinh_cat_theo_company_va_tat_duoc_cho_studio():
    """Mặc định là 6.000 (company). Studio truyền `None` để `web_fetch` giữ trọn 20.000 ký tự đã bóc HTML —
    bỏ được mặc định là điều kiện để K3.2 không âm thầm đổi hành vi của studio."""
    assert ToolBox().max_output == MAX_OUTPUT == 6_000
    dai = "y" * 9_000
    assert len(_box(lambda s: dai).call(ToolCall(id="1", name="echo", args={"s": "x"}))) < 6_100
    assert _box(lambda s: dai, max_output=None).call(ToolCall(id="1", name="echo", args={"s": "x"})) == dai


def test_urls_chi_lay_web_fetch_thanh_cong_va_giu_thu_tu():
    spec = ToolSpec(name="web_fetch", description="", parameters={"type": "object",
                    "properties": {"url": {"type": "string"}}, "required": ["url"]})
    tb = ToolBox(max_output=None)
    tb.add(spec, lambda url: "lỗi: chặn IP nội bộ" if "10." in url else "nội dung")
    tb.add(SPEC, lambda s: s)
    for u in ("https://a.vn", "https://10.0.0.1", "https://b.vn"):
        tb.call(ToolCall(id="1", name="web_fetch", args={"url": u}))
    tb.call(ToolCall(id="2", name="echo", args={"s": "https://c.vn"}))
    assert tb.urls() == ["https://a.vn", "https://b.vn"]


def test_trace_cat_content_khong_lo_noi_dung():
    """4L-2: `content` (WRITE_FILE …) là `OPAQUE_ARGS` — trace() không bao giờ ghi nội dung thật, chỉ độ dài."""
    spec = ToolSpec(name="write_file", description="", parameters={"type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]})
    tb = ToolBox()
    tb.add(spec, lambda path, content: "ok")
    bi_mat = "SỐ THẺ KHÁCH: 4111-1111-1111-1111"
    tb.call(ToolCall(id="1", name="write_file", args={"path": "a.txt", "content": bi_mat}))
    tr = tb.trace()
    assert tr[0]["args"]["content"] == f"<{len(bi_mat)} ký tự>"
    assert bi_mat not in json.dumps(tr, ensure_ascii=False)
    assert tr[0]["args"]["path"] == "a.txt"
    assert "content" in OPAQUE_ARGS


def test_trace_hash_on_dinh_khac_thu_tu_khoa():
    """`_h` băm bằng `sort_keys=True`: hai dict cùng nội dung khác thứ tự khoá phải ra cùng args_hash."""
    tb1, tb2 = _box(), _box()
    tb1.call(ToolCall(id="1", name="echo", args={"s": "x"}))
    tb2.call(ToolCall(id="1", name="echo", args={"s": "x"}))
    assert tb1.calls[0]["args_hash"] == tb2.calls[0]["args_hash"]
    from xagents_core.tools import _h
    assert _h({"a": 1, "b": 2}) == _h({"b": 2, "a": 1})


def test_trace_out_hash_truoc_khi_cat():
    """`out_hash` băm đầu ra ĐẦY ĐỦ, trước khi `max_output` cắt — hai lần gọi trả về cùng nội dung dài nhưng bị
    cắt khác nhau (ví dụ `max_output` khác nhau giữa hai bảng) vẫn phải ra cùng `out_hash`."""
    dai = "z" * 9_000
    tb_cat = _box(lambda s: dai, max_output=100)
    tb_full = _box(lambda s: dai, max_output=None)
    tb_cat.call(ToolCall(id="1", name="echo", args={"s": "x"}))
    tb_full.call(ToolCall(id="1", name="echo", args={"s": "x"}))
    assert tb_cat.calls[0]["chars"] != tb_full.calls[0]["chars"]  # bị cắt khác nhau thật
    assert tb_cat.calls[0]["out_hash"] == tb_full.calls[0]["out_hash"]  # nhưng băm trên nội dung gốc thì giống


def test_trace_clip_args_dai():
    """`trace(max_args=...)` cắt TỪNG giá trị tham số độc lập, không cắt cả dict đối số thành một chuỗi."""
    tb = _box()
    dai = "a" * 500
    tb.call(ToolCall(id="1", name="echo", args={"s": dai}))
    tr = tb.trace(max_args=50)
    assert len(tr[0]["args"]["s"]) == 51 and tr[0]["args"]["s"].endswith("…")
    tr_full = tb.trace(max_args=1000)
    assert tr_full[0]["args"]["s"] == dai


def test_root_va_sandbox_mac_dinh_rong():
    """Cả hai là *nhãn* cho audit và cho provider tự chạy tool; `None` nghĩa là 'bảng này không chạy lệnh nào',
    khác hẳn với chuỗi rỗng nghĩa là 'chạy ở đâu đó mà không ai ghi lại'."""
    tb = ToolBox()
    assert tb.root is None and tb.sandbox is None
    assert ToolBox(root="/w", sandbox="container:python:3.12-slim").sandbox == "container:python:3.12-slim"
