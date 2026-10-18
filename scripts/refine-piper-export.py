"""Refine the existing local preview through real reviewers; never approve publication."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sqlite3
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import yaml

from studio.backup import restore_backup
from studio.events import CutList, Envelope, Repair, Script
from studio.factory import Factory
from studio.media import load_media_config
from studio.orchestrator import _with_provenance


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--max-seconds',type=float,default=1800)
    args=parser.parse_args();root=args.root.resolve();workspace=root/'workspace'
    if not workspace.exists():restore_backup(args.source.resolve()/'backup-final',workspace)
    profile=yaml.safe_load((workspace/'media.restore.yaml').read_text())
    cfg=load_media_config();cfg.output_dir=Path(profile['output_dir']);cfg.upload_dir=Path(profile['upload_dir'])
    assessment=cfg.tts['rights_assessment']
    assert assessment['publication_decision']=='NOT_CLEARED' and cfg.tts['license']=='unknown'
    os.environ['STUDIO_POLICY_KEY_FILE']=str(workspace/'policy.key')
    factory=Factory(workspace/'studio.sqlite',cfg,demo=False)
    factory.orch.runner.client.tool_budget_tokens=350000
    narration=json.loads((root/'refined-narration.json').read_text())
    try:
        state=factory.snapshot();identity_file=root/'export-identity.json'
        if not identity_file.exists():
            identity_file.write_text(json.dumps({'source_project_ids':[p['id'] for p in state['projects']],
                'parent_id':next(p['id'] for p in state['projects'] if not p['origin']),
                'baseline_media_ledger':factory.resources()['ledger']},ensure_ascii=False,indent=2),encoding='utf-8')
        identity=json.loads(identity_file.read_text());original_ids=set(identity['source_project_ids'])
        vid=identity['parent_id'];child_id=next((p['id'] for p in state['projects'] if p['id'] not in original_ids),None)
        script_event=factory.orch.latest('scripts',vid)
        edit_id=str(uuid5(NAMESPACE_URL,str(root)+':speech-clarity-script'))
        if not any(e.event_id==edit_id for e in factory.bus.replay('scripts',vid)):
            script=Script.model_validate(script_event.payload);script.version+=1
            assert len(script.sections)==len(narration)==4
            for section,text in zip(script.sections,narration,strict=True):section.narration=text
            script.hook=narration[0].split('.')[0]+'.';script.cta=narration[-1].split('. ')[-1]
            script.word_count=len(' '.join(narration).split());script.estimated_minutes=20/60
            manifest=factory.orch.manifest(vid);source_version=manifest.version
            factory.orch.gate.invalidate('PUB-'+vid)
            factory.bus.publish(Envelope(event_id=edit_id,topic='scripts',key=vid,actor='console:editor',payload=script.model_dump()))
            cut=CutList(video_id=vid,manifest_version=source_version,decision='repair',repairs=[
                Repair(scene_id=scene.scene_id,action='regenerate_audio',new_narration=text,
                       reason='Operator requested clearer Piper speech; preserve existing images and section claims')
                for scene,text in zip(manifest.scenes,narration,strict=True)])
            factory.bus.publish(Envelope(event_id=str(uuid5(NAMESPACE_URL,str(root)+':speech-clarity-cut')),
                topic='cut-lists',key=vid,actor='console:editor',payload=cut.model_dump()))
            factory.orch._audit('validation.speech_refinement',{'operator_instruction':
                'Xử lý nốt các cụm giọng đọc chưa rõ và làm rõ quyền phát hành của Piper, rồi xuất lại video chính và Shorts cho tôi xem, chưa đăng YouTube.',
                'source_workspace':str(args.source.resolve()),'source_manifest_version':source_version,
                'source_script_event_id':script_event.event_id,'speech_rights':assessment,
                'images':'reuse original verified assets; no image generation','publication_approval':False},video_id=vid)
        factory.command('run',{})
        started=time.monotonic();previous=None;requested=child_id is not None
        print(json.dumps({'root':str(root),'parent':vid,'voice':'real Piper','models':'live CLI','release':'NOT_CLEARED'}),flush=True)
        while time.monotonic()-started<args.max_seconds:
            state=factory.snapshot()
            for project in state['projects']:
                if project['id'] not in original_ids:child_id=project['id']
            for event in list(factory.bus.replay('media-assets')):
                asset=event.payload
                if asset['kind']!='final_video':continue
                if not ((asset['video_id']==vid and asset['manifest_version']>1) or asset['video_id']==child_id):continue
                receipt=root/('rights-intake-'+event.event_id+'.json')
                if not receipt.exists():
                    extra=_with_provenance(event,factory.orch)
                    extra['latest_operator_scope']='2026-10-17: fix speech, clarify licensing, local preview only; no YouTube.'
                    generation=factory.orch.runner.run_context('rights-checker',event,extra=extra)
                    receipt.write_text(json.dumps({'source_event_id':event.event_id,'speech_rights':assessment,
                        'generation':dataclasses.asdict(generation)},ensure_ascii=False,indent=2),encoding='utf-8')
                    print(json.dumps({'rights_intake':asset['video_id'],'tokens':generation.tokens}),flush=True)
            factory.cycle();state=factory.snapshot()
            (root/'state-full.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            progress={'projects':[(p['id'],p['state'],p['manifest_version']) for p in state['projects']],
                      'queue':state['queue'],'deferred':state['deferred']}
            if progress!=previous:print(json.dumps(progress),flush=True);previous=progress
            assert not list(factory.bus.replay('publish-events'))
            with sqlite3.connect('file:'+str(workspace/'studio.sqlite')+'?mode=ro',uri=True) as db:
                assert db.execute('select count(*) from upload_sessions').fetchone()[0]==0
            target_escalations={'ESC-'+target for target in (vid,child_id) if target}
            if any(g['kind']=='escalation' and g['id'] in target_escalations for g in state['gates']):
                raise RuntimeError('Inspect target model or budget escalation; historical projects remain separate')
            main_project=next(p for p in state['projects'] if p['id']==vid)
            complete=set(main_project['reviews'])>={'fact','quality','rights'} and main_project['manifest_version']>1
            if complete and not requested and state['queue']==state['deferred']==0:
                feedback=root/'pending-refinement.json';applied=root/'refinement-applied.json'
                if feedback.exists() and not applied.exists():
                    correction=json.loads(feedback.read_text());manifest=factory.orch.manifest(vid)
                    if factory.orch.desk.repair_rounds[vid]>=2:raise RuntimeError('No refinement round remains')
                    scene=next(s for s in manifest.scenes if s.scene_id==correction['scene_id'])
                    script=Script.model_validate(factory.orch.latest('scripts',vid).payload);script.version+=1
                    script.sections[scene.order].narration=correction['narration']
                    script.word_count=sum(len(s.narration.split()) for s in script.sections)
                    factory.orch.gate.invalidate('PUB-'+vid)
                    factory.bus.publish(Envelope(topic='scripts',key=vid,actor='console:editor',payload=script.model_dump()))
                    repairs=[Repair(scene_id=scene.scene_id,action='unlock',reason=correction['reason']),
                             Repair(scene_id=scene.scene_id,action='regenerate_audio',reason=correction['reason'],
                                    new_narration=correction['narration'])]
                    cut=CutList(video_id=vid,manifest_version=manifest.version,decision='repair',repairs=repairs)
                    factory.bus.publish(Envelope(topic='cut-lists',key=vid,actor='console:editor',payload=cut.model_dump()))
                    applied.write_text(json.dumps({'source_manifest':manifest.model_dump(),'correction':correction,
                        'script_event_source':'console:editor; operator-authorized speech clarification'},ensure_ascii=False,indent=2),encoding='utf-8')
                    continue
                final=next(a for a in reversed(main_project['media']) if a['kind']=='final_video')
                factory.command('shorts',{'video_id':vid,'manifest_version':main_project['manifest_version'],
                    'source_checksum':final['checksum'],'title':main_project['title'],'start_s':0,
                    'end_s':int(final['duration_s']*10)/10,'request_id':'piper-clear-'+root.name})
                requested=True
            targets=[p for p in state['projects'] if p['id'] in {vid,child_id}]
            if (requested and len(targets)==2 and state['queue']==state['deferred']==0
                    and all(set(p['reviews'])>={'fact','quality','rights'} for p in targets)):
                factory.command('pause',{});factory.cycle();final_state=factory.snapshot()
                final_targets=[p for p in final_state['projects'] if p['id'] in {vid,child_id}]
                result={'status':'completed_local_preview_release_not_cleared','root':str(root),
                    'workspace':str(workspace),'source':str(args.source.resolve()),'models':'live_cli',
                    'state_view':'requested exports only; historical source Short retained in state-full.json and database',
                    'state':{**final_state,'projects':final_targets},'budget':factory.orch.supervisor.report(),
                    'media_ledger':factory.resources()['ledger'],'speech_rights':assessment,
                    'publish_events':0,'upload_sessions':0,'elapsed_s':time.monotonic()-started}
                (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                (root/'state-full.json').write_text(json.dumps(final_state,ensure_ascii=False,indent=2),encoding='utf-8')
                print('LOCAL EXPORTS COMPLETE; publication not approved',flush=True);return
            time.sleep(.2)
        raise TimeoutError('Inspect state-full.json before resuming')
    finally:factory.close()


if __name__=='__main__':main()
