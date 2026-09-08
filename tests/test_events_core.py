"""K3.5a: khung event (`Envelope`, `AuditLog`, `SharedContext`, `SupervisorAction`) lên `xagents_core.events`.

Bước này rủi ro hơn K3.3/K3.4 ở một điểm mà hai bước kia không có: **nó đổi hình dạng của thứ đã nằm trên đĩa.**
`Envelope` của studio nhận thêm ba trường; bus SQLite lưu envelope dưới dạng JSON và đọc lại bằng
`model_validate_json`, nên câu hỏi phải trả lời bằng máy — không phải bằng suy luận — là: *một file bus ghi
TRƯỚC bước này có còn mở và replay được không?*

`test_bus_cu_van_mo_duoc` dựng đúng một file như thế. Nó KHÔNG dùng fixture nhị phân: `AGENTS.md` luật 3 cấm
commit `*.sqlite*` (và `.gitignore` chặn thật), nên "định dạng cũ" được viết thẳng bằng SQL + JSON — vừa không
có blob trong git, vừa đọc được bằng mắt thay vì phải mở bằng công cụ.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from studio.events import AuditLog, Envelope, SharedContext, SupervisorAction, can_transition
from studio.sqlite_bus import _DDL, SQLiteBus

# Đúng những trường mà `Envelope` của studio có TRƯỚC K3.5a — sáu, không hơn.
TRUONG_CU = ["event_id", "topic", "key", "actor", "ts", "payload"]


def _ghi_bus_dinh_dang_cu(path, so_event: int = 2) -> list[str]:
    """Ghi thẳng bằng sqlite3 + JSON, đúng như bản trước K3.5a từng ghi."""
    con = sqlite3.connect(path)
    con.executescript(_DDL)
    ids = []
    for i in range(so_event):
        eid = uuid4().hex
        ids.append(eid)
        body = {"event_id": eid, "topic": "channel-briefs", "key": "CH1", "actor": "human",
                "ts": datetime.now(UTC).isoformat(),
                "payload": {"channel_id": "CH1", "goals": [f"muc tieu {i}"], "audience": "người mới",
                            "pillars": ["hướng dẫn"], "cadence": "2/tuần", "boundaries": ["không hứa thu nhập"]}}
        assert sorted(body) == sorted(TRUONG_CU), "fixture phải đúng hình dạng CŨ, không được lẫn trường mới"
        con.execute("INSERT INTO events(event_id, topic, key, actor, ts, body) VALUES (?,?,?,?,?,?)",
                    (eid, body["topic"], body["key"], body["actor"], body["ts"],
                     json.dumps(body, ensure_ascii=False)))
    con.commit(); con.close()
    return ids


def test_bus_cu_van_mo_duoc(tmp_path):
    """Ca đắt nhất của K3.5a. Một file bus ghi trước bước này phải mở được, replay đủ event, và ba trường mới
    nhận default hợp lý — `correlation_id` lùi về chính `event_id` (event không có cha là gốc chuỗi của nó)."""
    db = tmp_path / "studio-0.1.0.sqlite"
    ids = _ghi_bus_dinh_dang_cu(db)

    evs = list(SQLiteBus(db).replay())

    assert [e.event_id for e in evs] == ids, "replay phải đủ và đúng thứ tự"
    for e in evs:
        assert e.schema_version == 1
        assert e.correlation_id == e.event_id, "event cũ không có cha → nó là gốc chuỗi của chính nó"
        assert e.causation_id is None


def test_bus_cu_ghi_tiep_duoc_bang_ban_moi(tmp_path):
    """Mở file cũ rồi ghi tiếp: event mới mang đủ trường nhân quả, event cũ vẫn nguyên. Đây là hình dạng THẬT
    khi nâng cấp một máy đang chạy — không ai xoá bus rồi bắt đầu lại."""
    db = tmp_path / "studio-0.1.0.sqlite"
    cu = _ghi_bus_dinh_dang_cu(db, so_event=1)

    bus = SQLiteBus(db)
    goc = next(iter(bus.replay()))
    moi = bus.publish(goc.child(topic="trend-reports", key="CH1", actor="trend-researcher",
                                payload={"channel_id": "CH1", "sources": ["https://a.example.org"],
                                         "trends": [{"topic": "t", "momentum": "rising", "evidence": "e"}]}))
    bus.close()

    evs = list(SQLiteBus(db).replay())
    assert [e.event_id for e in evs] == [*cu, moi.event_id]
    assert evs[1].causation_id == cu[0] and evs[1].correlation_id == cu[0], "chuỗi nhân quả nối từ event CŨ"
    assert type(evs[1]) is Envelope and evs[0].correlation_id == cu[0]


# ---------- lớp con phải là LỚP CON, không phải bản sao ----------

def test_envelope_ke_thua_core_va_chi_thu_hep_topic():
    """Nếu ai đó chép `Envelope` về lại studio thay vì kế thừa, ca này đỏ — và bản fork thứ hai lại mọc lên."""
    from xagents_core.events import Envelope as CoreEnvelope

    assert issubclass(Envelope, CoreEnvelope)
    them = set(Envelope.model_fields) - set(CoreEnvelope.model_fields)
    assert them == set(), "studio KHÔNG thêm trường nào vào Envelope, chỉ thu hẹp `topic`"
    assert Envelope.model_fields["topic"].annotation is not str, "`topic` phải hẹp về Literal của phòng ban"


def test_topic_la_khong_qua_duoc():
    """Thu hẹp `topic` xuống lớp con không được làm mất kiểm tra — đó là điều kiện để đưa `topic: str` lên core."""
    with pytest.raises(ValidationError):
        Envelope(topic="khong-ton-tai", key="k", actor="a", payload={})


def test_child_tra_ve_LOP_CON_khong_phai_lop_core():
    """`child()` ở core dùng `type(self)`. Viết cứng tên lớp ở đó thì mỗi event con trong studio lại là một
    `Envelope` core không có `topic: Topic` — mất kiểm tra topic ở đúng chỗ vừa thêm nó vào."""
    e = Envelope(topic="channel-briefs", key="CH1", actor="human", payload={})
    c = e.child(topic="trend-reports", key="CH1", actor="trend-researcher", payload={})
    assert type(c) is Envelope
    with pytest.raises(ValidationError):
        e.child(topic="khong-ton-tai", key="CH1", actor="a", payload={})


def test_truong_pham_vi_cua_phong_ban_o_LOP_CON():
    """`video_id`/`channel_id` là tên gọi của MIỀN, không phải cơ chế — chúng ở lớp con. Company có
    `ticket_id`/`project_id` ở đúng chỗ này; đưa cả bốn lên core là bắt mỗi bên mang trường của bên kia."""
    from xagents_core.events import AuditLog as CoreAuditLog

    assert issubclass(AuditLog, CoreAuditLog)
    them = set(AuditLog.model_fields) - set(CoreAuditLog.model_fields)
    assert them == {"video_id", "channel_id"}
    assert {"actor", "action", "evidence", "tokens"} <= set(CoreAuditLog.model_fields)


def test_shared_context_va_supervisor_action_ke_thua():
    from xagents_core.events import SharedContext as CoreSharedContext
    from xagents_core.events import SupervisorAction as CoreSupervisorAction

    assert issubclass(SharedContext, CoreSharedContext)
    assert set(SharedContext.model_fields) - set(CoreSharedContext.model_fields) == set()
    assert issubclass(SupervisorAction, CoreSupervisorAction)
    assert set(SupervisorAction.model_fields) - set(CoreSupervisorAction.model_fields) == set()


# ---------- cửa thoát của máy trạng thái ----------

def test_can_transition_giu_cua_thoat_blocked_va_escalated():
    """Cơ chế ở core nhưng bảng là của phòng ban. Cửa thoát PHẢI giữ: một video đang ở bất kỳ đâu cũng phải
    chặn hoặc escalate được — bỏ nó là một lượt hỏng giữa dây chuyền không còn đường dừng."""
    assert can_transition("briefed", "researched") and not can_transition("briefed", "published")
    for tu in ("briefed", "scripted", "in_production", "in_review", "scheduled", "published"):
        assert can_transition(tu, "blocked") and can_transition(tu, "escalated"), tu
