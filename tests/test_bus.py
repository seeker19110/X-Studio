import pytest

from studio.bus import BusError, InMemoryBus, PermissionDenied
from studio.events import Envelope, VideoBrief


def _brief(vid="V1", **kw):
    return VideoBrief(video_id=vid, channel_id="CH1", working_title="x", pillar="p", angle="a", audience="u", **kw)


def test_publish_valid_brief():
    bus = InMemoryBus()
    bus.publish(Envelope(topic="video-briefs", key="V1", actor="channel-strategist", payload=_brief().model_dump()))
    assert len(bus) == 1


def test_invalid_payload_rejected():
    bus = InMemoryBus()
    with pytest.raises(BusError):
        bus.publish(Envelope(topic="video-briefs", key="V1", actor="channel-strategist", payload={"video_id": "V1"}))


def test_schema_required_fields_for_manual_topics():
    bus = InMemoryBus()
    with pytest.raises(BusError):
        bus.publish(Envelope(topic="research-dossiers", key="V1", actor="trend-researcher", payload={"video_id": "V1"}))
    bus.publish(Envelope(topic="research-dossiers", key="V1", actor="trend-researcher",
                         payload={"video_id": "V1", "sources": [{"title": "a", "url": "https://x"}], "evidence": ["e"]}))
    assert len(bus) == 1


def test_namespace_owner_enforced():
    bus = InMemoryBus()
    with pytest.raises(PermissionDenied):
        bus.publish(Envelope(topic="shared-context", key="rights", actor="editor",
                             payload={"namespace": "rights", "version": 1, "content_ref": "x"}))
    bus.publish(Envelope(topic="shared-context", key="rights", actor="rights-checker",
                         payload={"namespace": "rights", "version": 1, "content_ref": "x"}))


def test_every_topic_has_schema():
    from typing import get_args

    from studio.events import Topic
    bus = InMemoryBus()
    assert set(get_args(Topic)) == set(bus._schemas)


def test_replay_by_key():
    bus = InMemoryBus()
    for k in ("A", "B", "A"):
        bus.publish(Envelope(topic="audit-log", key=k, actor=k, payload={"actor": k, "action": "x"}))
    assert len(list(bus.replay(key="A"))) == 2
