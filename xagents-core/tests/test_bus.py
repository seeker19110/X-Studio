"""`xagents_core.bus` — cơ chế bus chung (K3.5b).

Công ty GIẢ mà mọi ca ở đây dùng (`cfg`, `FakeEnvelope`, `_tin`) nằm ở `conftest.py`. Bài học K3.3c2: mã ở
core thì ca ở core — nếu không, một PR sau gỡ mất thứ gì đó sẽ chỉ đỏ ở một suite và dễ đọc nhầm thành "lỗi
của công ty ấy".
"""
from __future__ import annotations

import json

import pytest

from conftest import FakeEnvelope, _tin
from xagents_core.bus import BusError, InMemoryBus, PermissionDenied, is_human, producer_allowed
from xagents_core.events import Envelope


class Bus(InMemoryBus[FakeEnvelope]):
    envelope_cls = FakeEnvelope


# ---------- ACL, tách khỏi bus để test được một mình ----------

@pytest.mark.parametrize(("actor", "kq"), [("human", True), ("human:sep", True), ("humanoid", False), ("bien-tap", False)])
def test_is_human_nhan_dung_human_va_human_hai_cham(actor, kq):
    assert is_human(actor) is kq


def test_producer_allowed_ba_nhanh(cfg):
    acl = cfg.topic_acl
    assert producer_allowed(acl, "audit-log", "ai-cung-duoc")     # open_topics: mọi actor
    assert producer_allowed(acl, "ban-tin", "human:sep")          # người: theo human_topics
    assert not producer_allowed(acl, "khong-co-bang", "human")    # người, topic ngoài human_topics
    assert producer_allowed(acl, "ban-tin", "bien-tap")           # agent: theo producers
    assert not producer_allowed(acl, "ban-tin", "nguoi-la")
    assert not producer_allowed(acl, "topic-khong-trong-bang", "bien-tap")  # ô thiếu = chặn, không phải mở


# ---------- validate ----------

def test_topic_khong_co_schema_bi_tu_choi_chu_khong_lot_qua(cfg):
    class B2(InMemoryBus[Envelope]):
        envelope_cls = Envelope
    bus = B2(cfg)
    with pytest.raises(BusError, match="không có schema cho topic la-hoac"):
        bus.validate("la-hoac", {})


def test_payload_sai_pydantic_va_sai_json_schema_deu_do(cfg):
    bus = Bus(cfg)
    with pytest.raises(BusError, match="payload không hợp lệ cho ban-tin"):
        bus.validate("ban-tin", {"tieu_de": 9})
    with pytest.raises(BusError, match="ban-tin không hợp lệ theo JSON Schema"):
        bus.validate("ban-tin", {"tieu_de": "t", "thua": 1})


def test_validate_envelope_kiem_ca_truong_ngoai_payload(cfg):
    """`validate` chỉ nhìn `payload`. Ca này canh lớp thứ hai: `schema_version`, `correlation_id`,
    `causation_id` — ba trường K3.5a thêm vào — chỉ có `validate_envelope` mới thấy được."""
    bus = Bus(cfg)
    bus.validate_envelope(_tin())
    with pytest.raises(BusError, match=r"ban-tin không hợp lệ theo JSON Schema: schema_version"):
        bus.validate_envelope(_tin(schema_version=0))   # schema đòi minimum 1



def test_nullable_fields_chi_liet_ke_truong_schema_cho_phep_null(cfg):
    bus = Bus(cfg)
    assert bus.nullable_fields("ban-tin") == frozenset({"ghi_chu"})
    assert bus.nullable_fields("khong-co") == frozenset()


# ---------- quyền ----------

def test_publish_chan_envelope_sai_o_tang_NGOAI_payload(cfg):
    """Canh chỗ `validate_envelope` được NỐI VÀO `publish`, không phải bản thân hàm ấy.

    Gỡ dòng `self.validate_envelope(env)` khỏi `_check_publish` mà không có ca này thì cả hai suite vẫn xanh —
    đo hai chiều đã bắt đúng lỗ đó. Và đây không phải rủi ro giả tưởng: chính lớp này làm 19 schema của studio
    đỏ ở K3.5b vì chúng chưa biết ba trường K3.5a thêm vào envelope."""
    bus = Bus(cfg)
    with pytest.raises(BusError, match="schema_version"):
        bus.publish(_tin(schema_version=0))
    assert len(bus) == 0


def test_publish_dung_quyen_thi_vao_log_va_bao_subscriber(cfg):
    bus = Bus(cfg); thay = []
    bus.subscribe("ban-tin", thay.append); bus.subscribe("*", thay.append)
    e = bus.publish(_tin())
    assert len(bus) == 1 and thay == [e, e] and list(bus.replay("ban-tin")) == [e]


def test_vuot_quyen_nem_loi_va_ghi_audit_dung_lop_con(cfg):
    bus = Bus(cfg)
    with pytest.raises(PermissionDenied, match="agent nguoi-la không được phát topic ban-tin"):
        bus.publish(_tin(actor="nguoi-la"))
    (d,) = [e for e in bus.replay("audit-log")]
    assert type(d) is FakeEnvelope and d.actor == "bus"
    assert json.loads(d.payload["evidence"]) == {"topic": "ban-tin", "key": "B1", "actor": "nguoi-la",
                                                 "reason": d.payload["evidence"] and json.loads(d.payload["evidence"])["reason"]}


def test_nguoi_vuot_quyen_duoc_goi_la_nguoi_chu_khong_phai_agent(cfg):
    bus = Bus(cfg)
    with pytest.raises(PermissionDenied, match="người human:sep không được phát topic noi-bo") as ex:
        bus.publish(FakeEnvelope(topic="noi-bo", key="k", actor="human:sep", payload={}))
    assert "ban-tin" in str(ex.value)  # gợi ý liệt kê human_topics, không phải producers


def test_shared_context_kiem_theo_chu_namespace(cfg):
    bus = Bus(cfg)
    bus.publish(FakeEnvelope(topic="shared-context", key="k", actor="bien-tap", payload={"namespace": "giong"}))
    with pytest.raises(PermissionDenied, match="nguoi-la không được ghi namespace giong"):
        bus.publish(FakeEnvelope(topic="shared-context", key="k", actor="nguoi-la", payload={"namespace": "giong"}))


def test_enforce_owners_tat_thi_bo_kiem_quyen_nhung_giu_validate(cfg):
    bus = Bus(cfg, enforce_owners=False)
    bus.publish(_tin(actor="nguoi-la"))
    assert len(bus) == 1
    with pytest.raises(BusError):
        bus.publish(FakeEnvelope(topic="ban-tin", key="B1", actor="nguoi-la", payload={}))


def test_extra_publish_checks_la_diem_mo_cho_luat_rieng_cua_mot_cong_ty(cfg):
    """Company có một luật không bảng nào suy ra được (`gate.decide` chỉ người ghi). Core không được biết tên
    topic của ai, nên luật ấy là một hook — ca này canh hook thật sự được gọi, và gọi TRƯỚC kiểm ACL."""
    thu_tu = []

    class B3(Bus):
        def _extra_publish_checks(self, env):
            thu_tu.append("extra")
            if env.payload.get("tieu_de") == "cam": raise PermissionDenied("luật riêng")

    bus = B3(cfg)
    with pytest.raises(PermissionDenied, match="luật riêng"):
        bus.publish(_tin(actor="nguoi-la", payload={"tieu_de": "cam"}))
    assert thu_tu == ["extra"]  # chạy trước ACL: ACL sẽ chặn `nguoi-la`, nhưng luật riêng chặn trước
    bus.publish(_tin()); assert thu_tu == ["extra", "extra"]


# ---------- subscriber hỏng ----------

def test_handler_nem_loi_van_bao_du_subscriber_con_lai_va_ghi_audit(cfg):
    bus = Bus(cfg); sau = []

    def hong(_e): raise RuntimeError("vỡ")
    bus.subscribe("ban-tin", hong); bus.subscribe("ban-tin", sau.append)
    with pytest.raises(RuntimeError, match="vỡ"):
        bus.publish(_tin())
    assert len(sau) == 1  # subscriber sau KHÔNG mất event dù cái trước ném
    (a,) = [e for e in bus.replay("audit-log") if e.payload["action"] == "subscriber_error"]
    assert json.loads(a.payload["evidence"])["error"] == "vỡ"


def test_audit_ve_handler_hong_khong_di_qua_chinh_handler_do(cfg):
    """`_persist_only`: nếu audit `subscriber_error` cũng đi qua subscriber, một handler hỏng đăng ký `*` sẽ
    ném lại khi nghe chính audit về mình — vòng lặp."""
    bus = Bus(cfg); goi = []

    def hong(e):
        goi.append(e.topic)
        raise RuntimeError("vỡ")
    bus.subscribe("*", hong)
    with pytest.raises(RuntimeError):
        bus.publish(_tin())
    assert goi == ["ban-tin"]  # KHÔNG có "audit-log"


# ---------- đọc lại ----------

def test_replay_loc_theo_topic_va_key_latest_lay_ban_moi_nhat(cfg):
    bus = Bus(cfg)
    a = bus.publish(_tin()); b = bus.publish(FakeEnvelope(topic="ban-tin", key="B2", actor="bien-tap", payload={"tieu_de": "u"}))
    c = bus.publish(_tin())
    assert list(bus.replay()) == [a, b, c]
    assert list(bus.replay(key="B1")) == [a, c]
    assert list(bus.replay(topic="ban-tin", key="B2")) == [b]
    assert bus.latest("ban-tin", "B1") is c and bus.latest("ban-tin", "B9") is None


def test_notify_bao_dung_subscriber_cua_topic_va_sao(cfg):
    """`_notify` báo một BẢNG subscriber truyền vào, không phải `self._subs` — `SQLiteBus` tháo bảng ra để ghi
    đĩa TRƯỚC khi báo (không mất event khi handler ném), rồi gọi lại đúng bảng ấy. Ca ở đây vì mã ở đây: K3.5c
    chuyển `sqlite_bus` lên core, nhưng nhánh này đã sống từ K3.5b."""
    bus = Bus(cfg); thay = []
    subs = {"ban-tin": [thay.append], "*": [thay.append], "khac": [lambda _e: thay.append("SAI")]}
    e = _tin()
    bus._notify(subs, e)
    assert thay == [e, e]


def test_nullable_fields_bo_qua_schema_kieu_boolean(cfg):
    """JSON Schema cho phép một property là `true`/`false` thay vì object — không phải dict thì bỏ qua.

    Không có vế `isinstance(spec, dict)` thì `spec.get("type")` là `AttributeError` ngay lúc nạp schema, tức
    là một schema hợp lệ theo chuẩn làm sập cả bus."""
    d = cfg.root / "topics" / "schemas"
    raw = json.loads((d / "noi-bo.json").read_text(encoding="utf-8"))
    raw["properties"]["payload"] = {"type": "object", "properties": {
        "gi_cung_duoc": True,                       # schema boolean, hợp lệ theo chuẩn
        "co_the_null": {"type": ["string", "null"]},
    }}
    (d / "noi-bo.json").write_text(json.dumps(raw), encoding="utf-8")

    assert Bus(cfg).nullable_fields("noi-bo") == frozenset({"co_the_null"})
