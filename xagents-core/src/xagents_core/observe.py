"""Span quanh ba ranh giới có sẵn (ADR-0009): `runner.step`, `llm.complete`, `tool.call`.

**Vì sao module này tồn tại.** Repo có đúng một con số thời gian trong đường gọi model — `Generated.duration_ms`,
một số phẳng cho cả một bước agent có thể gồm 25 lượt `complete`, mỗi lượt m lời gọi tool. Nó không trả lời được
"bước 40 giây này chậm ở model hay ở tool". `trace.py` cũng không: nó dựng bảng từ bus SAU khi việc xong, mà thời
gian trôi bên trong một bước không sinh sự kiện nào. Span là cơ chế thứ hai, song song, đo TRONG lúc chạy —
không thay `trace.py` và không thay `duration_ms` (quyết định 1 của ADR).

**Vì sao thuần stdlib.** Core có đúng 3 dependency runtime và `strict = true` KHÔNG kèm cờ toàn cục
`--ignore-missing-imports`. Một `import opentelemetry` ở đỉnh file làm mypy đỏ trên mọi máy không cài gói, và
thêm OTel làm dep bắt buộc là nâng core lên 4 dep cho một tính năng mặc định TẮT. Nên core giữ *cơ chế*
(`Span` + `SpanSink`), còn sink cụ thể là *nghĩa* và thuộc nơi gọi — `otel_sink()` dưới đây import bên trong hàm
và lùi về `NullSink` khi thiếu gói (quyết định 4).

**Vì sao `sink=None` phải yield `None` chứ không phải một `Span` bị vứt đi.** Mặc định tắt là điều kiện để ghép
span vào đường nóng của cả sáu package mà không phải bọc mỗi chỗ ghép bằng một cờ cấu hình riêng (quyết định 5).
"Tắt" ở đây nghĩa là **không cấp phát, không đọc đồng hồ, không gọi sink** — nên chỗ gọi phải chịu được `None`
(`if sp is not None: sp.attrs[...] = ...`). Test `test_sink_none_la_no_op_that_su` chứng minh bằng cách cho
`time.monotonic_ns` ném ngoại lệ; một bản "vẫn tạo Span rồi bỏ" sẽ đỏ ở đó.

**Vì sao cha đi bằng `contextvars` chứ không bằng biến toàn cục.** Scheduler chạy `ThreadPoolExecutor`: hai
ticket song song với một biến toàn cục sẽ gán span của ticket A làm cha cho ticket B. `ContextVar` có ngữ cảnh
riêng cho mỗi luồng (và mỗi task asyncio), nên không phải khoá gì mà vẫn đúng.

`use_parent` là ngoại lệ có chủ ý của quy tắc "cha = span đang mở": trong `_turns`, tool của một lượt chạy SAU
khi `llm.complete` của lượt đó đã đóng (model trả `tool_calls` rồi runner mới gọi tool). Muốn cây khớp hình
runtime — "tool này thuộc lượt model nào" — thì phải gắn cha tường minh, chứ không kéo dài span `llm.complete`
cho tới hết vòng tool (làm thế thì latency của model không còn đọc được, đúng thứ span sinh ra để đo).
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# `__all__` là hợp đồng public của module (cùng khuôn `context.py`): thêm tên mà quên dòng này thì người gọi
# `from xagents_core.observe import *` không thấy nó, và AttributeError nổ ở chỗ khác hẳn nơi gây lỗi.
__all__ = ["MemorySink", "NullSink", "Span", "SpanSink", "otel_sink", "span", "use_parent"]


@dataclass
class Span:
    """Một khoảng thời gian có tên, có cha. `start_ns`/`end_ns` là đồng hồ ĐƠN ĐIỆU (`monotonic_ns`) — dùng để
    đo hiệu, không phải để biết "lúc mấy giờ"; sink nào cần mốc treo tường thì tự quy đổi (xem `_OtelSink`)."""
    name: str
    start_ns: int
    end_ns: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    parent: Span | None = None

    @property
    def duration_ms(self) -> float:
        """0.0 khi span chưa đóng — người đọc thấy "chưa xong", không thấy một số âm vô nghĩa."""
        return (self.end_ns - self.start_ns) / 1e6 if self.end_ns else 0.0


@runtime_checkable
class SpanSink(Protocol):
    def emit(self, span: Span) -> None: ...


class NullSink:
    """Sink không làm gì. Khác `sink=None` (không đo gì cả): ở đây span VẪN được đo và đóng, chỉ không đi đâu —
    dùng làm giá trị lùi của `otel_sink()` để chỗ gọi không phải phân biệt "có OTel" hay "không"."""

    def emit(self, span: Span) -> None:
        return None


@dataclass
class MemorySink:
    """Giữ span trong RAM theo thứ tự ĐÓNG (con trước cha). Cho test và cho đọc tại chỗ; không bền, không giới hạn
    kích thước — đừng cắm vào tiến trình chạy dài."""
    spans: list[Span] = field(default_factory=list)

    def emit(self, span: Span) -> None:
        self.spans.append(span)


_current: ContextVar[Span | None] = ContextVar("xagents_current_span", default=None)


@contextmanager
def span(name: str, sink: SpanSink | None, **attrs: Any) -> Iterator[Span | None]:
    """Mở một span con của span đang mở. `sink is None` → no-op thật sự, yield `None`.

    Ngoại lệ được ghi vào `.error` rồi **re-raise**: span là đo đạc, không phải xử lý lỗi — nuốt ngoại lệ ở đây
    sẽ biến một lượt hỏng thành một lượt "xong" ở mọi tầng trên."""
    if sink is None:
        yield None
        return
    sp = Span(name=name, start_ns=time.monotonic_ns(), attrs=dict(attrs), parent=_current.get())
    token = _current.set(sp)
    try:
        yield sp
    except BaseException as e:
        sp.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        _current.reset(token)
        sp.end_ns = time.monotonic_ns()
        sink.emit(sp)


@contextmanager
def use_parent(parent: Span | None) -> Iterator[None]:
    """Gắn `parent` làm cha cho các span mở trong khối — cho việc chạy SAU khi span cha đã đóng (vòng tool).
    `None` giữ nguyên cha hiện tại, để chỗ gọi không phải phân biệt "có bật span" hay không."""
    if parent is None:
        yield
        return
    token = _current.set(parent)
    try:
        yield
    finally:
        _current.reset(token)


class _OtelSink:
    """Phát span đã đóng sang OpenTelemetry. Span của ta đo bằng đồng hồ đơn điệu còn OTel muốn mốc epoch, nên
    mốc treo tường được suy ngược từ `time.time_ns()` tại lúc phát trừ đi độ dài — lệch đúng bằng chi phí của
    chính sink, nhỏ hơn nhiều so với sai số của việc lưu thêm một đồng hồ thứ hai vào mọi span."""

    def __init__(self, tracer: Any) -> None:
        self._tracer = tracer

    def emit(self, span: Span) -> None:
        end = time.time_ns()
        s = self._tracer.start_span(span.name, start_time=end - (span.end_ns - span.start_ns))
        for k, v in span.attrs.items():
            s.set_attribute(k, v)
        if span.error:
            s.set_attribute("error", span.error)
        s.end(end_time=end)


def otel_sink(tracer_name: str = "xagents") -> SpanSink:
    """Sink OpenTelemetry nếu người vận hành đã tự cài `opentelemetry-api`, `NullSink` nếu chưa.

    `import` nằm TRONG hàm và OTel không có trong bất kỳ `pyproject.toml` nào: đây là thứ người dùng cài, không
    phải dependency của repo (quyết định 4 của ADR-0009)."""
    try:
        # `type: ignore` chứ không phải một `[[tool.mypy.overrides]]` như `anthropic.*`: OTel không phải extra
        # của repo, nó là thứ NGƯỜI VẬN HÀNH tự cài, nên nó không có tên trong `pyproject.toml` nào cả. Kèm
        # `unused-ignore` vì `strict` bật `warn_unused_ignores`: máy CÓ cài OTel thì import hết thiếu, và một
        # ignore thừa sẽ làm mypy đỏ ở đúng máy đang chạy đường thật.
        from opentelemetry import trace as otel_trace  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return NullSink()
    return _OtelSink(otel_trace.get_tracer(tracer_name))
