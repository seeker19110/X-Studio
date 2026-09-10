"""Lớp span thuần stdlib (ADR-0009 `p3.1a`/`p3.1c`) và điểm ghép `ToolBox.call` (`p3.1b`).

Ca **chiều ngược** là phần đáng giá của file này: `sink=None` phải là no-op THẬT (quyết định 5 của ADR —
không cấp phát, không đọc đồng hồ), nên nó được chứng minh bằng cách cho `time.monotonic_ns` ném ngoại lệ chứ
không bằng cách đếm span rỗng: một `Span` được tạo rồi vứt đi vẫn cho ra "không có span" mà vẫn tốn đồng hồ.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any

import pytest

from xagents_core import observe
from xagents_core.observe import MemorySink, NullSink, Span, otel_sink, span, use_parent
from xagents_core.tools import ToolBox, ToolCall, ToolError, ToolSpec


def _box(sink: Any = None) -> ToolBox:
    b = ToolBox(sink=sink)
    b.add(ToolSpec(name="echo", description="", parameters={"properties": {"x": {"type": "string"}}, "required": ["x"]}),
          lambda x: f"đã nhận {x}")
    return b


# ---------- lớp span ----------

def test_span_do_duoc_thoi_gian():
    sink = MemorySink()
    with span("a", sink) as sp:
        assert sp is not None
        time.sleep(0.002)
    (got,) = sink.spans
    assert got is sp and got.end_ns > got.start_ns and got.duration_ms > 0
    assert got.error == "" and got.parent is None


def test_span_ghi_attrs_va_ca_khi_chua_dong():
    sink = MemorySink()
    with span("a", sink, tier="strong") as sp:
        assert sp is not None
        assert sp.duration_ms == 0.0, "chưa đóng thì end_ns = 0"
        sp.attrs["model"] = "m1"
    assert sink.spans[0].attrs == {"tier": "strong", "model": "m1"}


def test_span_ngoai_le_ghi_error_va_van_noi_len():
    sink = MemorySink()
    with pytest.raises(ValueError, match="hỏng"):
        with span("a", sink):
            raise ValueError("hỏng")
    (got,) = sink.spans
    assert got.error == "ValueError: hỏng" and got.end_ns > 0


def test_sink_none_la_no_op_that_su(monkeypatch):
    """Chiều ngược 1: không cấu hình sink thì `span()` KHÔNG đọc đồng hồ và KHÔNG cấp phát `Span`."""
    def no_clock() -> int:
        raise AssertionError("sink=None mà vẫn đọc đồng hồ")
    monkeypatch.setattr(observe.time, "monotonic_ns", no_clock)
    with span("a", None, x=1) as sp:
        assert sp is None


def test_null_sink_nuot_span():
    sink = NullSink()
    with span("a", sink) as sp:
        assert sp is not None
    assert sink.emit(Span(name="b", start_ns=1)) is None


def test_cay_long_nhau_cha_con():
    sink = MemorySink()
    with span("runner.step", sink) as top:
        with span("llm.complete", sink) as mid:
            with span("tool.call", sink) as leaf:
                pass
    assert leaf is not None and mid is not None
    assert leaf.parent is mid and mid.parent is top and top.parent is None
    assert [s.name for s in sink.spans] == ["tool.call", "llm.complete", "runner.step"], "con đóng trước cha"


def test_use_parent_gan_cha_cho_span_mo_sau():
    """Lời gọi tool chạy SAU khi `llm.complete` đã đóng (xem `_turns`), nên cha phải gắn tường minh."""
    sink = MemorySink()
    with span("runner.step", sink) as top:
        with span("llm.complete", sink) as turn:
            pass
        with use_parent(turn):
            with span("tool.call", sink) as leaf:
                pass
    assert leaf is not None and leaf.parent is turn and turn is not None and turn.parent is top


def test_use_parent_none_giu_nguyen_cha_hien_tai():
    sink = MemorySink()
    with span("runner.step", sink) as top:
        with use_parent(None):
            with span("tool.call", sink) as leaf:
                pass
    assert leaf is not None and leaf.parent is top


def test_song_song_hai_luong_khong_lan_cha():
    """Scheduler chạy `ThreadPoolExecutor`: span của ticket A không được nhận cha là span của ticket B."""
    sink = MemorySink()
    started = threading.Barrier(2)
    lock = threading.Lock()

    def ticket(name: str) -> None:
        with span(f"runner.step:{name}", sink):
            started.wait(timeout=5)
            with lock:
                with span(f"llm.complete:{name}", sink):
                    pass

    ts = [threading.Thread(target=ticket, args=(n,)) for n in ("A", "B")]
    for t in ts: t.start()
    for t in ts: t.join(timeout=5)
    kids = {s.name: s for s in sink.spans if s.name.startswith("llm")}
    assert len(kids) == 2
    for n in ("A", "B"):
        parent = kids[f"llm.complete:{n}"].parent
        assert parent is not None and parent.name == f"runner.step:{n}"


# ---------- sink OTel tuỳ chọn ----------

def test_otel_sink_thieu_goi_thi_lui_ve_null(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry", None)  # `from … import` trên None → ImportError
    assert isinstance(otel_sink(), NullSink)


class _FakeOtelSpan:
    def __init__(self, name: str, start_time: int) -> None:
        self.name, self.start_time, self.attrs, self.end_time = name, start_time, {}, 0

    def set_attribute(self, k: str, v: Any) -> None: self.attrs[k] = v

    def end(self, end_time: int) -> None: self.end_time = end_time


class _FakeTracer:
    def __init__(self) -> None: self.spans: list[_FakeOtelSpan] = []

    def start_span(self, name: str, start_time: int) -> _FakeOtelSpan:
        s = _FakeOtelSpan(name, start_time); self.spans.append(s); return s


def _fake_otel(monkeypatch) -> _FakeTracer:
    import types
    tracer = _FakeTracer()
    trace_mod = types.ModuleType("opentelemetry.trace")
    trace_mod.get_tracer = lambda name: tracer  # type: ignore[attr-defined]
    pkg = types.ModuleType("opentelemetry")
    pkg.trace = trace_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "opentelemetry", pkg)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_mod)
    return tracer


def test_otel_sink_co_goi_thi_phat_span(monkeypatch):
    tracer = _fake_otel(monkeypatch)
    sink = otel_sink()
    with span("llm.complete", sink, tier="strong") as sp:
        assert sp is not None
        time.sleep(0.002)
    (got,) = tracer.spans
    assert got.name == "llm.complete" and got.attrs == {"tier": "strong"}
    assert got.end_time > got.start_time and "error" not in got.attrs


def test_otel_sink_ghi_error_vao_attribute(monkeypatch):
    tracer = _fake_otel(monkeypatch)
    sink = otel_sink()
    with pytest.raises(ValueError):
        with span("llm.complete", sink):
            raise ValueError("vỡ")
    assert tracer.spans[0].attrs["error"] == "ValueError: vỡ"


# ---------- ghép vào ToolBox.call ----------

def test_toolbox_span_tool_call():
    sink = MemorySink()
    b = _box(sink)
    assert b.call(ToolCall(id="1", name="echo", args={"x": "a"})) == "đã nhận a"
    (got,) = sink.spans
    assert got.name == "tool.call" and got.attrs["tool"] == "echo" and got.attrs["ok"] is True
    assert got.attrs["chars"] == len("đã nhận a") and got.duration_ms >= 0


def test_toolbox_span_tool_khong_ton_tai_ghi_error_va_nem():
    sink = MemorySink()
    b = _box(sink)
    with pytest.raises(ToolError):
        b.call(ToolCall(id="1", name="khong-co", args={}))
    assert sink.spans[0].error.startswith("ToolError:")


def test_toolbox_calls_khong_doi_khi_co_span():
    """Chiều ngược: span không được lấy mất `ms`/`out_hash`/`args_hash` của `ToolBox.calls` (4L-2)."""
    keys = {"name", "args", "ok", "chars", "args_hash", "out_hash", "ms"}
    tc = ToolCall(id="1", name="echo", args={"x": "a"})
    plain = _box(); plain.call(tc)
    spanned = _box(MemorySink()); spanned.call(tc)
    assert set(plain.calls[0]) == set(spanned.calls[0]) == keys
    assert plain.trace()[0]["out_hash"] == spanned.trace()[0]["out_hash"]
    assert isinstance(spanned.calls[0]["ms"], float)


def test_toolbox_mac_dinh_khong_co_sink(monkeypatch):
    """Mặc định `ToolBox.sink is None` → không đọc đồng hồ span (đồng hồ của `ms` là `monotonic`, khác hàm)."""
    monkeypatch.setattr(observe.time, "monotonic_ns", lambda: (_ for _ in ()).throw(AssertionError("đọc đồng hồ")))
    assert _box().call(ToolCall(id="1", name="echo", args={"x": "a"})) == "đã nhận a"
