from pathlib import Path

import pytest

from studio.events import Envelope, MediaAsset
from studio.factory import Factory
from studio.media import MediaConfig


@pytest.mark.parametrize('license,expected', [('licensed', 'licensed'), ('cc-by', 'cc-by'), ('unknown', 'unknown')])
def test_short_uses_current_voice_rights_without_obsolete_take_credits(tmp_path, monkeypatch, license, expected):
    monkeypatch.setenv('STUDIO_POLICY_KEY_FILE', str(tmp_path/'policy.key'))
    factory = Factory(tmp_path/'db', MediaConfig(output_dir=tmp_path/'out',
                      production={'automation':'production'}), demo=True)
    try:
        factory.command('create', {'title':'Source', 'target_minutes':.2})
        for _ in range(40):
            factory.cycle()
        parent = factory.snapshot()['projects'][0]
        vid = parent['id']
        manifest = factory.orch.manifest(vid)
        path = manifest.scenes[0].asset_refs['scene_audio']
        current = next(MediaAsset.model_validate(e.payload) for e in reversed(list(
            factory.bus.replay('media-assets', vid))) if e.payload['path']==path)
        current.provenance.license = license
        current.provenance.attribution = 'CURRENT_VOICE_NOTICE'
        current.provenance.source_url = 'https://example.org/current-voice'
        factory.bus.publish(Envelope(topic='media-assets', key=vid, actor='renderer', payload=current.model_dump()))
        historical = current.model_copy(deep=True)
        historical.path = str(Path(path).parent/'obsolete-voice.wav')
        historical.manifest_version = 0
        historical.provenance.license = 'unknown'
        historical.provenance.attribution = 'OBSOLETE_PIPER_NOTICE'
        factory.bus.publish(Envelope(topic='media-assets', key=vid, actor='renderer', payload=historical.model_dump()))
        final = next(a for a in reversed(parent['media']) if a['kind']=='final_video')
        factory.command('shorts', {'video_id':vid, 'manifest_version':parent['manifest_version'],
            'source_checksum':final['checksum'], 'title':'New voice excerpt',
            'start_s':0, 'end_s':1, 'request_id':'new-voice'})
        for _ in range(40):
            factory.cycle()
        child = next(p for p in factory.snapshot()['projects'] if p['id']!=vid)
        exported = next(a for a in reversed(child['media']) if a['kind']=='final_video')
        provenance = exported['provenance']
        assert provenance['license']==expected
        assert 'CURRENT_VOICE_NOTICE' in provenance['attribution']
        assert 'OBSOLETE_PIPER_NOTICE' not in provenance['attribution']
        captions=next(e.payload for e in reversed(list(factory.bus.replay('media-assets',child['id'])))
                      if e.payload['kind']=='captions')
        assert captions['provenance']['license']=='generated'
        assert captions['provenance']['source_url'] is None
        from studio.orchestrator import _with_provenance
        trigger=next(e for e in reversed(list(factory.bus.replay('media-assets',child['id'])))
                     if e.payload['kind']=='final_video')
        package=_with_provenance(trigger,factory.orch)
        scene_records=[a for a in package['assets'] if a['kind'] in {'scene_video','scene_audio'}]
        assert scene_records and all(a['usage']=='current' for a in scene_records)
    finally:
        factory.close()
