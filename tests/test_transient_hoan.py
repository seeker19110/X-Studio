"""K3.3d: lỗi VẬN CHUYỂN không phải lỗi agent — orchestrator HOÃN event thay vì dừng.

Trước bước này studio chỉ có `except (RunnerError, LLMError)`: một nhịp mạng chập, một backend hết quota, một
CLI timeout đều đọc ra "agent trả lời sai" → ghi `agent_failed`, desk đếm một lần hỏng, đủ số lần thì video bị
`blocked` và cần người gỡ. Việc chưa bao giờ được làm lại vì `_done` đã đóng dấu `orchestrated` cho event.

`TransientError` (lên core ở K3.3c2, ném đúng chỗ từ đó) nay được BẮT ở cả ba chỗ gọi model — `_call`, `_plan`,
`_decide` — và event quay lại hàng đợi ở nhịp `tick` sau. Ba chỗ chứ không một, vì chúng là ba bản sao của cùng
một bước: vá một chỗ là để lại hai chỗ y hệt (TRAPS.md §1 khuôn 3).
"""
from __future__ import annotations

from studio.bus import InMemoryBus
from studio.events import Envelope
from studio.fakes import make_scripted_client
from studio.llm import LLMError, TransientError
from studio.media import MediaConfig, make_media
from studio.orchestrator import Orchestrator, _evidence

CHANNEL = {"channel_id": "CH1", "goals": ["1000 sub"], "audience": "người mới", "pillars": ["hướng dẫn"],
           "cadence": "2/tuần", "boundaries": ["không hứa thu nhập"]}
COMMENTS = {"video_id": "CH1-V1", "comments": [{"comment_id": "c1", "author": "a", "text": "hay quá"}]}


class _Flaky:
    """Bọc client thật: ném `exc` cho `n` lời gọi đầu, sau đó trả lời bình thường."""

    def __init__(self, inner, exc: BaseException, n: int = 1):
        self.inner, self.exc, self.left, self.calls = inner, exc, n, 0

    def complete(self, *, system, user, schema, model_tier, cache_key=None, tools=None, messages=None, workdir=None):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise self.exc
        return self.inner.complete(system=system, user=user, schema=schema, model_tier=model_tier,
                                   cache_key=cache_key, tools=tools, messages=messages, workdir=workdir)


def _orch(bus, tmp_path, exc, n=1, **opts):
    client = _Flaky(make_scripted_client(**opts), exc, n)
    o = Orchestrator(bus, client, media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    o._flaky = client   # type: ignore[attr-defined]
    return o


def _actions(bus):
    return [e.payload["action"] for e in bus.replay("audit-log")]


# ---------- 1. `_call`: hoãn, không dừng ----------

def test_transient_hoan_khong_dung(tmp_path):
    """Ca chính của K3.3d. Lỗi vận chuyển ở một route: event KHÔNG bị đóng dấu `orchestrated`, KHÔNG ghi
    `agent_failed`, và nhịp `tick` sau chạy lại đúng bước đó."""
    bus = InMemoryBus()
    o = _orch(bus, tmp_path, TransientError("lỗi mạng: connection reset"))
    env = Envelope(topic="audience-comments", key="CH1-V1", actor="human", payload=COMMENTS)
    bus.publish(env)
    res = o.run()

    assert res[0].deferred == "transient:community-manager"
    assert any(a.startswith("transient:community-manager") for a in res[0].actions)
    assert env.event_id in o.deferred and env.event_id not in o.processed
    assert "agent_failed" not in _actions(bus), "lỗi vận chuyển không được tính là lỗi agent"
    assert "orchestrated" not in _actions(bus), "event chưa xong thì không được đóng dấu"
    assert not list(bus.replay("reply-drafts"))

    # nhịp sau: client hết hỏng → chạy lại đúng bước vừa hoãn
    o.tick()
    assert env.event_id in o.processed and not o.deferred
    assert list(bus.replay("reply-drafts")), "event phải được làm lại, không phải mất luôn"
    assert o.stats["transient"] == 1


def test_loi_noi_dung_van_la_loi_agent(tmp_path):
    """Đối chứng: `LLMError` trần (model trả lời sai) KHÔNG được hoãn — nó là lỗi agent như trước, ghi
    `agent_failed` và đóng dấu event. Nếu ca này đỏ thì bản vá đã nuốt cả lỗi thật."""
    bus = InMemoryBus()
    o = _orch(bus, tmp_path, LLMError("đầu ra không phải JSON"))
    env = Envelope(topic="audience-comments", key="CH1-V1", actor="human", payload=COMMENTS)
    bus.publish(env)
    res = o.run()

    assert res[0].deferred is None and not o.deferred
    assert "agent_failed" in _actions(bus) and env.event_id in o.processed


# ---------- 2. hẹn giờ của backend ----------

def test_hen_gio_cua_backend_duoc_ton_trong(tmp_path):
    """"mọi backend đều đang nghỉ, thử lại sau 120s" → chờ đúng 120s. Hỏi lại mỗi nhịp trong lúc pool cạn chỉ
    sinh một dòng lỗi mỗi lần và làm phình audit-log (company đo được 60 bản ghi/phút, 2026-09-04)."""
    bus = InMemoryBus()
    o = _orch(bus, tmp_path, TransientError("mọi backend đều đang nghỉ, thử lại sau 120s (antigravity: hết quota)"))
    env = Envelope(topic="audience-comments", key="CH1-V1", actor="human", payload=COMMENTS)
    bus.publish(env)
    res = o.run()

    assert res[0].deferred == "transient:community-manager (chờ 120s)"
    assert env.event_id in o.defer_until
    hen = _evidence(next(e.payload for e in bus.replay("audit-log") if e.payload["action"] == "defer.until"))
    assert hen["wait_s"] == 120 and hen["reason"] == "transient:community-manager"

    o.tick()   # chưa tới hẹn → không đụng vào backend
    assert env.event_id in o.deferred and o._flaky.calls == 1

    o.defer_until[env.event_id] -= 121   # tua tới sau hẹn
    o.tick()
    assert not o.deferred and list(bus.replay("reply-drafts"))


def test_tick_chi_danh_thuc_transient_khong_danh_thuc_paused(tmp_path):
    """`_retry_deferred(only="transient:")`: video đang bị supervisor pause phải nằm yên tới đúng lệnh `resume`,
    kể cả khi một event khác được đánh thức cùng nhịp."""
    bus = InMemoryBus()
    o = _orch(bus, tmp_path, TransientError("lỗi mạng: reset"))
    bus.publish(Envelope(topic="supervisor-actions", key="CH1-V9", actor="supervisor",
                         payload={"action": "pause", "target": "CH1-V9", "reason": "vượt ngân sách"}))
    paused = Envelope(topic="audience-comments", key="CH1-V9", actor="human",
                      payload={**COMMENTS, "video_id": "CH1-V9"})
    bus.publish(paused)
    o.run()
    assert o.deferred[paused.event_id][1] == "paused:CH1-V9"

    o.tick()
    assert paused.event_id in o.deferred, "pause chỉ được gỡ bằng `resume`, không phải bằng một nhịp tick"

    bus.publish(Envelope(topic="supervisor-actions", key="CH1-V9", actor="supervisor",
                         payload={"action": "resume", "target": "CH1-V9", "reason": "người trực gỡ pause"}))
    o.run()
    # `resume` ĐÃ đánh thức nó: lý do hoãn đổi từ `paused:` sang `transient:` vì client vẫn còn hỏng — tức
    # event đã chạy lại thật, không phải vẫn nằm im.
    assert o.deferred[paused.event_id][1] == "transient:community-manager"


# ---------- 3. hai chỗ gọi model còn lại ----------

def test_transient_trong_plan_khong_dot_luot_cua_ke_hoach(tmp_path):
    """`_plan` (channel-strategist) là chỗ gọi model THỨ HAI, không đi qua `_call`. Trước K3.3d nó ghi
    `plan!failed` + `agent_failed` cho một lỗi mạng."""
    bus = InMemoryBus()
    o = _orch(bus, tmp_path, TransientError("lỗi mạng: timeout"), plan_size=1)
    env = Envelope(topic="trend-reports", key="CH1", actor="trend-researcher",
                   payload={"channel_id": "CH1", "sources": ["https://example.com"],
                            "trends": [{"topic": "t", "momentum": "rising", "evidence": "e"}]})
    bus.publish(env)
    res = o.run()

    assert res[0].deferred == "transient:channel-strategist"
    assert "plan!failed" not in res[0].actions and "agent_failed" not in _actions(bus)
    assert not o.plans

    o.tick()
    assert o.plans and env.event_id in o.processed


def test_transient_trong_decide_khong_mat_lan_dang_da_duyet(tmp_path):
    """`_decide` (publisher, ADR-0008) là chỗ gọi model THỨ BA, và nó chạy trong nhánh `audit-log` của
    `process()` — nhánh có `return` sớm riêng. Đây là chỗ lỗi vận chuyển đắt nhất: gate publish ĐÃ được người
    ký, mà `_done` thì đóng dấu event vĩnh viễn — mất luôn lần đăng đã duyệt, không ai thấy.

    Chạy lại an toàn vì hai cơ chế có sẵn: nhánh `PUB-` giữ nguyên trạng thái khi video đã `approved`, và
    `_publish_video` dùng lại upload trước qua `_prior_upload`."""
    bus = InMemoryBus()
    o = _orch(bus, tmp_path, TransientError("HTTP 529: overloaded"), n=99, plan_size=1)
    bus.publish(Envelope(topic="channel-briefs", key="CH1", actor="human", payload=CHANNEL))
    o._flaky.left = 0          # cả pipeline chạy bình thường tới gate publish
    o.run()
    o.gate.decide("PLAN-CH1-1", "approve", by="human:owner"); o.run()
    (sid,) = [s for s in o.gate.pending if s.startswith("PUB-")]
    vid = sid[4:]

    o._flaky.left = 99         # từ đây mọi lời gọi model đều hỏng vì transport
    o.gate.decide(sid, "approve", by="human:editor")
    res = o.run()
    quyet = next(r for r in res if r.topic == "audit-log")

    assert quyet.deferred == "transient:publisher", "quyết định gate phải được hoãn, không đóng dấu"
    assert quyet.event_id in o.deferred and quyet.event_id not in o.processed
    assert not list(bus.replay("publish-events", vid)), "chưa đăng được thì không được có publish-events"
    assert "agent_failed" not in _actions(bus)

    o._flaky.left = 0          # mạng trở lại
    o.tick()
    ev = [e.payload for e in bus.replay("publish-events", vid)]
    assert ev and ev[-1]["status"] in {"scheduled", "published"}, "lần đăng đã duyệt phải được làm lại"
    assert not o.deferred
