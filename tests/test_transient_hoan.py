"""K3.3d: lỗi VẬN CHUYỂN không phải lỗi agent — orchestrator HOÃN event thay vì dừng.

Trước bước này studio chỉ có `except (RunnerError, LLMError)`: một nhịp mạng chập, một backend hết quota, một
CLI timeout đều đọc ra "agent trả lời sai" → ghi `agent_failed`, desk đếm một lần hỏng, đủ số lần thì video bị
`blocked` và cần người gỡ. Việc chưa bao giờ được làm lại vì `_done` đã đóng dấu `orchestrated` cho event.

`TransientError` (lên core ở K3.3c2, ném đúng chỗ từ đó) nay được BẮT ở cả ba chỗ gọi model — `_call`, `_plan`,
`_decide` — và event quay lại hàng đợi ở nhịp `tick` sau. Ba chỗ chứ không một, vì chúng là ba bản sao của cùng
một bước: vá một chỗ là để lại hai chỗ y hệt (TRAPS.md §1 khuôn 3).
"""
from __future__ import annotations

import pytest

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


def test_completed_sibling_route_survives_retry_and_restart(tmp_path, monkeypatch):
    from studio.orchestrator import ROUTES, StepResult
    from studio.sqlite_bus import SQLiteBus

    path = tmp_path / "resume.sqlite"
    bus = SQLiteBus(path)
    o = _orch(bus, tmp_path, TransientError("unused"), n=0)
    env = Envelope(topic="review-results", key="V1", actor="fact-checker", payload={
        "video_id": "V1", "source": "fact", "verdict": "pass", "findings": []})
    calls = []
    from types import SimpleNamespace

    def generate(runner, agent, *args, **kwargs):
        calls.append(agent)
        if agent == "seo-optimizer": raise TransientError("retry")
        return SimpleNamespace(payloads=[], tokens=0, model="fake", context_writes=[], cache_hit_ratio=0)

    monkeypatch.setattr("studio.longform.generate_partitioned", generate)
    routes = [r for r in ROUTES if r.topic_in == "review-results"]
    for route in routes: o._call(route, env, StepResult(env.event_id, env.topic, env.key))
    bus.close()
    bus = SQLiteBus(path)
    resumed = _orch(bus, tmp_path, TransientError("unused"), n=0)
    for route in routes: resumed._call(route, env, StepResult(env.event_id, env.topic, env.key))
    assert calls.count("production-manager") == 1
    assert calls.count("seo-optimizer") == 2
    bus.close()


def test_transient_seo_correction_keeps_retry_available(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from studio.events import Finding
    from studio.orchestrator import StepResult
    from studio.preflight import PreflightReport

    bus = InMemoryBus()
    o = _orch(bus, tmp_path, TransientError("unused"), n=0)
    env = Envelope(topic="metadata-packages", key="V1", actor="seo-optimizer", payload={
        "video_id": "V1", "title": "Test", "description": "test", "tags": []})
    monkeypatch.setattr("studio.orchestrator.preflight", lambda *args, **kwargs: PreflightReport(findings=[
        Finding(level="block", text="needs revision")]))
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1: raise TransientError("retry")
        return SimpleNamespace(payloads=[], tokens=0, model="fake", context_writes=[], cache_hit_ratio=0)

    monkeypatch.setattr("studio.longform.generate_partitioned", generate)
    first = StepResult(env.event_id, env.topic, env.key)
    o._preflight(env, first)
    assert first.transient
    second = StepResult(env.event_id, env.topic, env.key)
    o._preflight(env, second)
    assert len(calls) == 2
    assert not second.transient


def test_reply_batch_retry_and_restart_do_not_repost_completed_replies(tmp_path, monkeypatch):
    from studio.runner import Generated
    from studio.sqlite_bus import SQLiteBus

    path = tmp_path / "replies.sqlite"
    bus = SQLiteBus(path)
    o = _orch(bus, tmp_path, TransientError("unused"), n=0)
    bus.publish(Envelope(topic="audience-comments", key="CH1-V1", actor="human", payload={
        "video_id": "CH1-V1", "comments": [
            {"comment_id": "c1", "author": "a", "text": "one"},
            {"comment_id": "c2", "author": "b", "text": "two"},
            {"comment_id": "c3", "author": "c", "text": "three"}]}))
    o.run()
    sid = next(iter(o.reply_batches))
    calls = []

    def generate(_agent, inp, _topic, **_kwargs):
        cid = inp.payload["comment_id"]
        calls.append(cid)
        if cid == "c2" and calls.count(cid) == 1: raise TransientError("temporary transport failure")
        return Generated(payloads=[{"video_id": "CH1-V1", "kind": "reply", "status": "published"}],
                         tokens=0, model="fake")

    monkeypatch.setattr(o.runner, "generate", generate)
    o.gate.decide(sid, "approve", by="human:editor")
    o.run()
    assert calls == ["c1", "c2"]
    bus.close()
    bus = SQLiteBus(path)
    resumed = _orch(bus, tmp_path, TransientError("unused"), n=0)
    monkeypatch.setattr(resumed.runner, "generate", generate)
    resumed.run()
    replies = [e.payload["comment_id"] for e in bus.replay("publish-events")]
    assert replies == ["c1", "c2", "c3"]
    assert calls == ["c1", "c2", "c2", "c3"]
    bus.close()


def test_render_checkpoint_survives_thumbnail_failure_and_restart(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from studio.events import Scene, SceneManifest
    from studio.sqlite_bus import SQLiteBus

    path = tmp_path / 'render.sqlite'
    bus = SQLiteBus(path)
    o = _orch(bus, tmp_path, TransientError('unused'), n=0)
    env = Envelope(topic='scene-manifests', key='V1', actor='production-manager', payload=SceneManifest(
        video_id='V1', scenes=[Scene(scene_id='S1', order=0, narration='hello', visual_prompt='desk')]).model_dump())
    bus.publish(env)
    calls = []

    def generate(runner, agent, *args, **kwargs):
        calls.append(agent)
        if agent == 'thumbnail-designer' and calls.count(agent) == 1:
            raise TransientError('retry')
        return SimpleNamespace(payloads=[], tokens=0, model='fake', context_writes=[], cache_hit_ratio=0)

    monkeypatch.setattr('studio.longform.generate_partitioned', generate)
    assert o.process(env).transient
    o.queue = [queued for queued in o.queue if queued.event_id != env.event_id]
    o.run()  # editor consumes the successful draft while thumbnail is deferred
    bus.close()
    bus = SQLiteBus(path)
    resumed = _orch(bus, tmp_path, TransientError('unused'), n=0)
    resumed.run()
    drafts = [e for e in bus.replay('media-assets') if e.payload['kind'] == 'draft_video']
    assert len(drafts) == 1
    assert calls.count('editor') == 1
    assert calls.count('thumbnail-designer') == 2
    bus.close()


def test_draft_published_before_stage_checkpoint_does_not_repeat_editor(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pytest

    from studio.events import Scene, SceneManifest
    from studio.sqlite_bus import SQLiteBus

    path = tmp_path / 'stage.sqlite'
    bus = SQLiteBus(path)
    o = _orch(bus, tmp_path, TransientError('unused'), n=0)
    env = Envelope(topic='scene-manifests', key='V1', actor='production-manager', payload=SceneManifest(
        video_id='V1', scenes=[Scene(scene_id='S1', order=0, narration='hello', visual_prompt='desk')]).model_dump())
    bus.publish(env)
    calls = []
    def generate(runner, agent, *args, **kwargs):
        calls.append(agent)
        return SimpleNamespace(payloads=[], tokens=0, model='fake', context_writes=[], cache_hit_ratio=0)
    monkeypatch.setattr('studio.longform.generate_partitioned', generate)
    remember = o._remember
    def interrupted(key):
        if key == f'code:{env.event_id}': raise RuntimeError('checkpoint interrupted')
        return remember(key)
    monkeypatch.setattr(o, '_remember', interrupted)
    with pytest.raises(RuntimeError, match='checkpoint interrupted'): o.process(env)
    bus.close()
    bus = SQLiteBus(path)
    resumed = _orch(bus, tmp_path, TransientError('unused'), n=0)
    def forbidden(*args, **kwargs): raise AssertionError('must reuse prepared output')
    monkeypatch.setattr(resumed.renderer.media.video, 'assemble', forbidden)
    resumed.run()
    assert sum(e.payload['kind'] == 'draft_video' for e in bus.replay('media-assets')) == 1
    assert calls.count('editor') == 1
    bus.close()


@pytest.mark.parametrize("decision", ["repair", "approve"])
def test_cut_restart_uses_source_version_and_completes_prepared_output(tmp_path, monkeypatch, decision):
    import pytest

    from studio.events import CutList, Scene, SceneManifest
    from studio.sqlite_bus import SQLiteBus

    path = tmp_path / 'cut.sqlite'
    bus = SQLiteBus(path)
    o = _orch(bus, tmp_path, TransientError('unused'), n=0)
    manifest = SceneManifest(video_id='V1', scenes=[Scene(scene_id='S1', order=0, narration='hello', visual_prompt='desk')])
    o.renderer.render(manifest)
    env = Envelope(topic='cut-lists', key='V1', actor='editor', payload=CutList(
        video_id='V1', manifest_version=1, decision=decision, repairs=[]).model_dump())
    bus.publish(env)
    publish = bus.publish
    def interrupted(event):
        if event.topic == 'media-assets' and event.payload['kind'] == ('draft_video' if decision == 'repair' else 'final_video'):
            raise RuntimeError('partial repair')
        return publish(event)
    monkeypatch.setattr(bus, 'publish', interrupted)
    with pytest.raises(RuntimeError, match='partial repair'): o.process(env)
    assert o.manifest('V1').version == (2 if decision == 'repair' else 1)
    bus.close()
    bus = SQLiteBus(path)
    resumed = _orch(bus, tmp_path, TransientError('unused'), n=0)
    monkeypatch.setattr(resumed.desk, 'repair_allowed', lambda vid: False)
    result = resumed.process(env)
    assert ('repair→recovered' if decision == 'repair' else 'finalize') in result.actions
    assert resumed.manifest('V1').version == (2 if decision == 'repair' else 1)
    assert sum(e.payload['kind'] == 'draft_video' for e in bus.replay('media-assets')) == (2 if decision == 'repair' else 1)
    assert (decision == 'approve') == any(e.payload['kind'] == 'final_video' for e in bus.replay('media-assets'))
    bus.close()


def test_restart_restores_latest_trusted_qc_for_reviewers(tmp_path):
    import json

    from studio.events import AuditLog, Finding
    from studio.qc import QCReport
    from studio.sqlite_bus import SQLiteBus

    path = tmp_path / 'qc.sqlite'
    bus = SQLiteBus(path)
    original = _orch(bus, tmp_path, TransientError('unused'), n=0)
    previous = QCReport(available=False)
    expected = QCReport(available=True, metrics={'duration_s': 42}, findings=[
        Finding(level='warn', text='soft issue', location='video')])
    original._audit('qc', {'video_id': 'V1', **previous.as_dict()}, video_id='V1')
    original._audit('qc', {'video_id': 'V1', **expected.as_dict()}, video_id='V1')
    # Neither malformed nor actor-spoofed evidence may replace a measured report.
    original._audit('qc', {'video_id': 'V1', 'available': True, 'metrics': {},
                           'findings': [{'level': 'invalid'}]}, video_id='V1')
    for evidence in ({'video_id': 'V1'}, {'video_id': 'V1', 'available': 'yes', 'metrics': [], 'findings': []}):
        original._audit('qc', evidence, video_id='V1')
    for actor, payload_actor in [('renderer', 'orchestrator'), ('orchestrator', 'renderer')]:
        audit = AuditLog(actor=payload_actor, action='qc', video_id='V1',
                         evidence=json.dumps({'video_id': 'V1', **previous.as_dict()}))
        bus.publish(Envelope(topic='audit-log', key=actor, actor=actor, payload=audit.model_dump()))
    bus.close()
    bus = SQLiteBus(path)
    resumed = _orch(bus, tmp_path, TransientError('unused'), n=0)
    assert resumed.qcs['V1'].as_dict() == expected.as_dict()
    from studio.orchestrator import _with_package
    trigger = Envelope(topic='media-assets', key='V1', actor='renderer', payload={'video_id': 'V1'})
    assert _with_package(trigger, resumed)['package']['qc'] == expected.as_dict()
    bus.close()


@pytest.mark.parametrize('decision', ['repair', 'approve'])
@pytest.mark.parametrize('restart', [False, True])
def test_fresh_stale_cut_cannot_overwrite_newer_repair(tmp_path, decision, restart, monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock

    from studio.events import CutList, Repair, Scene, SceneManifest
    from studio.renderer import checksum
    from studio.sqlite_bus import SQLiteBus

    database = tmp_path / 'stale.sqlite'
    bus = SQLiteBus(database)
    orch = _orch(bus, tmp_path, TransientError('unused'), n=0)
    source = SceneManifest(video_id='V1', scenes=[
        Scene(scene_id='S1', order=0, narration='original first', visual_prompt='desk'),
        Scene(scene_id='S2', order=1, narration='original second', visual_prompt='room')])
    orch.renderer.render(source)
    first_cut = CutList(video_id='V1', manifest_version=1, decision='repair', repairs=[
        Repair(scene_id='S1', action='regenerate_audio', reason='first edit', new_narration='first accepted edit')])
    first = Envelope(topic='cut-lists', key='V1', actor='editor', payload=first_cut.model_dump())
    bus.publish(first)
    orch.process(first)
    accepted = orch.manifest('V1').model_dump()
    outputs = list(bus.replay('media-assets'))
    files = {e.payload['path']: checksum(Path(e.payload['path'])) for e in outputs}
    if restart:
        bus.close()
        bus = SQLiteBus(database)
        orch = _orch(bus, tmp_path, TransientError('unused'), n=0)
    repair = Mock(wraps=orch.renderer.apply_cutlist)
    finalize = Mock(wraps=orch.renderer.finalize)
    monkeypatch.setattr(orch.renderer, 'apply_cutlist', repair)
    monkeypatch.setattr(orch.renderer, 'finalize', finalize)
    stale = Envelope(topic='cut-lists', key='V1', actor='editor', payload=CutList(
        video_id='V1', manifest_version=1, decision=decision, repairs=[
            Repair(scene_id='S2', action='regenerate_audio', reason='late edit', new_narration='late conflicting edit')]
        if decision == 'repair' else []).model_dump())
    bus.publish(stale)
    result = orch.process(stale)
    assert 'cut!stale' in result.actions
    repair.assert_not_called()
    finalize.assert_not_called()
    assert orch.manifest('V1').model_dump() == accepted
    assert len(list(bus.replay('media-assets'))) == len(outputs)
    assert {path: checksum(Path(path)) for path in files} == files
    rejected = [e for e in bus.replay('audit-log') if e.payload['action'] == 'cut.stale']
    assert _evidence(rejected[-1].payload)['event_id'] == stale.event_id
    # Rejecting the distinct stale event must leave the first operation's durable
    # output intact and replayable, including its original media checksums.
    orch.renderer.operation_id = first.event_id
    assert orch.renderer.apply_cutlist(source, first_cut).model_dump() == accepted
    bus.close()
