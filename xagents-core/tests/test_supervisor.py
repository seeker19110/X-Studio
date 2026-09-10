"""`xagents_core.supervisor` — cơ chế chung của supervisor (K3.7).

Core KHÔNG mang `report()`/`sprint_report()` của ai: hai hàm ấy nói về hai miền khác nhau và ở lại từng công
ty. Ở đây chỉ có bốn cơ chế: `Budget`, `_act_once`, `escalate_gate`, `_count_debt`/`debt_table`.
"""
from __future__ import annotations

from conftest import FakeEnvelope
from xagents_core.supervisor import Budget, SupervisorBase, debt_ids


class Sup(SupervisorBase):
    """Công ty giả: `_act` chỉ ghi lại, không cần bus."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.acted: list[tuple[str, str, str]] = []

    def _act(self, target, action, reason, evidence=None):
        self.acted.append((target, action, reason))


def _review(payload, actor="qa", key="T1"):
    return FakeEnvelope(topic="ban-tin", key=key, actor=actor, payload=payload)


# ---------- Budget ----------

def test_budget_ratio_do_bang_token_dau_ra_con_ratio_usd_theo_tien():
    b = Budget(limit=100, used=900, output_used=80, limit_usd=2.0, cost_usd=1.0)
    assert b.ratio == 0.8 and b.ratio_usd == 0.5
    assert Budget(limit=0).ratio == 0.0 and Budget(limit=1).ratio_usd == 0.0


# ---------- _act_once ----------

def test_act_once_moi_dich_moi_hanh_dong_dung_mot_lan():
    s = Sup()
    s._act_once("V1", "warn", "80%"); s._act_once("V1", "warn", "80% nữa")
    s._act_once("V1", "budget_cut", "100%"); s._act_once("V2", "warn", "80%")
    assert s.acted == [("V1", "warn", "80%"), ("V1", "budget_cut", "100%"), ("V2", "warn", "80%")]


# ---------- escalate_gate ----------

def test_escalate_gate_chong_lap_theo_once_key():
    s = Sup()
    s.escalate_gate("G1", "quá hạn"); s.escalate_gate("G1", "quá hạn lần nữa")
    s.escalate_gate("G1", "thế hệ mới", once_key="gate:G1:2")
    s.escalate_gate("G2", "quá hạn")
    assert [a[0] for a in s.acted] == ["G1", "G1", "G2"]
    assert [a[2] for a in s.acted] == ["quá hạn", "thế hệ mới", "quá hạn"]


def test_escalate_once_tat_thi_moi_lan_goi_deu_phat():
    """Studio cố ý chống lặp ở khoá `once` BỀN của orchestrator: một cờ RAM ở đây nói sai sau mỗi lần mở lại bus."""
    class S(Sup): escalate_once = False
    s = S()
    s.escalate_gate("G1", "quá hạn"); s.escalate_gate("G1", "quá hạn")
    assert len(s.acted) == 2


# ---------- debt_ids / _count_debt / debt_table ----------

def test_debt_ids_bat_ca_ma_chuan_va_debt_viet_tu_do():
    assert debt_ids({"findings": [{"text": "xem DEF-01"}, {"text": "sd-3 chưa xử"}, "khong-phai-dict"],
                     "root_cause": "debt: schema.v2"}) == {"DEF-01", "SD-3", "SCHEMA.V2"}
    assert debt_ids({}) == set()


def test_count_debt_cham_nguong_thi_sinh_debt_due_va_dem_the_he():
    s = Sup(debt_threshold=2)
    for _ in range(4):
        s._count_debt(_review({"project_id": "P1", "ticket_id": "T1", "source": "qa", "findings": [{"text": "DEF-01"}]}))
    assert [d["times"] for d in s.debt_due] == [1, 2]
    assert s.debt_due[-1]["consecutive"] == 4 and s.debt_due[0]["hint"]


def test_khong_co_project_id_thi_bo_qua_tru_khi_hoc_duoc_tu_ticket():
    s = Sup(debt_threshold=1)
    s._count_debt(_review({"ticket_id": "T9", "findings": [{"text": "DEF-01"}]}))
    assert s.debt_due == []
    s.ticket_project["T9"] = "P1"
    s._count_debt(_review({"ticket_id": "T9", "findings": [{"text": "DEF-01"}]}))
    assert s.debt_due[0]["project_id"] == "P1"


def test_nguon_khong_nhac_lai_thi_chuoi_ve_khong():
    s = Sup(debt_threshold=2)
    s._count_debt(_review({"project_id": "P1", "source": "qa", "findings": [{"text": "DEF-01"}]}))
    s._count_debt(_review({"project_id": "P1", "source": "qa", "findings": []}))          # nợ đã trả
    s._count_debt(_review({"project_id": "P1", "source": "qa", "findings": [{"text": "DEF-01"}]}))
    assert s.debt_due == []   # chuỗi đứt trước khi chạm ngưỡng


def test_debt_table_loc_theo_du_an_va_dem_nguon(cfg):
    s = Sup(debt_threshold=3)
    s._count_debt(_review({"project_id": "P1", "ticket_id": "T1", "source": "qa", "findings": [{"text": "DEF-01"}]}))
    s._count_debt(_review({"project_id": "P1", "ticket_id": "T2", "source": "security", "findings": [{"text": "DEF-01"}]}))
    s._count_debt(_review({"project_id": "P2", "ticket_id": "T3", "source": "qa", "findings": [{"text": "SD-9"}]}))
    assert [r["debt_id"] for r in s.debt_table()] == ["DEF-01", "SD-9"]
    row = s.debt_table("P1")[0]
    assert row == {"project_id": "P1", "debt_id": "DEF-01", "mentions": 2, "consecutive": 1,
                   "tickets": ["T1", "T2"], "sources": ["qa", "security"], "escalated": 0, "threshold": 3}


def test_debt_threshold_khong_bao_gio_nho_hon_mot():
    assert Sup(debt_threshold=0).debt_threshold == 1
