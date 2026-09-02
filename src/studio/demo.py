"""Demo offline: chạy cả phòng ban với client giả và media giả — từ brief kênh tới lên lịch đăng, dừng ở đúng
hai gate (plan, publish) và người duyệt bằng code. `PYTHONPATH=src python -m studio.demo`."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from .bus import InMemoryBus
from .events import Envelope
from .fakes import make_scripted_client
from .media import MediaConfig, make_media
from .orchestrator import Orchestrator


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    out = Path(tempfile.mkdtemp(prefix="studio-demo-"))
    bus = InMemoryBus()
    orch = Orchestrator(bus, make_scripted_client(plan_size=1, repairs=1), media=make_media(MediaConfig(output_dir=out)))
    bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human:owner", payload={
        "channel_id": "CH1", "goals": ["1000 sub trong 3 tháng"], "audience": "người mới làm YouTube", "pillars": ["hướng dẫn", "so sánh"],
        "cadence": "2 video/tuần", "boundaries": ["không hứa thu nhập", "không dùng nhạc chưa có license"], "language": "vi"}))
    def show(steps):
        for s in steps: print(f"  {s.topic:<22} {s.key:<10} {', '.join(s.actions) or s.deferred or '-'}")
    print("1) brief kênh → trend → kế hoạch → gate plan"); show(orch.run())
    print("   gate chờ:", list(orch.gate.pending))
    orch.gate.decide("PLAN-CH1-1", "approve", by="human:owner")
    print("2) duyệt plan → nghiên cứu → kịch bản → fact → sản xuất (render giả) → editor sửa 1 cảnh → chốt → review → gate publish")
    show(orch.run())
    print("   gate chờ:", list(orch.gate.pending), "| trạng thái:", orch.desk.state)
    orch.gate.decide("PUB-CH1-V1", "approve", by="human:editor")
    print("3) duyệt publish → publisher lên lịch"); show(orch.run())
    bus.publish(Envelope(topic="publish-events", key="CH1-V1", actor="human:publisher", payload={"video_id": "CH1-V1", "status": "published", "url": "https://youtu.be/abc"}))
    bus.publish(Envelope(topic="performance-snapshots", key="CH1-V1", actor="human:analytics", payload={
        "video_id": "CH1-V1", "channel_id": "CH1", "views": 1200, "impressions": 20000, "ctr": 0.06, "avg_view_duration_s": 7,
        "retention_curve": [{"t": 0, "pct": 100}, {"t": 3, "pct": 80}, {"t": 6, "pct": 62}, {"t": 9, "pct": 58}]}))
    print("4) số liệu thật → phân tích (điểm rơi map vào cảnh) → chiến lược"); show(orch.run())
    print("   trạng thái:", orch.desk.state)
    print("   asset:", sorted(p.name for p in (out / "CH1-V1").rglob("*") if p.is_file())[:8], "...")
    print("   báo cáo:", json.dumps(orch.supervisor.report()["videos"], ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
