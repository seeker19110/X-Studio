"""4L-7: hẹn hoãn của studio phải SỐNG SÓT khi mở lại tiến trình.

`_defer(..., wait_s=…)` ghi `defer.until` vào audit-log từ K3.3d, nhưng bản ghi ấy chỉ là một dòng log đẹp:
`deferred`/`defer_until` sống trong RAM, nên mở lại bus là `_rehydrate` đẩy thẳng event vào hàng đợi và nhịp
chạy đầu tiên gọi lại đúng cái backend vừa nói "thử lại sau 1800 giây". Company đã vá việc này ở
`company/orch/rehydrate.py:_nap_lai_hen`; đây là bản studio.

Bẫy TRAPS §1 khuôn 3: thế hệ của hẹn phải là `event_id` của CHÍNH event bị hoãn, không phải `video_id` —
video làm lại (rework/retry) sinh event mới và không được dính hẹn cũ của lần trước.
"""
from __future__ import annotations

import json
import time

from studio.events import Envelope
from studio.fakes import make_scripted_client
from studio.media import MediaConfig, make_media
from studio.orchestrator import Orchestrator, StepResult
from studio.sqlite_bus import SQLiteBus

BRIEF = {"video_id": "CH1-V1", "channel_id": "CH1", "working_title": "Bắt đầu kênh", "angle": "cho người mới",
         "pillar": "hướng dẫn", "audience": "người mới", "format": "long"}


def _orch(bus, tmp_path):
    return Orchestrator(bus, make_scripted_client(), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)


def test_hen_hoan_con_nguyen_sau_khi_mo_lai_tien_trinh(tmp_path):
    db = tmp_path / "studio.sqlite"
    bus = SQLiteBus(db)
    o = _orch(bus, tmp_path)
    env = Envelope(topic="video-briefs", key="CH1-V1", actor="channel-strategist", payload=BRIEF)
    bus.publish(env)
    o._defer(env, StepResult(env.event_id, env.topic, env.key), "transient:script-writer", wait_s=1800)
    con_lai = o.defer_until[env.event_id] - time.monotonic()
    assert 1700 < con_lai <= 1800
    del o, bus

    bus2 = SQLiteBus(db)                      # "tiến trình khác": bus mở lại từ đúng file ấy
    o2 = _orch(bus2, tmp_path)
    assert env.event_id in o2.deferred, "mở lại tiến trình là mất hẹn — đập thẳng vào backend đang cạn quota"
    assert o2.deferred[env.event_id][1] == "transient:script-writer"
    con_lai_2 = o2.defer_until[env.event_id] - time.monotonic()
    assert con_lai_2 >= con_lai - 5, f"hẹn bị đặt lại từ đầu: {con_lai_2} so với {con_lai}"
    assert con_lai_2 <= con_lai, "hẹn không được DÀI RA sau mỗi lần mở lại"
    assert env.event_id not in [e.event_id for e in o2.queue], "còn hẹn thì không được nằm trong hàng đợi chạy ngay"
    # và tới nhịp chạy nó vẫn im, vì hẹn chưa tới
    o2._retry_deferred(only="transient:")
    assert env.event_id in o2.deferred


def test_hen_da_qua_thi_chay_ngay_va_hen_hong_khong_lam_ket(tmp_path):
    db = tmp_path / "studio.sqlite"
    bus = SQLiteBus(db)
    o = _orch(bus, tmp_path)
    e1 = Envelope(topic="video-briefs", key="CH1-V1", actor="channel-strategist", payload=BRIEF)
    e2 = Envelope(topic="video-briefs", key="CH1-V2", actor="channel-strategist", payload={**BRIEF, "video_id": "CH1-V2"})
    bus.publish(e1); bus.publish(e2)
    o._defer(e1, StepResult(e1.event_id, e1.topic, e1.key), "transient:x", wait_s=1)
    # mốc của e2 bị hỏng (chuỗi không parse được thành thời điểm): thà chạy còn hơn kẹt vĩnh viễn
    bus.publish(Envelope(topic="audit-log", key="orchestrator", actor="orchestrator", payload={
        "actor": "orchestrator", "action": "defer.until", "video_id": "CH1-V2",
        "evidence": json.dumps({"event_id": e2.event_id, "reason": "transient:y", "wait_s": 1,
                                "until": "khong-phai-thoi-diem"})}))
    time.sleep(1.1)
    del o, bus

    o2 = _orch(SQLiteBus(db), tmp_path)
    ids = [e.event_id for e in o2.queue]
    assert e1.event_id in ids and e2.event_id in ids, "hẹn đã qua (hoặc hỏng) thì event phải chạy bình thường"
    assert o2.deferred == {}


def test_hen_cua_event_da_xong_khong_giu_lai_event_moi(tmp_path):
    """Khuôn 3: hẹn khoá theo `event_id`. Event đã `orchestrated` không nằm trong hàng đợi nên hẹn cũ vô hại,
    và một event MỚI của cùng video (làm lại) không bị hẹn cũ chặn."""
    db = tmp_path / "studio.sqlite"
    bus = SQLiteBus(db)
    o = _orch(bus, tmp_path)
    cu = Envelope(topic="video-briefs", key="CH1-V1", actor="channel-strategist", payload=BRIEF)
    bus.publish(cu)
    o._defer(cu, StepResult(cu.event_id, cu.topic, cu.key), "transient:x", wait_s=3600)
    o._audit("orchestrated", {"event_id": cu.event_id, "topic": cu.topic, "actions": []}, video_id="CH1-V1")
    moi = Envelope(topic="video-briefs", key="CH1-V1", actor="channel-strategist", payload={**BRIEF, "retry": 1})
    bus.publish(moi)
    del o, bus

    o2 = _orch(SQLiteBus(db), tmp_path)
    assert cu.event_id not in o2.deferred, "event đã xong không được nạp lại hẹn"
    assert moi.event_id in [e.event_id for e in o2.queue], "lần làm lại không được dính hẹn của lần trước"
