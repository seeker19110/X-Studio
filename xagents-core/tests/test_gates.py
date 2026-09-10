"""`xagents_core.gates` — sổ gate chung (K3.7).

Bài học K3.3c2: mã ở core thì ca ở core. Công ty GIẢ ở đây không mượn `GateKind` của company hay studio —
`kind` chỉ là chuỗi, đúng như core nhìn thấy.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from xagents_core.gates import GateRequest, HumanGate, approvers

ENV = "FAKE_GATE_APPROVERS"


def _req(sid="G1", **kw):
    kw.setdefault("created_by", "human:default")
    return GateRequest(kind="duyet", subject_id=sid, checklist=["c1"], **kw)


# ---------- approvers(): env thắng cfg, "đặt nhưng rỗng" khác "không đặt" ----------

def test_approvers_doc_env_var_theo_ten_duoc_truyen(monkeypatch):
    monkeypatch.setenv(ENV, "human:a, human:b ,")
    assert approvers(ENV) == frozenset({"human:a", "human:b"})


def test_approvers_env_dat_nhung_rong_la_khong_gioi_han_va_bo_qua_cfg(monkeypatch):
    monkeypatch.setenv(ENV, "")
    assert approvers(ENV, SimpleNamespace(gate={"approvers": ["human:c"]})) == frozenset()


def test_approvers_khong_dat_env_thi_roi_xuong_cfg(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert approvers(ENV, SimpleNamespace(gate={"approvers": ["human:c", 7]})) == frozenset({"human:c", "7"})


def test_approvers_cfg_thieu_nut_thi_rong_chu_khong_no(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert approvers(ENV, None) == frozenset()
    assert approvers(ENV, object()) == frozenset()
    assert approvers(ENV, SimpleNamespace(gate={"approvers": None})) == frozenset()


def test_approvers_cfg_path_sau_hai_muc(monkeypatch):
    """`cfg_path` dài hơn 2 mức vẫn đi được — core không viết cứng đường dẫn của công ty nào."""
    monkeypatch.delenv(ENV, raising=False)
    cfg = SimpleNamespace(gate=SimpleNamespace(approvers={"nguoi": ["human:z"]}))
    assert approvers(ENV, cfg, ("gate", "approvers", "nguoi")) == frozenset({"human:z"})


# ---------- four-eyes + allowlist ----------

@pytest.mark.parametrize("created_by", [None, "", "   "])
def test_request_tu_choi_created_by_rong_hoac_none(created_by):
    """created_by rỗng/None làm ngắn mạch four-eyes ở decide() — request() phải chặn trước khi vào pending."""
    g = HumanGate()
    with pytest.raises(PermissionError):
        g.request(_req(created_by=created_by))
    assert g.pending == {}


def test_request_chap_nhan_created_by_hop_le():
    g = HumanGate()
    r = g.request(_req(created_by="human:a"))
    assert r.created_by == "human:a" and "G1" in g.pending


def test_decide_ghi_quyet_dinh_va_chuyen_sang_history():
    g = HumanGate()
    g.request(_req(created_by="human:a"))
    r = g.decide("G1", "approve", by="human:b", reason="ok")
    assert (r.decision, r.decided_by, r.reason) == ("approve", "human:b", "ok")
    assert g.pending == {} and g.is_approved("G1") and not g.is_approved("G2")


def test_nguoi_duyet_trung_nguoi_tao_bi_tu_choi():
    g = HumanGate()
    g.request(_req(created_by="human:a"))
    with pytest.raises(PermissionError, match="four-eyes"):
        g.decide("G1", "approve", by="human:a")


def test_allowlist_chan_nguoi_ngoai_danh_sach_va_enforce_false_thi_khong_kiem():
    g = HumanGate(approvers={"human:b"})
    g.request(_req(created_by="human:a"))
    with pytest.raises(PermissionError, match="human:x không nằm trong danh sách người duyệt"):
        g.decide("G1", "approve", by="human:x")
    g.decide("G1", "approve", by="human:x", enforce=False)  # replay lịch sử: không kiểm lại
    assert g.history[-1].decided_by == "human:x"


def test_allowlist_rong_thi_ai_cung_duyet_duoc():
    g = HumanGate()
    g.request(_req(created_by="human:a"))
    assert g.decide("G1", "approve", by="ai-do").decision == "approve"


# ---------- overdue()/due(): biên đúng bằng timeout KHÔNG phải quá hạn ----------

def test_overdue_va_due_theo_bien_timeout():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    g = HumanGate(timeout=timedelta(hours=24), remind_at=timedelta(hours=12))
    g.request(_req("MOI", created_at=now - timedelta(hours=1)))
    g.request(_req("NHAC", created_at=now - timedelta(hours=13)))
    g.request(_req("BIEN", created_at=now - timedelta(hours=24)))    # đúng bằng timeout: chưa quá hạn
    g.request(_req("QUA", created_at=now - timedelta(hours=25)))
    assert [r.subject_id for r in g.overdue(now)] == ["QUA"]
    assert g.due(now) == (["NHAC", "BIEN"], ["QUA"])


def test_overdue_khong_truyen_now_thi_lay_bay_gio():
    """`created_at` lùi 1 giây thay vì dựa vào thời gian trôi giữa hai dòng lệnh.

    Ca này từng ĐỎ trên Windows (2026-09-09): `datetime.now(UTC)` ở đó có bước ~15,6 ms, nên `request()` và
    `overdue()` trong cùng một tick đọc ra cùng một mốc và hiệu là `timedelta(0)` — không lớn hơn `timeout=0`.
    Không phải flake ngẫu nhiên: đỏ 5/5 lần lặp. Biên "đúng bằng timeout thì CHƯA quá hạn" là cố ý
    (`test_overdue_va_due_theo_bien_timeout`), nên chỗ phải sửa là ca này, không phải phép so sánh.

    Vẫn đo đúng thứ nó sinh ra để đo: `overdue()`/`due()` không nhận `now` thì phải tự đọc đồng hồ — mốc lùi 1
    giây chỉ quá hạn khi baseline là bây giờ."""
    g = HumanGate(timeout=timedelta(0))
    g.request(_req("X", created_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert [r.subject_id for r in g.overdue()] == ["X"]
    assert g.due()[1] == ["X"]


# ---------- seq: hai thế hệ gate của cùng subject phải phân biệt được ----------

def test_seq_tang_dan_va_khong_dua_vao_dong_ho():
    """Hai gate cho CÙNG `subject_id` mở liên tiếp phải mang hai `seq` khác nhau.

    Trước 2026-09-09 company phân biệt thế hệ bằng `created_at.isoformat(microseconds)`; trên Windows hai lần
    `request()` liên tiếp cho cùng một dấu thời gian, nên khoá `once` của lần quá hạn thứ hai trùng lần thứ
    nhất và bị nuốt — đúng lớp lỗi mà khoá ấy sinh ra để chặn. Bộ đếm không đọc đồng hồ nên không mắc."""
    g = HumanGate()
    a = g.request(_req("G1"))
    b = g.request(_req("G1"))   # thế hệ hai, ghi đè thế hệ một dưới cùng `subject_id`
    assert (a.seq, b.seq) == (1, 2), "hai thế hệ phải phân biệt được kể cả khi đồng hồ cho cùng một mốc"
    assert g.pending["G1"] is b, "gate mới ghi đè gate cũ dưới cùng `subject_id` — đó chính là lý do cần `seq`"


def test_seq_deterministic_khi_phat_lai_cung_thu_tu():
    """Phát lại cùng một log theo cùng thứ tự thì gate thứ ba vẫn là gate thứ ba.

    Đây là nửa thứ hai của lý do đổi sang bộ đếm: lúc rehydrate, gate được `request()` LẠI nên `created_at` là
    bây giờ chứ không phải mốc gốc — khoá `once` đổi sau mỗi restart, và một gate đã escalate lại escalate lần
    nữa. `seq` chỉ phụ thuộc thứ tự gọi, nên hai lần dựng cho cùng một dãy khoá."""
    def dung() -> list[int]:
        g = HumanGate()
        for sid in ("A", "B", "A", "C"):
            g.request(_req(sid))
        return [r.seq for r in (g.pending["A"], g.pending["B"], g.pending["C"])]

    assert dung() == dung() == [3, 2, 4]
