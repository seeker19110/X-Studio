"""Exercise the installed default render and native Shorts workflow with real model backends."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from studio.factory import Factory
from studio.media import load_media_config


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--dossier',type=Path,default=Path('docs/rights/local-validation-vieneu.json'))
    parser.add_argument('--narration',type=Path,help='Optional reviewed narration; real models still plan, render and review it')
    parser.add_argument('--budget-tokens',type=int,default=1500000)
    parser.add_argument('--max-seconds',type=int,default=2700)
    parser.add_argument('--target-seconds',type=float,default=24)
    parser.add_argument('--tool-budget-tokens',type=int,default=350000)
    parser.add_argument('--retry-research',action='store_true')
    args=parser.parse_args()
    root=(args.resume or Path('output/real-validation')/datetime.now().strftime('default-factory-%Y%m%d-%H%M%S')).resolve()
    root.mkdir(parents=True,exist_ok=bool(args.resume))
    os.environ['STUDIO_POLICY_KEY_FILE']=str(root/'policy.key')
    cfg=load_media_config();cfg.output_dir=root/'media';cfg.upload_dir=root/'uploads'
    assert cfg.video['resolution']=='1920x1080' and cfg.video['layout']=='editorial'
    assert cfg.tts['provider']=='command' and cfg.tts.get('license')=='licensed' and cfg.production.get('automation')=='production'
    assert cfg.tts.get('rights_assessment',{}).get('publication_decision')=='COMMERCIAL_VOICE_USE_ALLOWED'
    spec=importlib.util.spec_from_file_location('live_intake',Path(__file__).with_name('live-upgrades-smoke.py'))
    intake=importlib.util.module_from_spec(spec);spec.loader.exec_module(intake)
    dossier=args.dossier
    declaration=json.loads(dossier.read_text(encoding='utf-8'))
    narration=json.loads(args.narration.read_text(encoding='utf-8')) if args.narration else []
    (root/'quality-profile.json').write_text(json.dumps({'video':cfg.video,'tts':cfg.tts,'render':cfg.render},ensure_ascii=False,indent=2),encoding='utf-8')
    Path('output/real-validation/default-factory-path.txt').write_text(str(root),encoding='utf-8')
    f=Factory(root/'studio.sqlite',cfg,demo=False)
    f.orch.runner.client.tool_budget_tokens=min(args.tool_budget_tokens,args.budget_tokens)
    for backend in getattr(f.orch.runner.client,'backends',[]):
        if hasattr(backend.client,'timeout'):backend.client.timeout=120.
    try:
        if args.retry_research:
            from uuid import NAMESPACE_URL, uuid5

            from studio.events import Envelope
            for brief in list(f.bus.replay('video-briefs'))[-1:]:
                if f.orch.latest('research-dossiers',brief.key) is not None:raise ValueError('Research already exists; do not replace it')
                retry=Envelope(event_id=str(uuid5(NAMESPACE_URL,brief.event_id+':research-budget-retry')),
                    topic='video-briefs',key=brief.key,actor='desk',payload=brief.payload,causation_id=brief.event_id)
                f.bus.publish(retry)
                f.orch._audit('validation.research_retry',{'source_event_id':brief.event_id,'retry_event_id':retry.event_id,
                    'tool_budget_tokens':f.orch.runner.client.tool_budget_tokens,'project_budget_tokens':args.budget_tokens},video_id=brief.key)
        if not args.resume:
            f.command('create',{'title':'Kiểm tra video đã xuất trước khi duyệt',
                'audience':'Người mới chỉnh sửa video','target_minutes':args.target_seconds/60,'format':'long','language':'vi',
                'budget_tokens':args.budget_tokens,'rights_dossier':declaration,'boundaries':[
                    f'Một video mới khoảng {args.target_seconds:g} giây, 4 cảnh; tổng khoảng 60–70 từ tiếng Việt, gồm hook và CTA. Cảnh ngắn 3–6 giây được chấp nhận cho bài thử này. Không thêm dấu ba chấm để kéo dài thời lượng.',
                    'Chỉ hướng dẫn quy trình: xem hình, nghe lời đọc và rà phụ đề trước khi duyệt bản xuất. Không demo giao diện.',
                    'Mở file video đã xuất để kiểm tra trước khi duyệt. Đọc phụ đề trong khi nghe để đối chiếu với lời đọc; không yêu cầu tắt tiếng để kiểm khớp lời.',
                    'Nội dung mới, không lặp kịch bản kiểm tra môi trường và sao lưu ở lượt thử trước.',
                    'Heading của mỗi phần mô tả ngắn 2–5 từ; bốn phần trong script khớp bốn cảnh. Không đặt heading là S1/S2.',
                    'Hook và CTA có trong lời đọc thật; không chèn câu đệm hoặc lặp số thứ tự.',
                    'Không đọc tên dự án hoặc nhãn hiệu, không có logo. Tập trung vào việc kiểm tra file video đã xuất.',
                    'Title, alt_titles và description không ghi số giây/phút hoặc hứa thời gian hoàn thành thao tác. Thời lượng video không phải thời gian kiểm một file.',
                    'Tư liệu là vector minh họa gốc, không logo, nhân vật, nhạc hoặc nguồn ngoài.',
                    'Chỉ render/review cục bộ. Không upload, không lên lịch YouTube; giữ gate publish chờ duyệt.',
                    'Mô tả không chép timestamp. Video ngắn không cần chapters.',
                    'Giọng mặc định VieNeu v3 Turbo, preset Ngọc Huyền, Apache-2.0. Không dùng Piper hoặc ghi công giọng cũ. Xem speech_profile, speech_rights và dossier hiện tại.',
                    'Thumbnail: overlay tối đa bốn từ, không dấu chấm câu, không lặp nguyên cụm trong tiêu đề. Style dẫn đúng câu đang có trong lời đọc, không trích bản cũ.',
                    *(['Giữ nguyên thứ tự và nguyên văn narration đã rà; không thêm hook/CTA khác. Các phần: '+json.dumps(narration,ensure_ascii=False)] if narration else []),
                ]})
        print(json.dumps({'root':str(root),'profile':'installed defaults; real models and media'}),flush=True)
        started=time.monotonic();previous=None
        while time.monotonic()-started<args.max_seconds:
            # Local production permission leaves publication and independent rights review pending.
            intake.authorize_local_plan(f,root,declaration,allowed_risks=frozenset({'brand','legal'}))
            for final in list(f.bus.replay('media-assets')):
                if final.payload['kind']=='final_video' and not (root/('rights-intake-'+final.event_id+'.json')).exists():
                    intake.prepare_rights(f,root,dossier,final)
            f.cycle();state=f.snapshot()
            (root/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            progress={'projects':[(p['id'],p['state'],p['manifest_version']) for p in state['projects']],
                      'gates':[(g['id'],g['kind']) for g in state['gates']],'queue':state['queue'],
                      'deferred':state['deferred']}
            if progress!=previous:print(json.dumps(progress,ensure_ascii=False),flush=True);previous=progress
            assert not list(f.bus.replay('publish-events'))
            if any(g['kind']=='escalation' for g in state['gates']):raise RuntimeError('Review or budget needs attention; inspect state.json')
            parent=next((p for p in state['projects'] if not p['origin']),None)
            if parent and 'PUB-'+parent['id'] in f.orch.gate.pending and len(state['projects'])==1 and state['queue']==state['deferred']==0:
                final=next(a for a in reversed(parent['media']) if a['kind']=='final_video')
                f.command('shorts',{'video_id':parent['id'],'manifest_version':parent['manifest_version'],
                    'source_checksum':final['checksum'],'title':parent['title'],'start_s':0,
                    'end_s':int(final['duration_s']*10)/10,'request_id':'default-native-short'})
            if (len(state['projects'])==2 and state['queue']==0 and state['deferred']==0
                    and all('PUB-'+p['id'] in f.orch.gate.pending for p in state['projects'])):
                assert all(p['reviews'].get(source,{}).get('verdict')=='pass' for p in state['projects'] for source in ['fact','quality','rights'])
                assert all(a['provider']!='fake' for p in state['projects'] for a in p['media'])
                assert all(f.orch.supervisor.budgets[p['id']].used<args.budget_tokens for p in state['projects'])
                child=next(p for p in state['projects'] if p['origin'])
                assert child['origin']['layout']=='reflow'
                with sqlite3.connect('file:'+str(root/'studio.sqlite')+'?mode=ro',uri=True) as db:
                    assert db.execute('select count(*) from upload_sessions').fetchone()[0]==0
                f.command('pause',{});f.cycle();state=f.snapshot()
                (root/'result.json').write_text(json.dumps({'status':'passed_before_publication',
                    'root':str(root),'elapsed_s':time.monotonic()-started,'models':'live_cli',
                    'budget':f.orch.supervisor.report(),'media_ledger':f.resources()['ledger'],'state':state,
                    'publish_events':0,'upload_sessions':0},ensure_ascii=False,indent=2),encoding='utf-8')
                print('DEFAULT WORKFLOW PASSED; both publication gates pending',flush=True);return
            time.sleep(.2)
        raise TimeoutError('Default real-model workflow did not complete within time limit')
    finally:f.close()


if __name__=='__main__':
    main()
