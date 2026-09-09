"""4L-7: `python -m studio.trace <VIDEO|PLAN-…|channel>` — dòng thời gian MỘT chủ thể, chỉ đọc.

Ca bắt buộc của gói: sáu pha khác nhau của một video phải in ra ĐÚNG sáu dòng, đúng thứ tự `bus.replay()`,
và dòng gate `PUB-…` phải hiện tên người ký (`by`) lẫn lý do (`reason`) — không có hai thứ đó thì người trực
vẫn phải mở SQLite ra tra tay, tức là lệnh này vô dụng đúng ở chỗ nó sinh ra để giải quyết.
"""
from __future__ import annotations

import json

import pytest

from studio import trace as TR
from studio.bus import InMemoryBus
from studio.events import Envelope


def _audit(bus: InMemoryBus, action: str, data: dict, actor: str = "orchestrator", **kw) -> None:
    bus.publish(Envelope(topic="audit-log", key=actor, actor=actor, payload={
        "actor": actor, "action": action, "evidence": json.dumps(data, ensure_ascii=False), **kw}))


def _sau_pha() -> InMemoryBus:
    """Sáu mốc của CH1-V1: brief → kịch bản → review → gate publish (người ký) → đăng → phân tích."""
    bus = InMemoryBus(enforce_owners=False)
    bus.publish(Envelope(topic="video-briefs", key="CH1-V1", actor="channel-strategist", payload={
        "video_id": "CH1-V1", "channel_id": "CH1", "working_title": "Bắt đầu kênh YouTube",
        "pillar": "hướng dẫn", "angle": "cho người mới", "audience": "người mới", "format": "long"}))
    bus.publish(Envelope(topic="scripts", key="CH1-V1", actor="script-writer", payload={
        "video_id": "CH1-V1", "working_title": "Bắt đầu kênh YouTube", "hook": "ba phút đầu",
        "sections": [{"heading": "mở", "narration": "xin chào"}], "cta": "đăng ký"}))
    bus.publish(Envelope(topic="review-results", key="CH1-V1", actor="quality-reviewer", payload={
        "video_id": "CH1-V1", "source": "quality", "verdict": "pass", "findings": []}))
    _audit(bus, "gate.decide", {"subject_id": "PUB-CH1-V1", "decision": "approve",
                                "by": "human:editor", "reason": "thumbnail và mô tả đã đúng"}, actor="human:editor")
    bus.publish(Envelope(topic="publish-events", key="CH1-V1", actor="publisher", payload={
        "video_id": "CH1-V1", "status": "published", "url": "https://youtu.be/abc"}))
    bus.publish(Envelope(topic="analytics-reports", key="CH1-V1", actor="analytics-analyst", payload={
        "channel_id": "CH1", "video_id": "CH1-V1", "recommendations": ["giữ chân tốt"]}))
    return bus


def _than(md: str) -> list[str]:
    """Phần thân của bản in: bỏ các dòng `#` và dòng trống ngăn cách."""
    return [ln for ln in md.splitlines() if ln.strip() and not ln.startswith("#")]


def test_trace_video_sau_pha_dung_sau_dong_dung_thu_tu():
    bus = _sau_pha()
    t = TR.trace(bus, "CH1-V1", agents={})
    assert t["kind"] == "video" and t["channel_id"] == "CH1" and t["videos"] == ["CH1-V1"]
    assert [r["topic"] for r in t["rows"]] == [
        "video-briefs", "scripts", "review-results", "audit-log", "publish-events", "analytics-reports"]
    body = _than(TR.render(t))
    assert len(body) == 6, f"phải đúng sáu dòng, có {len(body)}:\n" + "\n".join(body)
    gate_line = body[3]
    assert "PUB-CH1-V1" in gate_line and "human:editor" in gate_line, gate_line
    assert "thumbnail và mô tả đã đúng" in gate_line, "lý do người ký phải hiện ra"
    assert "analytics-reports" in body[5] and "published" in body[4]


def test_trace_theo_ke_hoach_va_kenh_gom_dung_video():
    """`PLAN-…` gom các video của kế hoạch; id kênh gom mọi video của kênh; video khác kênh không lẫn vào."""
    bus = _sau_pha()
    _audit(bus, "plan.proposed", {"plan_id": "PLAN-CH1-1", "channel_id": "CH1",
                                  "briefs": [{"video_id": "CH1-V1"}]}, channel_id="CH1")
    bus.publish(Envelope(topic="video-briefs", key="CH1-V2", actor="channel-strategist", payload={
        "video_id": "CH1-V2", "channel_id": "CH1", "working_title": "Video khác", "pillar": "so sánh",
        "angle": "a", "audience": "b", "format": "short"}))
    assert TR.trace(bus, "PLAN-CH1-1", agents={})["videos"] == ["CH1-V1"]
    assert TR.trace(bus, "CH1", agents={})["videos"] == ["CH1-V1", "CH1-V2"]
    # trace một video KHÔNG được kéo theo video anh em cùng kênh
    assert all("CH1-V2" not in str(r) for r in TR.trace(bus, "CH1-V1", agents={})["rows"])


def test_trace_gate_lay_kind_tu_request_va_bo_audit_noi_bo():
    bus = _sau_pha()
    _audit(bus, "gate.request", {"subject_id": "REP-CH1-V1-1", "kind": "replies", "created_by": "community-manager"},
           video_id="CH1-V1")
    _audit(bus, "gate.decide", {"subject_id": "REP-CH1-V1-1", "decision": "approve", "by": "human:cm"},
           actor="human:cm")
    _audit(bus, "once", {"key": "publish:CH1-V1:0"})       # việc nội bộ orchestrator: không vào dòng thời gian
    _audit(bus, "orchestrated", {"event_id": "x", "topic": "scripts"}, video_id="CH1-V1")
    t = TR.trace(bus, "CH1-V1", agents={})
    kinds = [r["gate"]["kind"] for r in t["rows"] if r["gate"]]
    assert kinds == [None, "replies", "replies"], kinds
    assert t["summary"]["gates_opened"] == 1 and t["summary"]["gates_decided"] == 2
    assert "once" not in [r["action"] for r in t["rows"]]


def test_trace_loi_va_media_va_render_khong_no():
    bus = _sau_pha()
    _audit(bus, "render_failed", {"error": "ffmpeg chết"}, video_id="CH1-V1")
    bus.publish(Envelope(topic="media-assets", key="CH1-V1", actor="renderer", payload={
        "video_id": "CH1-V1", "kind": "final_video", "path": "/tmp/CH1-V1/final.mp4",
        "provenance": {"generated_by": "fake:renderer"}}))
    t = TR.trace(bus, "CH1-V1", agents={})
    assert t["summary"]["errors"] == 1
    md = TR.render(t)
    assert "LỖI ffmpeg chết" in md and "final_video final.mp4" in md


def test_trace_id_la_thi_exit_1_khong_traceback(capsys):
    bus = _sau_pha()
    with pytest.raises(TR.TraceError, match="KHONG-CO"):
        TR.trace(bus, "KHONG-CO", agents={})
    assert TR.run(bus, "KHONG-CO", agents={}) == 1
    assert "KHONG-CO" in capsys.readouterr().err


def test_trace_cli_doc_only_tren_sqlite(tmp_path, capsys):
    """CLI mở DB ở chế độ CHỈ ĐỌC: không tạo file, không ghi thêm dòng nào vào bus đang chạy."""
    from studio.sqlite_bus import SQLiteBus
    db = tmp_path / "studio.sqlite"
    sq = SQLiteBus(db, enforce_owners=False)
    for e in _sau_pha().replay(): sq.publish(e)
    truoc = len(list(sq.replay()))
    assert TR.main(["CH1-V1", "--db", str(db), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "video" and len(out["rows"]) == 6
    assert TR.main(["CH1-V1", "--db", str(db)]) == 0
    assert len(list(SQLiteBus(db, enforce_owners=False).replay())) == truoc, "trace không được ghi gì"
    assert TR.main(["CH1-V1", "--db", str(tmp_path / "khong-co.sqlite")]) == 3
