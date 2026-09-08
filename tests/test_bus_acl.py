"""ACL topic của studio (K3.5b) — lớp kiểm quyền producer mà studio chưa từng chạy trước bước này.

Bảng ACL ở `studio/core.py` không suy ra được từ front matter `writes` của agent: quá nửa event của studio do
CODE phát. Nó **đo** từ event thật. Test ở đây giữ hai đầu của phép đo ấy: bảng đúng như đo được, và bus thật sự
chặn khi ai đó ra ngoài bảng.
"""
from __future__ import annotations

import json

import pytest

from studio.bus import HUMAN_TOPICS, OPEN_TOPICS, TOPIC_PRODUCERS, InMemoryBus, PermissionDenied, producer_allowed
from studio.core import CORE
from studio.events import Envelope
from studio.registry import load_agents

_BRIEF = {"video_id": "V1", "channel_id": "CH1", "working_title": "t", "pillar": "p",
          "angle": "a", "audience": "a", "estimate_tokens": 10}


def test_bang_acl_phu_dung_moi_topic_co_schema():
    """Mỗi topic có schema phải có đúng một ô trong bảng — không thừa (topic chết), không thiếu (ô rỗng ngầm).

    Thiếu một ô thì `producers.get(topic, frozenset())` trả tập rỗng và topic ấy bị chặn im lặng với MỌI actor;
    lỗi kiểu đó chỉ lộ ra lúc chạy thật, nên nó phải đỏ ở đây."""
    tren_dia = {p.stem for p in CORE.schema_dir.glob("*.json")}
    assert set(TOPIC_PRODUCERS) | OPEN_TOPICS == tren_dia


def test_producer_agent_la_tap_con_cua_front_matter_writes():
    """Agent chỉ được phát topic mình khai `writes`. Chiều ngược lại KHÔNG đúng và đó là điểm của cả bước này:
    bảng còn chứa `renderer`, `desk`, `orchestrator`, `adapter:youtube`, `chapters` — actor là CODE, không agent."""
    writes = {aid: set(a.writes) for aid, a in load_agents().items()}
    for topic, actors in TOPIC_PRODUCERS.items():
        for actor in actors:
            if actor in writes:
                assert topic in writes[actor], f"{actor} phát {topic} nhưng front matter không khai"
    khong_phai_agent = {a for acts in TOPIC_PRODUCERS.values() for a in acts} - set(writes)
    assert khong_phai_agent == {"renderer", "desk", "orchestrator", "adapter:youtube", "chapters"}


def test_human_topics_la_dung_MOT_bang_voi_orchestrator_publish():
    """Một nguồn sự thật, không phải hai bảng bằng nhau: `orchestrator publish` tái xuất chính bảng của ACL.

    `is` chứ không phải `==` — hai `frozenset` bằng nhau vẫn là hai chỗ phải sửa khi thêm một topic, và chỗ
    quên sửa sẽ nói "người không nạp tay được topic này" ở một tầng và "được" ở tầng kia."""
    from studio.orchestrator import HUMAN_TOPICS as CLI_TOPICS
    assert HUMAN_TOPICS is CLI_TOPICS


@pytest.mark.parametrize(("topic", "actor"), [
    ("scripts", "script-writer"), ("metadata-packages", "chapters"), ("metadata-packages", "seo-optimizer"),
    ("scene-manifests", "renderer"), ("video-briefs", "desk"), ("publish-events", "orchestrator"),
    ("audience-comments", "adapter:youtube"), ("channel-briefs", "human:owner"),
    ("performance-snapshots", "human:analytics"), ("publish-events", "human:publisher"),
    ("audit-log", "bat-ky-ai"), ("shared-context", "script-writer"),
])
def test_cap_do_duoc_tu_event_that_van_qua(topic, actor):
    assert producer_allowed(topic, actor)


@pytest.mark.parametrize(("topic", "actor"), [
    ("scripts", "editor"),                 # agent phát topic của agent khác
    ("video-briefs", "supervisor"),        # agent phát topic nó không khai `writes`
    ("video-briefs", "human"),             # người nạp tay topic ngoài `HUMAN_TOPICS`
    ("metadata-packages", "human:owner"),
    ("scripts", "x"),                      # actor không tồn tại
])
def test_cap_ngoai_bang_bi_chan(topic, actor):
    assert not producer_allowed(topic, actor)


def test_publish_vuot_quyen_nem_loi_va_ghi_audit():
    """Vượt quyền phải HIỆN RA: ném `PermissionDenied` và để lại một `publish_denied` do chính bus ghi.

    Ghi audit dưới `envelope_cls` của studio, không phải `Envelope` của core — nếu không, audit của bus tụt về
    lớp cơ sở và mất `topic: Topic` (cùng cái bẫy `child()` ở K3.5a)."""
    bus = InMemoryBus()
    with pytest.raises(PermissionDenied, match="không được phát topic video-briefs"):
        bus.publish(Envelope(topic="video-briefs", key="V1", actor="editor", payload=_BRIEF))
    denied = [e for e in bus.replay("audit-log") if e.payload["action"] == "publish_denied"]
    assert len(denied) == 1
    assert isinstance(denied[0], Envelope) and denied[0].actor == "bus"
    assert json.loads(denied[0].payload["evidence"])["actor"] == "editor"


def test_enforce_owners_tat_thi_khong_kiem_quyen_nhung_van_validate():
    """`enforce_owners=False` là cửa cho eval/replay, không phải cửa tắt validate."""
    bus = InMemoryBus(enforce_owners=False)
    bus.publish(Envelope(topic="video-briefs", key="V1", actor="editor", payload=_BRIEF))
    assert len(bus) == 1
    with pytest.raises(Exception, match="không hợp lệ"):
        bus.publish(Envelope(topic="video-briefs", key="V1", actor="editor", payload={"video_id": "V1"}))


def test_shared_context_van_kiem_theo_chu_namespace_chu_khong_theo_bang_topic():
    """`shared-context` ở `OPEN_TOPICS` nên bảng producer không nói gì về nó — quyền vẫn là của chủ namespace."""
    bus = InMemoryBus()
    ok = {"namespace": "voice", "key": "k", "version": 1, "content_ref": "ctx/voice/k.md", "video_id": "V1"}
    bus.publish(Envelope(topic="shared-context", key="voice/k", actor="script-writer", payload=ok))
    with pytest.raises(PermissionDenied, match="không được ghi namespace voice"):
        bus.publish(Envelope(topic="shared-context", key="voice/k", actor="editor", payload=ok))


def test_moi_schema_topic_biet_ba_truong_envelope_cua_K3_5a():
    """Schema envelope phải biết `schema_version`/`correlation_id`/`causation_id`.

    K3.5a thêm ba trường vào `Envelope` nhưng studio khi ấy KHÔNG validate envelope, nên 19 schema `topics/`
    với `additionalProperties: false` lặng lẽ lỗi thời — bật validate ở K3.5b làm 143 ca đỏ cùng lúc. Ca này
    biến "143 ca đỏ khó đọc" thành một câu, và bắt schema mới phải khai đủ ngay từ đầu."""
    thieu = {}
    for p in sorted(CORE.schema_dir.glob("*.json")):
        props = json.loads(p.read_text(encoding="utf-8"))["properties"]
        con = {"schema_version", "correlation_id", "causation_id"} - set(props)
        if con: thieu[p.stem] = sorted(con)
    assert thieu == {}
