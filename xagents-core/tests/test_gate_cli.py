"""`xagents_core.gate_cli` — allowlist tin cậy + gate bền vững (K3.7).

Ca chốt của module này là ca CHỐNG MẠO DANH (`test_actor_gia_danh_bi_tu_choi*`): lỗ hổng 2026-09-09 đã phải vá
HAI lần ở hai công ty vì cùng một logic tồn tại hai bản. Nó về core thì ca cũng phải về core — nếu chỉ còn ở
suite của company/studio, một PR sau nới `trusted_decision` sẽ đỏ ở nơi khác chỗ nó sửa.
"""
from __future__ import annotations

import json

import pytest

from conftest import FakeEnvelope
from xagents_core.bus import InMemoryBus
from xagents_core.events import AuditLog
from xagents_core.gate_cli import SYSTEM_GATE_ACTOR, PersistentGate, trusted_decision
from xagents_core.gates import GateRequest


class Bus(InMemoryBus[FakeEnvelope]):
    envelope_cls = FakeEnvelope


def _audit(actor="human:b", action="gate.decide", topic="audit-log", **evidence):
    payload = AuditLog(actor=actor, action=action, evidence=json.dumps(evidence)).model_dump()
    return FakeEnvelope(topic=topic, key=actor, actor=actor, payload=payload)


def _decide_env(actor, **evidence):
    ev = {"subject_id": "G1", "decision": "approve", "by": "human:b", **evidence}
    return _audit(actor=actor, **ev)


def _gate(bus, **kw):
    return PersistentGate(bus, envelope_cls=FakeEnvelope, audit_cls=AuditLog, **kw)


# ---------- trusted_decision: ALLOWLIST mặc định từ chối ----------

def test_nguoi_ky_dung_chu_ky_cua_minh_thi_nhan():
    assert trusted_decision(_decide_env("human:b"))["by"] == "human:b"


def test_actor_gia_danh_bi_tu_choi():
    """Actor KHÔNG hình người mang `by="human:b"` giả: trước bản vá 2026-09-09 nó qua được."""
    for actor in ("orchestrator", "desk", "engineer", "chuoi-bat-ky"):
        assert trusted_decision(_decide_env(actor)) is None


def test_actor_nguoi_nhung_lech_by_bi_tu_choi():
    assert trusted_decision(_decide_env("human:a")) is None


def test_actor_he_thong_dong_gate_nghiem_thu_uat_thi_nhan():
    env = _decide_env(SYSTEM_GATE_ACTOR, subject_id="UAT-1", by="Khách A")
    assert trusted_decision(env)["subject_id"] == "UAT-1"
    assert trusted_decision(env, uat_prefix=None) is None          # công ty không có nghiệm thu: tắt hẳn nhánh
    assert trusted_decision(_decide_env(SYSTEM_GATE_ACTOR, subject_id="G1", by="Khách A")) is None


@pytest.mark.parametrize("env", [
    _audit(topic="shared-context", subject_id="G1"),                       # sai topic
    _audit(action="gate.request", subject_id="G1"),                        # sai action
    FakeEnvelope(topic="audit-log", key="k", actor="human:b",
                 payload=AuditLog(actor="human:b", action="gate.decide", evidence="{khong-phai-json").model_dump()),
    FakeEnvelope(topic="audit-log", key="k", actor="human:b",
                 payload=AuditLog(actor="human:b", action="gate.decide", evidence="[1,2]").model_dump()),
    _decide_env("human:b", subject_id=""),                                 # subject rỗng
    _decide_env("human:b", subject_id=7),                                  # subject không phải chuỗi
    _decide_env("human:b", by=""),                                         # `by` rỗng
    _decide_env("human:b", by=None),                                       # `by` không phải chuỗi
    _decide_env("human:b", decision=None),                                 # decision không phải chuỗi
])
def test_ban_ghi_di_thuong_deu_tra_none(env):
    assert trusted_decision(env) is None


# ---------- PersistentGate: ghi bus, dựng lại từ replay ----------

def test_request_ghi_len_bus_va_created_at_bang_ts_cua_envelope(cfg):
    bus = Bus(cfg); g = _gate(bus)
    r = g.request(GateRequest(kind="duyet", subject_id="G1", checklist=["c1"], created_by="human:a"))
    env = next(iter(bus.replay(topic="audit-log")))
    assert r.created_at == env.ts and json.loads(env.payload["evidence"])["kind"] == "duyet"


def test_gate_dung_lai_nguyen_ven_tu_replay_o_tien_trinh_khac(cfg):
    bus = Bus(cfg); g = _gate(bus)
    g.request(GateRequest(kind="duyet", subject_id="G1", checklist=["c1"], created_by="human:a"))
    g2 = _gate(bus)
    assert g2.pending["G1"].created_by == "human:a" and g2.pending["G1"].created_at == g.pending["G1"].created_at
    g3 = _gate(bus)  # áp lại lần nữa: idempotent, không nhân đôi
    assert list(g3.pending) == ["G1"]


def test_decide_dong_gate_va_ghi_audit(cfg):
    bus = Bus(cfg); g = _gate(bus)
    g.request(GateRequest(kind="duyet", subject_id="G1", checklist=["c1"], created_by="human:a"))
    g.decide("G1", "approve", by="human:b", reason="ok")
    env = list(bus.replay(topic="audit-log"))[-1]
    assert env.actor == "human:b" and json.loads(env.payload["evidence"])["decision"] == "approve"
    assert _gate(Bus(cfg) if False else bus).is_approved("G1")   # tiến trình khác đọc lại: đã duyệt


def test_decide_gate_nghiem_thu_ghi_duoi_actor_he_thong(cfg):
    """`by` là chữ ký khách (không phải id actor), nên envelope phải mang actor hệ thống — nếu ghi thẳng
    `actor="Khách A"` thì chính `trusted_decision` sẽ vứt bản ghi ấy khi tiến trình khác dựng lại gate."""
    bus = Bus(cfg); g = _gate(bus)
    g.request(GateRequest(kind="duyet", subject_id="UAT-1", checklist=["c1"], created_by="delivery-lead"))
    g.decide("UAT-1", "approve", by="Khách A")
    env = list(bus.replay(topic="audit-log"))[-1]
    assert env.actor == SYSTEM_GATE_ACTOR and env.payload["actor"] == "Khách A"
    assert _gate(bus).is_approved("UAT-1")


def test_decide_actor_truyen_tay_duoc_giu_nguyen(cfg):
    bus = Bus(cfg); g = _gate(bus)
    g.request(GateRequest(kind="duyet", subject_id="UAT-2", checklist=["c1"], created_by="delivery-lead"))
    g.decide("UAT-2", "approve", by="Khách A", actor=SYSTEM_GATE_ACTOR)
    assert list(bus.replay(topic="audit-log"))[-1].actor == SYSTEM_GATE_ACTOR


def test_quyet_dinh_mao_danh_khong_dong_duoc_gate_that(cfg):
    """Ca chốt lỗ hổng 2026-09-09 ở TẦNG GATE (không chỉ ở hàm thuần): một envelope `gate.decide` do actor
    không phải người phát ra vẫn đi qua bus (topic `audit-log` MỞ), nhưng không được làm gate biến mất."""
    bus = Bus(cfg); g = _gate(bus)
    g.request(GateRequest(kind="duyet", subject_id="G1", checklist=["c1"], created_by="human:a"))
    bus.publish(_decide_env("engineer"))
    assert "G1" in g.pending and not g.is_approved("G1")
    assert list(_gate(bus).pending) == ["G1"]   # kể cả khi dựng lại từ replay


def test_created_by_lay_tu_actor_that_khong_phai_evidence_tu_khai(cfg):
    """Ca chốt ADR-0002: `audit-log` MỞ, nên `created_by` trong evidence là LỜI KHAI của người ghi. Ghi
    `created_by="human:a"` rồi tự duyệt bằng chính mình sẽ vô hiệu hoá four-eyes; gate phải mang `env.actor`."""
    bus = Bus(cfg); g = _gate(bus)
    bus.publish(_audit(actor="human:evil", action="gate.request",
                       subject_id="G9", kind="duyet", checklist=["c1"], created_by="human:a"))
    assert g.pending["G9"].created_by == "human:evil"
    assert _gate(bus).pending["G9"].created_by == "human:evil"      # kể cả khi dựng lại từ replay
    with pytest.raises(PermissionError):
        g.decide("G9", "approve", by="human:evil")

# ---------- ADR-0008: allowlist vai được TẠO gate ----------

class Acl(PersistentGate[FakeEnvelope, AuditLog]):
    REQUEST_ACTORS = frozenset({"supervisor"})


def _acl(bus):
    return Acl(bus, envelope_cls=FakeEnvelope, audit_cls=AuditLog)


def test_vai_ngoai_allowlist_khong_tao_duoc_gate_o_chieu_ghi(cfg):
    """Chiều GHI ném lỗi chứ không im lặng: gate sống trong RAM rồi biến mất khi replay là khuôn lỗi cũ."""
    g = _acl(Bus(cfg))
    with pytest.raises(PermissionError):
        g.request(GateRequest(kind="duyet", subject_id="G1", checklist=["c1"], created_by="builder"))
    assert g.pending == {}


def test_vai_ngoai_allowlist_khong_tao_duoc_gate_o_chieu_replay(cfg):
    """Gate ma: `audit-log` MỞ nên envelope vẫn đi qua bus, nhưng không được vào sổ gate của ai."""
    bus = Bus(cfg); g = _acl(bus)
    bus.publish(_audit(actor="builder", action="gate.request", subject_id="MA-1", kind="duyet", checklist=["c1"]))
    assert g.pending == {} and _acl(bus).pending == {}


def test_nguoi_va_vai_trong_allowlist_van_tao_duoc_gate(cfg):
    bus = Bus(cfg); g = _acl(bus)
    g.request(GateRequest(kind="duyet", subject_id="G1", checklist=["c1"], created_by="supervisor"))
    g.request(GateRequest(kind="duyet", subject_id="G2", checklist=["c1"], created_by="human:pm"))
    assert sorted(_acl(bus).pending) == ["G1", "G2"]      # người không cần có tên trong REQUEST_ACTORS


def test_khong_dat_allowlist_thi_giu_hanh_vi_cu(cfg):
    bus = Bus(cfg); g = _gate(bus)                        # REQUEST_ACTORS = None
    g.request(GateRequest(kind="duyet", subject_id="G1", checklist=["c1"], created_by="builder"))
    assert list(_gate(bus).pending) == ["G1"]

def test_uat_prefix_none_thi_gate_khong_nhan_quyet_dinh_he_thong(cfg):
    class NoUat(PersistentGate[FakeEnvelope, AuditLog]):
        UAT_PREFIX = None
    bus = Bus(cfg); g = NoUat(bus, envelope_cls=FakeEnvelope, audit_cls=AuditLog)
    g.request(GateRequest(kind="duyet", subject_id="UAT-1", checklist=["c1"], created_by="human:a"))
    bus.publish(_decide_env(SYSTEM_GATE_ACTOR, subject_id="UAT-1", by="Khách A"))
    assert "UAT-1" in g.pending
    g.decide("UAT-1", "approve", by="Khách A")   # không có khái niệm nghiệm thu: actor = chính `by`
    assert list(bus.replay(topic="audit-log"))[-1].actor == "Khách A"


def test_apply_bo_qua_ban_ghi_di_thuong_chu_khong_no(cfg):
    bus = Bus(cfg); g = _gate(bus)
    g.request(GateRequest(kind="duyet", subject_id="G1", checklist=["c1"], created_by="human:a"))
    g.apply(FakeEnvelope(topic="noi-bo", key="k", actor="bien-tap", payload={}))   # topic khác: bỏ qua ngay
    for env in (_audit(action="san-xuat", subject_id="G1"),
                _audit(action="gate.request", evidence_hong=True),
                _audit(action="gate.request", subject_id="G2"),           # thiếu `kind`
                _audit(action="gate.request", subject_id="", kind="duyet"),
                _decide_env("human:b", subject_id="KHONG-CO")):
        bus.publish(env)
    bus.publish(FakeEnvelope(topic="audit-log", key="k", actor="human:b",
                             payload=AuditLog(actor="human:b", action="gate.request", evidence="{hong").model_dump()))
    bus.publish(FakeEnvelope(topic="audit-log", key="k", actor="human:b",
                             payload=AuditLog(actor="human:b", action="gate.request", evidence="[]").model_dump()))
    assert list(g.pending) == ["G1"] and g.history == []


def test_lop_con_them_truong_mien_qua_hai_hook(cfg):
    """`triggered_by` của studio đi qua `_request_kwargs`/`_request_payload`, không phải qua một `apply` chép lại."""
    from dataclasses import dataclass

    @dataclass
    class Req(GateRequest):
        triggered_by: str | None = None

    class G(PersistentGate[FakeEnvelope, AuditLog]):
        def _request_kwargs(self, d):
            return {**super()._request_kwargs(d), "triggered_by": d.get("triggered_by")}
        def _request_payload(self, req):
            return {**super()._request_payload(req), "triggered_by": req.triggered_by}

    bus = Bus(cfg)
    g = G(bus, envelope_cls=FakeEnvelope, audit_cls=AuditLog, request_cls=Req)
    g.request(Req(kind="duyet", subject_id="G1", checklist=[], created_by="human:a", triggered_by="human:z"))
    assert G(bus, envelope_cls=FakeEnvelope, audit_cls=AuditLog, request_cls=Req).pending["G1"].triggered_by == "human:z"
