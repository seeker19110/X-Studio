"""`xagents_core.events` — khung event chung (K3.5a).

Chỉ canh phần KHUNG. Model miền (`Task` vs `VideoBrief`, `Topic` Literal, `PAYLOAD_MODELS`) ở lại package và
được canh ở đó; ca ở đây mà đụng tới chúng nghĩa là nghĩa đã rò lên core.

Ba nhóm ca:

1. `Envelope` — nhất là `child()` dùng `type(self)`, điều kiện để lớp con giữ được `topic` Literal của mình.
2. Lớp cơ sở đúng là CƠ SỞ: bốn class chỉ mang trường chung, không mang trường phạm vi của bên nào.
3. `can_transition` — cửa thoát `blocked`/`escalated` là tham số, không viết cứng tên của một công ty.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from xagents_core.events import (
    ALWAYS_REACHABLE,
    SCHEMA_VERSION,
    AuditLog,
    Envelope,
    SharedContext,
    SupervisorAction,
    can_transition,
)


def _env(**kw):
    return Envelope(**{"topic": "t", "key": "k", "actor": "a", "payload": {}, **kw})


# ---------- Envelope ----------

def test_event_khong_co_cha_la_goc_chuoi_cua_chinh_no():
    e = _env()
    assert e.correlation_id == e.event_id and e.causation_id is None
    assert e.schema_version == SCHEMA_VERSION


def test_child_ke_thua_correlation_va_tro_nguoc_ve_cha():
    goc = _env()
    con = goc.child(topic="t2", key="k", actor="b", payload={})
    chau = con.child(topic="t3", key="k", actor="c", payload={})
    assert con.correlation_id == goc.event_id and con.causation_id == goc.event_id
    assert chau.correlation_id == goc.event_id, "cả chuỗi cùng một correlation_id"
    assert chau.causation_id == con.event_id, "causation trỏ về cha TRỰC TIẾP, không phải gốc"


def test_child_dung_type_self_nen_lop_con_sinh_ra_lop_con():
    """Điều kiện để lớp con giữ được `topic` Literal. Viết cứng `Envelope(...)` trong `child()` thì mỗi event
    con lại tụt về lớp core và mất kiểm tra topic — thứ vừa được thêm vào ở lớp con."""
    class Hep(Envelope):
        pass

    e = Hep(topic="t", key="k", actor="a", payload={})
    assert type(e.child(topic="t2", key="k", actor="a", payload={})) is Hep


def test_envelope_cu_thieu_ba_truong_moi_van_nap_duoc():
    """Bus lưu envelope dạng JSON; bản ghi trước K3.5a không có ba trường nhân quả. Thiếu trường phải là
    DEFAULT, không phải lỗi — nếu không, mở lại một bus đang chạy là hỏng."""
    cu = '{"event_id":"abc","topic":"t","key":"k","actor":"a","ts":"2026-01-01T00:00:00Z","payload":{}}'
    e = Envelope.model_validate_json(cu)
    assert e.schema_version == SCHEMA_VERSION and e.correlation_id == "abc" and e.causation_id is None


def test_correlation_id_khai_tuong_minh_thi_khong_bi_ghi_de():
    e = _env(correlation_id="goc-cu")
    assert e.correlation_id == "goc-cu"


# ---------- lớp cơ sở phải là CƠ SỞ ----------

def test_khung_khong_mang_truong_pham_vi_cua_ben_nao():
    """`ticket_id` là của company, `video_id`/`channel_id` là của studio. Một cái lọt lên đây là core đã biết
    tên một công ty — đúng thứ nguyên tắc 1 cấm.

    **`project_id` là ngoại lệ, và chỉ trên `SharedContext`** (K3.6b). Trên `AuditLog` nó vẫn là trường phạm vi
    của company và vẫn bị cấm; trên `SharedContext` nó là *phân vùng của blackboard*, tức cơ chế — xem docstring
    `SharedContext`. Danh sách cấm vì thế theo TỪNG LỚP, không phải một tập chung: một tập chung sẽ hoặc cấm
    nhầm chỗ đúng, hoặc mở cả chỗ sai."""
    cam_chung = {"ticket_id", "video_id", "channel_id", "rulings", "output_tokens", "cost_usd", "phase"}
    for lop in (Envelope, SharedContext, AuditLog, SupervisorAction):
        assert not (set(lop.model_fields) & cam_chung), f"{lop.__name__} mang trường của một miền cụ thể"
    for lop in (Envelope, AuditLog, SupervisorAction):
        assert "project_id" not in lop.model_fields, f"{lop.__name__}: `project_id` ở đây là trường của company"


def test_topic_va_namespace_la_str_o_core():
    """`Topic`/`Namespace` là danh sách CỦA MỘT công ty. Ở core chúng phải mở, lớp con thu hẹp."""
    assert Envelope.model_fields["topic"].annotation is str
    assert SharedContext.model_fields["namespace"].annotation is str
    _env(topic="topic-nao-cung-duoc")   # core không phán xét topic


def test_khung_mang_dung_phan_chung():
    assert {"actor", "action", "evidence", "tokens"} == set(AuditLog.model_fields)
    # `project_id` + `content` lên core ở K3.6b cùng `blackboard.py`: chúng là hai cơ chế của blackboard
    # (phân vùng, toàn văn thay vì con trỏ), không phải trường của một miền. `rulings` vẫn ở company.
    assert {"namespace", "version", "content_ref", "summary", "project_id", "content"} == set(SharedContext.model_fields)
    assert {"target", "action", "reason", "evidence"} == set(SupervisorAction.model_fields)


def test_supervisor_action_chi_nhan_nam_hanh_dong():
    SupervisorAction(target="T1", action="pause", reason="r")
    with pytest.raises(ValidationError):
        SupervisorAction(target="T1", action="khong-ton-tai", reason="r")


# ---------- can_transition ----------

BANG = {"a": {"b"}, "b": {"c"}, "c": set()}


def test_di_theo_bang_va_chan_buoc_khong_co_trong_bang():
    assert can_transition("a", "b", BANG) and not can_transition("a", "c", BANG)
    assert not can_transition("khong-co-trong-bang", "b", BANG)


def test_cua_thoat_toi_duoc_tu_bat_ky_dau():
    """Cả hai công ty dựa vào điều này: một ticket/video ở bất kỳ trạng thái nào cũng phải chặn/escalate được.
    Quên vế này khi đưa hàm lên core làm 12 ca của company đỏ — đó là cách nó được phát hiện."""
    for tu in ("a", "b", "c", "khong-co-trong-bang"):
        assert can_transition(tu, "blocked", BANG) and can_transition(tu, "escalated", BANG), tu


def test_cua_thoat_la_THAM_SO_khong_viet_cung_ten_cua_mot_cong_ty():
    """Hai công ty hiện dùng chung hai tên, nhưng core không được BIẾT điều đó như một sự thật."""
    assert ALWAYS_REACHABLE == frozenset({"blocked", "escalated"})
    rieng = frozenset({"huy"})
    assert can_transition("a", "huy", BANG, rieng)
    assert not can_transition("a", "blocked", BANG, rieng), "đổi tham số thì cửa thoát cũ phải đóng"
