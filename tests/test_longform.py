from unittest.mock import Mock

from studio.events import Envelope
from studio.longform import generate_partitioned
from studio.runner import Generated


def test_chapter_manifest_rejects_whole_script_and_charges_failed_proposal(tmp_path):
    import pytest

    from studio.runner import RunnerError
    runner = Mock()
    runner.client = object()
    runner.bus.replay.return_value = []
    runner.agents = {'production-manager': Mock(version=1, system_prompt=lambda: 'Plan')}
    script = {'video_id': 'V', 'sections': [{'narration': f'Part {i}.'} for i in range(10)]}
    env = Envelope(topic='scripts', key='V', actor='script-writer', payload=script)

    def produce(agent, inp, topic, **kwargs):
        sections = inp.payload['sections'] if kwargs['extra']['chapter']['index'] < 3 else script['sections']
        return Generated(payloads=[{'scenes': [{'scene_id': str(i), 'order': i, 'narration': s['narration']}
            for i, s in enumerate(sections)]}], model='unit', tokens=50)

    runner.generate.side_effect = produce
    with pytest.raises(RunnerError, match='chapter narration'):
        generate_partitioned(runner, 'production-manager', env, 'scene-manifests', {'script': script}, tmp_path)
    assert runner.generate.call_count == runner._audit.call_count == 3
    assert len(list(tmp_path.glob('*.json'))) == 2


def test_cached_chapter_with_repeated_narration_is_regenerated(tmp_path):
    import json

    runner = Mock()
    runner.client = object()
    runner.bus.replay.return_value = []
    runner.agents = {'production-manager': Mock(version=1, system_prompt=lambda: 'Plan')}
    script = {'video_id': 'V', 'sections': [{'narration': f'Part {i}.'} for i in range(10)]}
    env = Envelope(topic='review-results', key='V', actor='fact-checker', payload={'source': 'fact'})

    def produce(agent, inp, topic, **kwargs):
        return Generated(payloads=[{'scenes': [{'scene_id': str(i), 'order': i, 'narration': s['narration']}
            for i, s in enumerate(kwargs['extra']['script']['sections'])]}], model='unit', tokens=50)

    runner.generate.side_effect = produce
    generate_partitioned(runner, 'production-manager', env, 'scene-manifests', {'script': script}, tmp_path)
    for path in tmp_path.glob('*.json'):
        stored = json.loads(path.read_text())
        if len(stored['payloads'][0]['scenes']) == 2:
            stored['payloads'][0]['scenes'][0]['narration'] = 'Unrelated opening from another chapter.'
            path.write_text(json.dumps(stored))
    result = generate_partitioned(runner, 'production-manager', env, 'scene-manifests', {'script': script}, tmp_path)
    assert runner.generate.call_count == runner._audit.call_count == 4
    assert [s['narration'] for s in result.payloads[0]['scenes']] == [s['narration'] for s in script['sections']]


def test_long_scripts_are_chaptered_cached_and_claim_ids_are_unique(tmp_path):
    runner = Mock()
    runner.agents = {"script-writer": Mock(version=1, system_prompt=lambda: "Write a story")}
    runner.client = object()
    runner.bus.replay.return_value = []
    runner.generate.return_value = Generated(payloads=[{"video_id":"V1","working_title":"Story", "hook":"hook", "cta":"end",
        "sections":[{"heading":"Chapter", "narration":"Some words", "claim_ids":["C1"]}],
        "claims":[{"claim_id":"C1","text":"Fact","source":"https://example.org"}]}],tokens=100,model="test")
    env = Envelope(topic="video-briefs",key="V1",actor="desk",payload={"video_id":"V1","target_minutes":25})
    out = generate_partitioned(runner,"script-writer",env,"scripts",{},tmp_path,chapter_minutes=8)
    assert runner.generate.call_count == 4
    assert len(out.payloads[0]["sections"]) == 4
    ids=[c["claim_id"] for c in out.payloads[0]["claims"]]
    assert len(set(ids)) == 4
    assert all(s["claim_ids"][0] in ids for s in out.payloads[0]["sections"])
    again = generate_partitioned(runner,"script-writer",env,"scripts",{},tmp_path,chapter_minutes=8)
    assert runner.generate.call_count == 4 and again.tokens == 0
    assert runner._audit.call_count == 4


def test_chapter_generation_stops_at_video_budget(tmp_path):
    import pytest

    from studio.runner import RunnerError
    runner = Mock(); runner.client = object()
    runner.agents = {"script-writer": Mock(version=1, system_prompt=lambda: "Write")}
    runner.bus.replay.return_value = []
    runner.generate.return_value = Generated(payloads=[{"sections": [], "claims": []}], tokens=80, model="test")
    env = Envelope(topic="video-briefs", key="V", actor="desk", payload={"video_id":"V", "target_minutes":24,"budget_tokens":100})
    with pytest.raises(RunnerError, match="budget"):
        generate_partitioned(runner,"script-writer",env,"scripts",{},tmp_path)
    assert runner.generate.call_count == 2


def test_partitioned_fact_review_uses_durable_video_budget(tmp_path):
    import pytest

    from studio.runner import RunnerError
    runner = Mock(); runner.client = object()
    runner.agents = {"fact-checker": Mock(version=1, system_prompt=lambda: "Review")}
    brief = Envelope(topic="video-briefs", key="V", actor="desk", payload={"video_id": "V", "budget_tokens": 100})
    runner.bus.replay.side_effect = lambda topic, *args: [brief] if topic == "video-briefs" else []
    runner.generate.return_value = Generated(payloads=[{"verdict": "pass", "findings": []}], tokens=80, model="test")
    script = {"video_id": "V", "sections": [{"heading": str(i), "narration": "words"} for i in range(9)], "claims": []}
    inp = Envelope(topic="scripts", key="V", actor="script-writer", payload=script)
    with pytest.raises(RunnerError, match="budget"):
        generate_partitioned(runner, "fact-checker", inp, "review-results", {}, tmp_path)
    assert runner.generate.call_count == 2


def test_short_request_uses_existing_runner_directly(tmp_path):
    runner=Mock(); env=Envelope(topic="video-briefs",key="V",actor="desk",payload={"target_minutes":3})
    generate_partitioned(runner,"script-writer",env,"scripts",{},tmp_path)
    assert runner.generate.call_count == 1


def test_long_manifest_and_fact_review_preserve_all_sections_and_claims(tmp_path):
    import pytest

    from studio.runner import RunnerError
    script={'video_id':'V','hook':'intro','cta':'end',
            'sections':[{'heading':str(i),'narration':'words','claim_ids':[f'F{i}']} for i in range(9)],
            'claims':[{'claim_id':f'F{i}','text':'fact'} for i in range(10)]}
    runner=Mock();runner.client=object();runner.bus.replay.return_value=[]
    runner.agents={a:Mock(version=1,system_prompt=lambda:'prompt') for a in ['production-manager','fact-checker']}
    env=Envelope(topic='scripts',key='V',actor='script-writer',payload=script)
    runner.generate.side_effect=lambda agent, inp, topic, **kwargs: Generated(payloads=[{'scenes':[
        {'scene_id':str(i),'order':i,'narration':s['narration']} for i,s in enumerate(inp.payload['sections'])]}],model='unit',tokens=0)
    result=generate_partitioned(runner,'production-manager',env,'scene-manifests',{},tmp_path)
    assert [s['scene_id'] for s in result.payloads[0]['scenes']]==[f'C{i//4+1}_S{i+1}' for i in range(9)]
    calls=runner.generate.call_args_list
    assert sum(len(c.args[1].payload['sections']) for c in calls)==9
    assert sum(len(c.args[1].payload['claims']) for c in calls)==10
    assert calls[1].args[1].payload['hook']=='' and calls[0].args[1].payload['cta']==''
    runner.generate.side_effect=[Generated(payloads=[{'verdict':v,'findings':[{'text':v}]}],model='unit',tokens=0) for v in ['pass','block','fail']]
    env=Envelope(topic='video-briefs',key='V',actor='desk',payload={'video_id':'V'})
    result=generate_partitioned(runner,'fact-checker',env,'review-results',{'script':script},tmp_path)
    assert result.payloads[0]['verdict']=='block' and len(result.payloads[0]['findings'])==3
    with pytest.raises(RunnerError): generate_partitioned(runner,'fact-checker',env,'review-results',{},tmp_path,chapter_minutes=0)
