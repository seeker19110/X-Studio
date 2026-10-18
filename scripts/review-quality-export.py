"""Recompose local review exports from verified speech; never calls a publishing adapter."""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

from studio.media import probe_duration
from studio.render_cache import digest
from studio.speech import timed_captions, validate_words
from studio.timeline import Cue, srt, stamp

PAGE = """<!doctype html><html lang="vi"><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#f3f1e9;color:#132c38;font-family:"DejaVu Sans",sans-serif;width:1920px;height:1080px;overflow:hidden}
header{position:absolute;left:110px;right:110px;top:65px;display:flex;justify-content:space-between;align-items:center;font-size:23px;letter-spacing:2px}
.brand{font-weight:800}.brand span{font-weight:400;color:#657573;margin-left:24px;font-size:20px;letter-spacing:1px}
.tag{color:#46625f;font-size:19px;letter-spacing:1.8px}.copy{position:absolute;left:110px;top:225px;width:780px}
.kicker{color:#34786c;font-size:25px;letter-spacing:2px;font-weight:700;margin-bottom:28px}
h1{font-size:101px;line-height:1.08;letter-spacing:-4px;margin:0;white-space:pre-line;font-weight:750}
.sub{font-size:30px;line-height:1.5;max-width:700px;color:#546965;margin-top:32px}
.art{position:absolute;left:970px;top:160px;width:840px;height:520px;border:2px solid #d5dfd7;border-radius:42px;background:#e3ece2;overflow:hidden}
.art svg{width:100%;height:100%}.rail{position:absolute;left:110px;right:110px;bottom:287px;display:flex;gap:22px}
.step{flex:1;display:flex;align-items:center;gap:17px;background:#e6e8df;border:2px solid transparent;padding:15px 21px;border-radius:18px;font-size:24px}
.step b{font-size:22px;background:#d2ddd1;border-radius:50%;width:42px;height:42px;display:flex;align-items:center;justify-content:center}
.step.active{background:#163e39;color:#fff}.step.active b{background:#f4af5a;color:#132c38}
.captions{position:absolute;left:100px;right:100px;bottom:72px;height:183px;background:#132c38;border-radius:27px}
body.portrait{width:1080px;height:1920px}.portrait header{left:90px;right:140px;top:125px;font-size:24px;letter-spacing:1px}
.portrait .brand span{display:block;margin:13px 0 0;font-size:17px}.portrait .tag{font-size:17px;max-width:210px;text-align:right;line-height:1.5}
.portrait .copy{left:90px;top:295px;width:850px}.portrait .kicker{font-size:25px;margin-bottom:30px}
.portrait h1{font-size:85px;letter-spacing:-3px;line-height:1.12}.portrait .sub{font-size:31px;line-height:1.45;margin-top:27px;max-width:820px}
.portrait .art{left:90px;top:745px;width:850px;height:565px;border-radius:34px}
.portrait .rail{left:90px;right:140px;bottom:480px;gap:13px}
.portrait .step{flex-direction:column;justify-content:center;gap:12px;padding:12px 5px;font-size:21px;border-radius:18px;height:112px;white-space:nowrap}
.portrait .step b{font-size:23px;width:42px;height:42px}
.portrait .captions{left:90px;right:140px;bottom:260px;height:190px;border-radius:25px}
</style><header><div class="brand">X STUDIO<span>BÀI THỬ NỘI BỘ</span></div><div class="tag">QUY TRÌNH<br>SỬA VIDEO</div></header>
<div class="copy"><div class="kicker"></div><h1></h1><div class="sub"></div></div><div class="art"></div>
<div class="rail"><div class="step"><b>1</b>Kiểm tra</div><div class="step"><b>2</b>Sao lưu</div><div class="step"><b>3</b>Sửa cảnh</div><div class="step"><b>4</b>Xem lại</div></div><div class="captions"></div>
<script>window.showStage = function(data){document.querySelector('.kicker').textContent=data.kicker;document.querySelector('h1').textContent=data.title;document.querySelector('.sub').textContent=data.sub;document.querySelector('.art').innerHTML=data.art;document.querySelectorAll('.step').forEach((el,i)=>el.classList.toggle('active',data.step===i||data.step===4));};</script></html>"""


def svg(content: str) -> str:
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 600" style="font-family:DejaVu Sans,sans-serif">' + content + '</svg>'


def label(x: int, y: int, text: str, size: int = 29, color: str = '#163e39') -> str:
    return f'<text x="{x}" y="{y}" text-anchor="middle" font-size="{size}" font-weight="700" fill="{color}">{text}</text>'


def file_card(x: int, y: int, fill: str = '#f9f7f0') -> str:
    return (f'<rect x="{x}" y="{y}" width="220" height="245" rx="25" fill="{fill}" stroke="#163e39" stroke-width="5"/>'
            f'<rect x="{x+22}" y="{y+24}" width="176" height="135" rx="13" fill="#34786c"/>'
            f'<path d="M{x+86} {y+56}l57 35-57 35z" fill="#f4af5a"/>'
            f'<path d="M{x+34} {y+191}h152M{x+34} {y+216}h105" stroke="#9bb7ac" stroke-width="9" stroke-linecap="round"/>')


def artwork() -> list[str]:
    copy = (file_card(95,150) + file_card(585,150,'#f6d5a4') +
            '<path d="M355 265h172m-32-30 32 30-32 30" fill="none" stroke="#34786c" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>' +
            label(205,455,'BẢN GỐC') + label(695,455,'BẢN SAO') + label(450,95,'Giữ một bản an toàn',32))
    checks = '<rect x="95" y="90" width="710" height="420" rx="30" fill="#f9f7f0" stroke="#163e39" stroke-width="5"/>'
    for y, text in [(190,'Công cụ sẵn sàng'),(300,'Thư mục làm việc'),(410,'Bản sao an toàn')]:
        checks += f'<circle cx="175" cy="{y-10}" r="30" fill="#34786c"/><path d="M160 {y-11}l11 12 22-26" fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
        checks += f'<text x="235" y="{y}" font-size="34" font-weight="600" fill="#163e39">{text}</text>'
    timeline = label(450,100,'Chỉ thay phần cần sửa',34)
    for i in range(4):
        x=72+i*195; fill='#f4af5a' if i==1 else '#f9f7f0'
        timeline += f'<rect x="{x}" y="200" width="170" height="145" rx="18" fill="{fill}" stroke="#163e39" stroke-width="5"/>'
        timeline += f'<path d="M{x+66} 240l48 32-48 32z" fill="#34786c"/>'
        timeline += label(x+85,390,f'CẢNH {i+1}',25)
    timeline += '<path d="M352 155v235m-16-235h32l-16 22z" fill="#163e39" stroke="#163e39" stroke-width="4"/><path d="M80 455h740" stroke="#a6b9ad" stroke-width="10" stroke-linecap="round"/>'
    review = '<rect x="130" y="85" width="640" height="365" rx="25" fill="#f9f7f0" stroke="#163e39" stroke-width="6"/><rect x="155" y="110" width="590" height="215" rx="15" fill="#34786c"/><path d="M422 153l88 59-88 59z" fill="#f4af5a"/><rect x="210" y="350" width="480" height="18" rx="9" fill="#163e39"/><rect x="280" y="387" width="340" height="18" rx="9" fill="#9bb7ac"/>'
    review += label(450,520,'Hình · Tiếng · Phụ đề',31)
    summary = ''
    for i,(x,y,text) in enumerate([(80,110,'KIỂM TRA'),(475,110,'SAO LƯU'),(80,335,'SỬA CẢNH'),(475,335,'XEM LẠI')]):
        summary += f'<rect x="{x}" y="{y}" width="345" height="155" rx="26" fill="{("#163e39" if i==3 else "#f9f7f0")}" stroke="#163e39" stroke-width="4"/>'
        summary += label(x+172,y+65,f'0{i+1}',34,'#f4af5a' if i==3 else '#34786c')
        summary += label(x+172,y+116,text,27,'#fff' if i==3 else '#163e39')
    return [svg(copy),svg(checks),svg(copy),svg(timeline),svg(review),svg(summary)]


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args,cwd=cwd,capture_output=True,text=True,check=True,timeout=600)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();source=args.source.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    prior=json.loads((source/'final-live-result.json').read_text(encoding='utf-8'))
    originals={p['final_video']['path']:digest(Path(p['final_video']['path'])) for p in prior['active_projects']}
    parent=prior['active_projects'][0]['video_id']
    with sqlite3.connect('file:'+str(source/'studio.sqlite')+'?mode=ro',uri=True) as db:
        base=next(json.loads(row[0])['payload'] for row in db.execute(
            "select body from events where topic='scene-manifests' and key=? order by seq desc", (parent,))
            if json.loads(row[0])['payload']['version']==1)
    parts=[];all_words=[];offset=0.;narrations=[]
    for scene in base['scenes']:
        audio=Path(scene['asset_refs']['scene_audio']);timing=Path(scene['asset_refs']['scene_timing'])
        data=json.loads(timing.read_text(encoding='utf-8'));assert data['sha256']==digest(audio)
        words=validate_words(data['words'],probe_duration(audio))
        span=math.ceil((words[-1]['start']+words[-1]['duration']+.28)*25)/25
        parts.append({'audio':str(audio),'audio_sha256':digest(audio),'timing':str(timing),'timing_sha256':digest(timing),
                      'narration':scene['narration'],'duration_s':span,'offset':offset,'words':words})
        all_words.extend({**w,'start':offset+w['start']} for w in words)
        narrations.append(scene['narration']);offset+=span
    duration=round(offset,3)
    inputs=[];graph=[]
    for i,part in enumerate(parts):
        inputs+=['-i',part['audio']]
        graph.append(f'[{i}:a]atrim=0:{part["duration_s"]},apad,atrim=0:{part["duration_s"]},asetpts=N/SR/TB[a{i}]')
    graph.append(''.join(f'[a{i}]' for i in range(len(parts)))+f'concat=n={len(parts)}:v=0:a=1,loudnorm=I=-14:TP=-1.5:LRA=7,aresample=48000[voice]')
    run(['ffmpeg','-v','error','-y',*inputs,'-filter_complex',';'.join(graph),'-map','[voice]','-ac','2','-c:a','pcm_s16le',str(out/'voice.wav')])
    def word_start(value: str) -> float: return next(w['start'] for w in all_words if w['text']==value)
    starts=[0.]+[round(word_start(w)*25)/25 for w in ['Một','Hai','Ba','Bốn','Bài']]+[duration]
    graphics=artwork()
    stages=[{'kicker':k,'title':t,'sub':s,'step':step,'art':art} for (k,t,s,step),art in zip([
        ('TRƯỚC KHI SỬA','Sửa video.\nGiữ bản gốc.','4 bước, đúng thứ tự.',4),
        ('BƯỚC 01 · KIỂM TRA','Chuẩn bị\nmôi trường.','Kiểm tra công cụ và thư mục làm việc.',0),
        ('BƯỚC 02 · SAO LƯU','Sao lưu\ntrước khi sửa.','Thư mục mới. Bản gốc giữ nguyên.',1),
        ('BƯỚC 03 · SỬA CẢNH','Chỉ sửa\ncảnh cần đổi.','Các cảnh còn lại giữ nguyên.',2),
        ('BƯỚC 04 · XEM LẠI','Xem hình.\nNghe tiếng.','Đối chiếu phụ đề rồi mới duyệt.',3),
        ('CHO LẦN SỬA TIẾP THEO','Kiểm tra.\nSao lưu.\nXem lại.','Làm đủ bước trước khi duyệt.',4)],graphics,strict=True)]
    (out/'layout.html').write_text(PAGE,encoding='utf-8')
    exports=[]
    with sync_playwright() as runtime:
        browser=runtime.chromium.launch(headless=True,executable_path=os.environ.get('STUDIO_WEBSITE_CHROMIUM'))
        for name,width,height,max_chars in [('video-chinh-1080p',1920,1080,38),('shorts-1080x1920',1080,1920,26)]:
            directory=out/name;directory.mkdir()
            page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
            page.set_content(PAGE);page.evaluate("portrait=>document.body.classList.toggle('portrait',portrait)",height>width)
            page.evaluate('document.fonts.ready')
            for i,stage in enumerate(stages):
                page.evaluate('stage=>showStage(stage)',stage)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(directory/f'{i}.png'))
            cues=[]
            for part in parts:
                cues.extend(timed_captions(part['words'],part['offset'],1.,text=part['narration'],max_chars=max_chars))
            # Editorial breaks keep Vietnamese phrases together and avoid orphan words.
            breaks={
                'Sửa video mà không mất bản gốc?':('Sửa video mà không mất bản gốc?','Sửa video mà\nkhông mất bản gốc?'),
                'Hai: sao lưu sang thư mục mới, giữ bản gốc.':('Hai: sao lưu sang thư mục mới,\ngiữ bản gốc.','Hai: sao lưu sang\nthư mục mới, giữ bản gốc.'),
                'Ba: chỉ sửa một cảnh, không dựng lại cả video.':('Ba: chỉ sửa một cảnh,\nkhông dựng lại cả video.','Ba: chỉ sửa một cảnh,\nkhông dựng lại cả video.'),
                'Bốn: xem bản cuối và phụ đề rồi mới duyệt.':('Bốn: xem bản cuối và phụ đề\nrồi mới duyệt.','Bốn: xem bản cuối\nvà phụ đề rồi mới duyệt.'),
                'Bài thử nội bộ X Studio: lần sau, hãy kiểm tra,':('Bài thử nội bộ X Studio:\nlần sau, hãy kiểm tra,','Bài thử nội bộ X Studio:\nlần sau, hãy kiểm tra,')}
            for cue in cues:
                phrase=' '.join(cue['text'].split())
                if phrase in breaks: cue['text']=breaks[phrase][height>width]
            assert ' '.join(' '.join(c['text'].split()) for c in cues)==' '.join(narrations)
            assert all(0 <= c['start'] < c['end'] <= duration for c in cues)
            assert all(len(c['text'].splitlines())<=2 for c in cues)
            assert all(len(line)<=max_chars for c in cues for line in c['text'].splitlines())
            assert not any(len(c['text'].splitlines())==2 and len(c['text'].splitlines()[-1].split())==1 for c in cues)
            font_size=52; margin_v=126 if width>height else 310
            subtitle_width=width-(200 if width>height else 230)
            measured=page.evaluate("""data=>{const c=document.createElement('canvas').getContext('2d');c.font=data.font;
                return data.lines.map(t=>c.measureText(t).width)}""",
                {'font':f'{font_size}px "DejaVu Sans"','lines':[line for c in cues for line in c['text'].splitlines()]})
            assert max(measured)<subtitle_width-40
            directory.joinpath('captions.srt').write_text(srt([Cue('review',c['start'],c['end'],c['text']) for c in cues]),encoding='utf-8')
            ass=(f'[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n'
                 '[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n'
                 f'Style: Default,DejaVu Sans,{font_size},&H00FFFFFF,&H00FFFFFF,&H00132C38,&H00132C38,0,0,0,0,100,100,0,0,1,0,0,2,100,{100 if width>height else 130},{margin_v},1\n'
                 '[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n')
            for cue in cues:
                start=stamp(cue['start'],'.')[:-1];end=stamp(cue['end'],'.')[:-1]
                caption=cue['text'].replace('\n',r'\N')
                ass+=f'Dialogue: 0,{start},{end},Default,,0,0,0,,{caption}\n'
            directory.joinpath('captions.ass').write_text(ass,encoding='utf-8')
            (directory/'captions.json').write_text(json.dumps(cues,ensure_ascii=False,indent=2),encoding='utf-8')
            page.close()
            command=['ffmpeg','-v','error','-y'];filters=[]
            for i in range(len(stages)):
                command+=['-loop','1','-framerate','25','-t',str(round(starts[i+1]-starts[i],3)),'-i',str(directory/f'{i}.png')]
                filters.append(f'[{i}:v]setsar=1,setpts=PTS-STARTPTS,format=yuv420p[v{i}]')
            command+=['-i',str(out/'voice.wav')]
            filters.append(''.join(f'[v{i}]' for i in range(len(stages)))+f'concat=n={len(stages)}:v=1:a=0,subtitles=captions.ass[v]')
            destination=out/(name+'.mp4')
            run([*command,'-filter_complex',';'.join(filters),'-map','[v]','-map',f'{len(stages)}:a:0',
                '-t',str(duration),'-r','25','-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p',
                '-threads','2','-filter_complex_threads','1','-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-movflags','+faststart',str(destination)],directory)
            probe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(destination)]).stdout)
            run(['ffmpeg','-v','error','-threads','2','-i',str(destination),'-f','null','-'])
            for i,when in enumerate([1.,5.5,9.,14.,18.,23.]):
                run(['ffmpeg','-v','error','-threads','2','-ss',str(when),'-i',str(destination),'-frames:v','1','-threads','2',str(directory/f'frame-{i}.jpg')])
            exports.append({'file':str(destination),'sha256':digest(destination),'probe':probe,'full_decode':'passed',
                            'captions':str(directory/'captions.srt'),'cue_count':len(cues),'max_text_width_px':max(measured),
                            'subtitle_safe_width_px':subtitle_width,'resolution':f'{width}x{height}'})
            print(json.dumps({'exported':str(destination),'resolution':f'{width}x{height}','caption_pages':len(cues)},ensure_ascii=False),flush=True)
        browser.close()
    assert all(digest(Path(path))==sha for path,sha in originals.items())
    result={'status':'local_quality_preview_not_published','source_root':str(source),'source_project_id':parent,
            'original_exports_preserved':originals,'speech_sources':parts,'duration_s':duration,'visual_starts':starts,
            'visual_source':'Original HTML/CSS and inline SVG; rasterized at each native output size, no remote images.',
            'voice_processing':'Original verified stock HoaiMy speech, S2 before the redundant Tiếp theo edit; trailing silence trimmed to word end + 0.28 s, -14 LUFS target, -1.5 dBTP, 48 kHz stereo.',
            'subtitle_processing':'Source punctuation restored only on exact normalized token match; native per-format reflow, <=2 lines, provider word boundaries.',
            'rights_scope':'private_local_validation','publication':'No publishing adapter or gate decision called. These are local review exports, not new approvals in the factory.',
            'exports':exports}
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
