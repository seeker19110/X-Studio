from unittest.mock import Mock

from studio.bus import InMemoryBus
from studio.events import Envelope, SceneManifest
from studio.fakes import make_scripted_client
from studio.media import MediaConfig, MediaError, make_media
from studio.orchestrator import Orchestrator


def test_failed_render_is_deferred_and_not_marked_completed(tmp_path):
    o=Orchestrator(InMemoryBus(),make_scripted_client(),media=make_media(MediaConfig(output_dir=tmp_path,render={"max_retries":2})))
    o.renderer.render=Mock(side_effect=MediaError("temporary provider error"))
    event=Envelope(topic="scene-manifests",key="V",actor="production-manager",payload=SceneManifest(video_id="V",scenes=[]).model_dump())
    result=o.process(event)
    assert result.transient and event.event_id in o.deferred and event.event_id not in o.processed
    assert o.defer_until[event.event_id]>0
    o.process(event)
    final=o.process(event)
    assert not final.transient and event.event_id in o.processed


def test_media_retry_exhaustion_pauses_known_video(tmp_path):
    from studio.events import VideoBrief
    from studio.orchestrator import StepResult
    o=Orchestrator(InMemoryBus(),make_scripted_client(),media=make_media(MediaConfig(output_dir=tmp_path,render={'max_retries':0})))
    brief=VideoBrief(video_id='V',channel_id='CH',working_title='x',audience='all',angle='x',pillar='education',target_minutes=1,
                     key_points=['x'],success_metric='retention',budget_tokens=1000)
    o.bus.publish(Envelope(topic='video-briefs',key='V',actor='desk',payload=brief.model_dump()))
    env=Envelope(topic='scene-manifests',key='V',actor='production-manager',payload={'video_id':'V'})
    o._media_failure(env,StepResult(env.event_id,env.topic,env.key),MediaError('offline'))
    assert list(o.bus.replay('supervisor-actions','V'))[-1].payload['action']=='pause'
