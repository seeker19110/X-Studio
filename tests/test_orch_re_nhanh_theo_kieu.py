"""Rẽ nhánh theo KIỂU đã validate, không theo chuỗi trên dict thô (4L-6a).

`_rework` và `_publish_video` là hai chỗ orchestrator quyết định dựa trên nội dung event: một chỗ quyết định làm
lại video, một chỗ quyết định chạm nền tảng thật. Trước bản này cả hai đọc `env.payload.get(...)` trần: payload sai
hình thì hoặc `KeyError` giữa vòng lặp, hoặc rơi vào nhánh sai mà không ai thấy. Nay: `ReviewResult` /
`PublishEvent` được `model_validate` TRƯỚC khi rẽ nhánh, và `ValidationError` không bị nuốt — audit
`agent_error_unhandled` + gate escalation, đúng khuôn "mọi lỗi agent phải có người nhận" của software-company.
"""
from __future__ import annotations

import json

from studio.bus import InMemoryBus
from studio.events import AuditLog, Envelope
from studio.fakes import make_scripted_client
from studio.media import MediaConfig, make_media
from studio.orchestrator import Orchestrator, StepResult

CHANNEL = {"channel_id": "CH1", "goals": ["1000 sub"], "audience": "người mới", "pillars": ["hướng dẫn"],
           "cadence": "2/tuần", "boundaries": ["không hứa thu nhập"]}


def _orch(bus, tmp_path, **opts):
    return Orchestrator(bus, make_scripted_client(**opts), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)


def _audits(bus, action):
    return [json.loads(e.payload["evidence"] or "{}") for e in bus.replay("audit-log")
            if e.actor == "orchestrator" and e.payload["action"] == action]


def _to_publish_gate(tmp_path, **opts):
    bus = InMemoryBus(); o = _orch(bus, tmp_path, plan_size=1, **opts)
    bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human", payload=CHANNEL)); o.run()
    o.gate.decide("PLAN-CH1-1", "approve", by="human:owner"); o.run()
    assert "PUB-CH1-V1" in o.gate.pending and o.platform.calls == []
    return bus, o


def _to_brief(tmp_path):
    """Dừng ngay sau gate plan approve: có brief `CH1-V1` trên desk để `_rework` có chỗ bám."""
    bus, o = _to_publish_gate(tmp_path)
    return bus, o, "CH1-V1"


# ---------- _rework: validate ReviewResult trước khi đọc `source` ----------

def test_rework_source_fact_van_rework_stage_script_sau_khi_validate(tmp_path):
    """Chiều ngược (payload hợp lệ): review-results `source=fact`, `verdict=block` vẫn làm lại ở stage `script`
    — nhánh này là hành vi lõi đã có test end-to-end; bản 4L-6a chỉ đổi chỗ ĐỌC (model đã validate thay cho dict
    thô), không đổi kết quả. `source` đi vào hint phải là Literal đã validate."""
    bus, o, vid = _to_brief(tmp_path)
    retry_truoc = o.desk.briefs[vid].retry
    env = Envelope(topic="review-results", key=vid, actor="fact-checker",
                   payload={"video_id": vid, "source": "fact", "verdict": "block",
                            "findings": [{"level": "block", "text": "C1 không nguồn"}]})
    res = StepResult(env.event_id, env.topic, env.key)
    o._rework(env, res)
    assert res.actions == ["rework"] and o.desk.briefs[vid].retry == retry_truoc + 1
    brief = [e.payload for e in bus.replay("video-briefs", vid)][-1]
    assert brief["hint"].startswith("[script] fact: ") and "C1 không nguồn" in brief["hint"]


def test_rework_payload_sai_hinh_nem_validation_error_thanh_agent_error_unhandled(tmp_path):
    """`verdict` ngoài Literal → không được rẽ nhánh mò, không được nuốt: audit `agent_error_unhandled` + gate
    escalation, và KHÔNG làm lại video (một payload hỏng không được tiêu một lượt retry)."""
    bus, o, vid = _to_brief(tmp_path)
    retry_truoc = o.desk.briefs[vid].retry
    env = Envelope(topic="review-results", key=vid, actor="fact-checker",
                   payload={"video_id": vid, "source": "fact", "verdict": "khong-hop-le"})
    res = StepResult(env.event_id, env.topic, env.key)
    o._rework(env, res)
    recs = _audits(bus, "agent_error_unhandled")
    assert [r["subject"] for r in recs] == [vid] and recs[0]["model"] == "ReviewResult" and recs[0]["topic"] == "review-results"
    assert o.desk.briefs[vid].retry == retry_truoc  # không rework
    assert f"ESC-{vid}" in o.gate.pending and any(a.startswith("unhandled:") for a in res.actions)


def test_rework_validate_hong_hai_lan_hai_the_he_khong_bi_khoa_once(tmp_path):
    """Khoá chống lặp mang thế hệ (video_id + retry): payload hỏng lần sau ở thế hệ khác vẫn phải mở gate lại,
    không bị một khoá tĩnh chặn vĩnh viễn (TRAPS khuôn 3)."""
    bus, o, vid = _to_brief(tmp_path)
    xau = {"video_id": vid, "source": "fact", "verdict": "khong-hop-le"}
    for _ in range(2):
        env = Envelope(topic="review-results", key=vid, actor="fact-checker", payload=dict(xau))
        o._rework(env, StepResult(env.event_id, env.topic, env.key))
    assert len(_audits(bus, "agent_error_unhandled")) == 2
    o.gate.decide(f"ESC-{vid}", "approve", by="human:owner", reason="mở lại sau khi sửa fact-checker")  # sang thế hệ mới
    o.run()
    env = Envelope(topic="review-results", key=vid, actor="fact-checker", payload=dict(xau))
    res = StepResult(env.event_id, env.topic, env.key)
    o._rework(env, res)
    assert f"ESC-{vid}" in o.gate.pending, "thế hệ mới hỏng lại thì phải có gate mới, không bị khoá cũ nuốt"


# ---------- _publish_video: validate PublishEvent trước khi chạm adapter ----------

def test_publish_hop_le_van_upload_binh_thuong(tmp_path):
    """Chiều ngược: payload publisher hợp lệ (`scheduled`) vẫn đi trọn adapter như trước."""
    bus, o = _to_publish_gate(tmp_path)
    o.gate.decide("PUB-CH1-V1", "approve", by="human:editor"); o.run()
    assert [c[0] for c in o.platform.calls] == ["upload_video", "set_thumbnail", "schedule", "upload_captions"]
    assert [e.payload for e in bus.replay("publish-events", "CH1-V1")][-1]["status"] == "scheduled"
    assert not _audits(bus, "agent_error_unhandled")


def test_publish_status_live_khong_goi_lai_platform(tmp_path):
    """Video đã lên sóng thật (`publish-events` gần nhất `status=published` kèm `platform_ref`): duyệt gate lần
    nữa KHÔNG được chạm adapter — gọi lại là double-publish, thứ ADR-0002 cấm cả ở chiều ngược lại."""
    bus, o = _to_publish_gate(tmp_path)
    bus.publish(Envelope(topic="publish-events", key="CH1-V1", actor="human:publisher",
                         payload={"video_id": "CH1-V1", "kind": "video", "status": "published",
                                  "platform_ref": "yt-live-1", "url": "https://y/1", "evidence": "người đăng tay"}))
    o.run()
    truoc = len(list(bus.replay("publish-events", "CH1-V1")))
    o.gate.decide("PUB-CH1-V1", "approve", by="human:editor")
    res = o.run()
    assert o.platform.calls == [], "đã live rồi thì không adapter nào được gọi thêm"
    live = _audits(bus, "platform.skipped_live")
    assert live and live[0]["platform_ref"] == "yt-live-1" and live[0]["approved_by"] == "human:editor"
    assert any("platform:skip_live:yt-live-1" in r.actions for r in res)
    assert len(list(bus.replay("publish-events", "CH1-V1"))) == truoc, "không phát bản sao publish-events"
    assert "orchestrator_failed" not in [e.payload["action"] for e in bus.replay("audit-log")]


def test_publish_payload_sai_hinh_thi_gate(tmp_path):
    """Quyết định của publisher sai hình (vd. `status="live"` — giá trị KHÔNG có trong `PublishEvent`): dừng
    TRƯỚC adapter, audit `agent_error_unhandled`, mở gate escalation. Không đoán ý model, không chạm nền tảng.

    Ở luồng thật `AgentRunner.generate` đã `bus.validate` đầu ra nên payload hỏng bị chặn sớm hơn; chốt chặn ở
    đây là lớp hai, và chỉ gọi thẳng `_publish_video` mới dựng lại được ca đó (giả `_decide`)."""
    bus, o = _to_publish_gate(tmp_path)
    o._decide = lambda r, env, res, extra: ({"video_id": "CH1-V1", "status": "live"}, None)  # type: ignore[method-assign]
    o.gate.decide("PUB-CH1-V1", "approve", by="human:editor"); o.run()
    assert o.platform.calls == []
    recs = _audits(bus, "agent_error_unhandled")
    assert recs and recs[0]["model"] == "PublishEvent" and recs[0]["subject"] == "CH1-V1"
    assert "ESC-CH1-V1" in o.gate.pending and not list(bus.replay("publish-events", "CH1-V1"))


def test_publish_event_cu_hong_thanh_gate_chu_khong_crash_vong_lap(tmp_path):
    """Dữ liệu `publish-events` cũ (trước khi bus validate chặt) không đọc được: `ValidationError` phải thành
    gate escalation, không được nuốt và cũng không được ném vỡ vòng lặp orchestrator."""
    bus, o = _to_publish_gate(tmp_path)
    e = bus.publish(Envelope(topic="publish-events", key="CH1-V1", actor="human:publisher",
                             payload={"video_id": "CH1-V1", "kind": "video", "status": "published", "platform_ref": "yt-1"}))
    o.run()
    e.payload["status"] = "len-song"  # giả lập bản ghi cũ nằm sẵn trong bus
    o.gate.decide("PUB-CH1-V1", "approve", by="human:editor"); o.run()
    assert o.platform.calls == []
    recs = _audits(bus, "agent_error_unhandled")
    assert recs and recs[0]["model"] == "PublishEvent" and recs[0]["topic"] == "publish-events"
    assert "ESC-CH1-V1" in o.gate.pending


def test_publish_events_kind_reply_khong_bi_coi_la_video_da_live(tmp_path):
    """`publish-events` dùng chung cho trả lời bình luận: một reply `published` KHÔNG phải video đã lên sóng —
    coi nhầm là bịt luôn đường đăng video."""
    bus, o = _to_publish_gate(tmp_path)
    bus.publish(Envelope(topic="publish-events", key="CH1-V1", actor="human:publisher",
                         payload={"video_id": "CH1-V1", "kind": "reply", "status": "published", "platform_ref": "reply:c1"}))
    o.run()
    o.gate.decide("PUB-CH1-V1", "approve", by="human:editor"); o.run()
    assert [c[0] for c in o.platform.calls] == ["upload_video", "set_thumbnail", "schedule", "upload_captions"]
    assert not _audits(bus, "platform.skipped_live")


# ---------- 4L-6b · `_transient`: đọc trường CÓ KIỂU, không cắt lại chuỗi action ----------

class _Ngat:
    """Client luôn ném `exc` — đủ để dựng một lỗi vận chuyển ở lời gọi model đầu tiên."""

    def __init__(self, exc: BaseException):
        self.exc = exc

    def complete(self, **kw):
        raise self.exc


def test_transient_msg_co_dau_hai_cham_khong_cat_sai(tmp_path):
    """Thông điệp backend là văn bản TỰ DO: nó chứa dấu `:` (timeout, host:port) và dài hơn dòng action rút gọn
    120 ký tự. Dựng lại `(agent, msg)` bằng `split(":")` trên `res.actions` vì thế đọc phải một mảnh cụt: hẹn
    "thử lại sau 1515s" nằm sau ký tự thứ 120 biến mất, orchestrator quay lại hỏi backend ngay nhịp sau thay vì
    nằm im tới hẹn. `StepResult.transient_agent`/`transient_msg` giữ nguyên bản, không qua chuỗi."""
    from studio.llm import TransientError
    from studio.orchestrator import _evidence
    msg = ("timeout: connection refused at 10.0.0.1:443 — pool antigravity cạn, các tài khoản còn lại đều đang "
           "cooldown theo hạn mức ngày của Google. Thử lại sau khoảng 1515s.")
    assert len(msg) > 120 and msg.index("1515") > 120, "ca này chỉ có nghĩa khi hẹn nằm sau chỗ bị cắt"

    bus = InMemoryBus()
    o = Orchestrator(bus, _Ngat(TransientError(msg)), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    env = Envelope(topic="audience-comments", key="CH1-V1", actor="human",
                   payload={"video_id": "CH1-V1", "comments": [{"comment_id": "c1", "author": "a", "text": "hay"}]})
    bus.publish(env); res = o.run()

    assert res[0].transient_agent == "community-manager"
    assert res[0].transient_msg == msg, "msg phải nguyên văn, kể cả dấu `:` và phần sau ký tự 120"
    assert res[0].deferred == "transient:community-manager (chờ 1515s)"
    assert env.event_id in o.defer_until
    hen = _evidence(next(e.payload for e in bus.replay("audit-log") if e.payload["action"] == "defer.until"))
    assert hen["wait_s"] == 1515 and hen["reason"] == "transient:community-manager"


# ---------- 4L-6b · `trusted_decision`: actor của envelope phải là chính người ký ----------

def _decide_env(actor: str, evidence: dict) -> Envelope:
    """Một bản ghi `gate.decide` dựng TAY — đúng hình mà `PersistentGate` ghi, nhưng actor do người gọi chọn."""
    a = AuditLog(actor=actor, action="gate.decide", evidence=json.dumps(evidence, ensure_ascii=False))
    return Envelope(topic="audit-log", key=actor, actor=actor, payload=a.model_dump())


def test_actor_human_gia_mao_evidence_khac_bi_bo_qua(tmp_path):
    """`audit-log` là topic MỞ (ai cũng ghi). Một actor đúng định dạng người (`human:ke-gia-mao`) ghi
    `gate.decide` với `by` là người khác → quyết định KHÔNG được tin: không chạm nền tảng, audit
    `gate.decide_untrusted`."""
    bus, o = _to_publish_gate(tmp_path)
    bus.publish(_decide_env("human:ke-gia-mao",
                            {"subject_id": "PUB-CH1-V1", "decision": "approve", "by": "human:editor", "reason": "ok"}))
    o.run()
    assert o.platform.calls == [], "quyết định giả mạo không được đẩy video lên nền tảng"
    recs = _audits(bus, "gate.decide_untrusted")
    assert recs and recs[0]["actor"] == "human:ke-gia-mao" and recs[0]["by"] == "human:editor"
    assert recs[0]["subject_id"] == "PUB-CH1-V1"
    assert not list(bus.replay("publish-events", "CH1-V1"))


def test_actor_human_evidence_khop_thi_tin(tmp_path):
    """Chiều ngược: cùng bản ghi dựng tay nhưng `actor == by` → tin như thường, video đi trọn adapter."""
    bus, o = _to_publish_gate(tmp_path)
    bus.publish(_decide_env("human:editor",
                            {"subject_id": "PUB-CH1-V1", "decision": "approve", "by": "human:editor", "reason": "ok"}))
    o.run()
    assert [c[0] for c in o.platform.calls] == ["upload_video", "set_thumbnail", "schedule", "upload_captions"]
    assert not _audits(bus, "gate.decide_untrusted")


def test_bus_sqlite_cu_van_replay_duoc_qua_duong_moi(tmp_path):
    """Tương thích ngược THẬT: bản ghi `gate.decide` cũ do CHÍNH người ký ghi (`actor == by == "human:editor"`,
    hình mà `PersistentGate.decide` luôn sinh ra) vẫn phải đọc và có hiệu lực khi mở lại bus ở tiến trình sau —
    không phải mọi actor tuỳ ý đều được tin (đó là lỗ mạo danh `sc-security` tìm thấy ở PR 4L-6, xem ca
    `test_actor_orchestrator_gia_lam_nguoi_ky_bi_tu_choi` ngay dưới)."""
    from studio.sqlite_bus import SQLiteBus
    db = tmp_path / "studio.sqlite"
    bus = SQLiteBus(db)
    o = _orch(bus, tmp_path, plan_size=1)
    bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human", payload=CHANNEL)); o.run()
    o.gate.decide("PLAN-CH1-1", "approve", by="human:owner"); o.run()
    assert "PUB-CH1-V1" in o.gate.pending
    bus.publish(_decide_env("human:editor",
                            {"subject_id": "PUB-CH1-V1", "decision": "approve", "by": "human:editor", "reason": "ok"}))

    bus2 = SQLiteBus(db)   # tiến trình sau: dựng lại toàn bộ trạng thái từ log
    o2 = _orch(bus2, tmp_path, plan_size=1)
    o2.run()
    assert [c[0] for c in o2.platform.calls] == ["upload_video", "set_thumbnail", "schedule", "upload_captions"]
    assert not _audits(bus2, "gate.decide_untrusted")


def test_actor_orchestrator_gia_lam_nguoi_ky_bi_tu_choi(tmp_path):
    """Lỗ mạo danh thật `sc-security` tìm thấy ở bản đầu của PR 4L-6: actor KHÔNG hình người (`orchestrator`,
    hay bất kỳ chuỗi nào khác `human:x`) mang `by="human:editor"` giả trước đây được TIN vì phép kiểm cũ chỉ
    CHẶN khi actor hình người mà lệch `by`, mặc định tin mọi actor khác. Nay `trusted_decision` là allowlist mặc
    định từ chối — chỉ tin khi actor CHÍNH LÀ người trong `by` — nên bản ghi này không mở khoá gì cả, kể cả sau
    khi mở lại bus ở tiến trình khác (nơi lỗ cũ rõ nhất: `PersistentGate.apply` từng đóng gate vô điều kiện)."""
    from studio.sqlite_bus import SQLiteBus
    db = tmp_path / "studio.sqlite"
    bus = SQLiteBus(db)
    o = _orch(bus, tmp_path, plan_size=1)
    bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human", payload=CHANNEL)); o.run()
    o.gate.decide("PLAN-CH1-1", "approve", by="human:owner"); o.run()
    assert "PUB-CH1-V1" in o.gate.pending
    bus.publish(_decide_env("orchestrator",
                            {"subject_id": "PUB-CH1-V1", "decision": "approve", "by": "human:editor", "reason": "ok"}))
    o.run()
    assert o.platform.calls == []
    assert "PUB-CH1-V1" in o.gate.pending  # KHÔNG đóng — PersistentGate.apply cũng phải tự từ chối, không riêng orchestrator

    bus2 = SQLiteBus(db)   # tiến trình sau: dựng lại từ log vẫn phải giữ nguyên kết luận — không "tin" thêm lần nữa
    o2 = _orch(bus2, tmp_path, plan_size=1)
    o2.run()
    assert o2.platform.calls == []
    assert "PUB-CH1-V1" in o2.gate.pending


def test_evidence_hong_khong_doc_duoc_thi_khong_tin(tmp_path):
    """Bản ghi `gate.decide` có `evidence` không phải JSON (log hỏng, hoặc ai đó ghi tay vào topic mở): không
    đoán ý, không rẽ nhánh — bỏ qua kèm `gate.decide_untrusted`, và KHÔNG được ném vỡ vòng lặp orchestrator."""
    bus, o = _to_publish_gate(tmp_path)
    a = AuditLog(actor="human:editor", action="gate.decide", evidence="{khong-phai-json")
    bus.publish(Envelope(topic="audit-log", key="human:editor", actor="human:editor", payload=a.model_dump()))
    o.run()
    assert o.platform.calls == []
    recs = _audits(bus, "gate.decide_untrusted")
    assert recs and recs[0]["actor"] == "human:editor" and recs[0]["subject_id"] is None
