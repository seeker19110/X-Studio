"""K3.6d2: `AgentRunner` (phần ngoài) lên `xagents_core` — studio KHÔNG được đổi hành vi.

Hai ca dưới sinh ra vì một phép đo hai chiều: bật `wants_content` và ép `_new_envelope` dùng `inp.child()` ở
core làm **ba ca của core đỏ nhưng cả 557 ca của studio vẫn xanh**. Nghĩa là nếu ai đó "dọn" hai hook ấy đi
cho gọn, studio sẽ âm thầm ghi audit rác và đổi hình dạng event trên bus mà không có gì trong suite này đỏ.
"""
from __future__ import annotations

from studio.blackboard import Blackboard
from studio.bus import InMemoryBus
from studio.events import Envelope
from studio.llm import FakeClient
from studio.runner import AgentRunner

_BRIEF = {"channel_id": "CH1", "goals": ["g"], "audience": "người mới", "pillars": ["hướng dẫn"],
          "cadence": "2/tuần", "boundaries": ["không hứa thu nhập"]}


def _runner(bus, bb=None):
    return AgentRunner(bus, FakeClient(), blackboard=bb, toolbox_factory=lambda s: None)


def _inp():
    return Envelope(topic="channel-briefs", key="CH1", actor="human", payload=_BRIEF)


def test_ghi_context_KHONG_sinh_audit_context_no_content():
    """`context_writes` của studio không có `content` (prompt không hỏi). Bật `wants_content` cho studio là mỗi
    lần ghi context sinh một audit rác — đây là ca duy nhất trong suite này bắt được điều đó."""
    bus = InMemoryBus(); bb = Blackboard(bus)
    r = _runner(bus, bb)
    r.write_context("channel-strategist", _inp(), [{"namespace": "strategy", "content_ref": "s.md", "summary": "x"}])

    hanh_dong = [e.payload["action"] for e in bus.replay(topic="audit-log")]
    assert hanh_dong == ["context_written"], f"chỉ được ghi context_written, thực tế {hanh_dong}"
    assert bb.read("strategy").content is None, "studio không ghi toàn văn"


def test_event_dau_ra_KHONG_noi_chuoi_nhan_qua():
    """Studio dựng envelope MỚI, không `inp.child()`. Đổi sang `child()` là đổi nội dung event trên bus —
    thay đổi dữ liệu, không phải chuyển mã."""
    bus = InMemoryBus()
    inp = _inp()
    out = _runner(bus).publish("channel-strategist", inp, "video-briefs", {
        "video_id": "CH1-V1", "channel_id": "CH1", "working_title": "t", "pillar": "hướng dẫn", "angle": "a", "audience": "người mới",
        "format": "long", "goals": ["g"], "estimate_tokens": 60000, "budget_tokens": 100000})

    assert out.causation_id is None, "event studio không mang cha"
    assert out.correlation_id == out.event_id, "và là gốc chuỗi của chính nó"


def test_audit_van_mang_video_id_channel_id_cua_studio():
    """Trường PHẠM VI của studio đi qua hook `_audit_scope`; core không được biết tên chúng."""
    bus = InMemoryBus()
    _runner(bus)._audit(_runner(bus).agents["channel-strategist"], "thu", _inp(), evidence="e")
    (a,) = [e for e in bus.replay(topic="audit-log") if e.payload["action"] == "thu"]
    assert a.payload["channel_id"] == "CH1" and a.payload.get("video_id") is None
