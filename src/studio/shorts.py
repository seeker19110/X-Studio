"""Local portrait excerpts from registered final videos, aligned to existing caption cues."""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, Self
from uuid import NAMESPACE_URL, uuid5

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .events import (
    ID_PATTERN,
    Envelope,
    MediaAsset,
    MetadataPackage,
    Scene,
    SceneManifest,
    Script,
    ScriptSection,
)
from .media import MediaConfig, frame_size, make_media
from .presentation import caption_width, write_captions
from .production import component_provenance, finish
from .render_cache import digest, fingerprint
from .speech import punctuated_words, timed_captions, validate_words
from .timeline import Cue, caption_pages, srt


class ShortsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    video_id: str = Field(pattern=ID_PATTERN)
    manifest_version: int = Field(ge=1,strict=True)
    source_checksum: str = Field(min_length=1,max_length=64)
    title: str = Field(min_length=1,max_length=200)
    start_s: float = Field(ge=0,allow_inf_nan=False)
    end_s: float = Field(gt=0,allow_inf_nan=False)
    fit: Literal['reflow','contain','cover'] = 'reflow'
    focus_x: float = Field(default=.5,ge=0,le=1,allow_inf_nan=False)
    request_id: str = Field(min_length=1,max_length=128,pattern=r'^[a-zA-Z0-9_-]+$')

    @model_validator(mode='after')
    def interval(self) -> Self:
        if not .5 <= self.end_s-self.start_s <= 180: raise ValueError('Chọn đoạn từ 0,5 đến 180 giây')
        return self


def read_captions(path: Path) -> list[Cue]:
    text = path.read_text(encoding='utf-8-sig').replace('\r\n','\n')
    cues = []
    def seconds(value: str) -> float:
        hour,minute,second,millisecond = map(int,re.split(r'[:,.]',value))
        return hour*3600+minute*60+second+millisecond/1000
    for chunk in re.split(r'\n\s*\n',text.strip()):
        match = re.search(r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\n(.+)',chunk,re.S)
        if match:
            start,end = seconds(match[1]),seconds(match[2])
            if end > start: cues.append(Cue('CLIP',start,end,match[3].strip()))
    if not cues: raise ValueError('Cần phụ đề SRT hợp lệ để giữ trọn lời đọc khi cắt')
    return sorted(cues,key=lambda c:c.start)


def render_short(source: Path, captions: Path, destination: Path, request: ShortsRequest, cfg: MediaConfig,
                 *, simulated: bool = False, reflow: dict | None = None) -> dict:
    cues = [c for c in read_captions(captions) if c.end > request.start_s and c.start < request.end_s]
    if not cues: raise ValueError('Đoạn chọn không có lời đọc trong phụ đề')
    start,end = cues[0].start,cues[-1].end
    if end-start > 180: raise ValueError('Sau khi căn phụ đề, đoạn vượt 180 giây; chọn đoạn ngắn hơn')
    checksum = digest(source)
    if request.fit=='reflow' and not reflow and not simulated: raise ValueError('Cần tư liệu cảnh nguồn để dàn lại; chọn giữ khung hoặc cắt mép cho video cũ')
    identity = fingerprint(['scene-ranges-v2',checksum,digest(captions),request.model_dump(),cfg.video,reflow])
    receipt = destination / 'derived.json'
    try:
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        if saved['fingerprint'] == identity and all(digest(destination / name)==sha for name,sha in saved['files'].items()): return saved['result']
    except (ValueError,OSError,KeyError,TypeError): pass
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.short-',dir=destination) as temp:
        root = Path(temp)
        subtitle = root / 'captions_v1.srt'
        portrait_cues=[Cue('CLIP',c.start-start,c.end-start,c.text) for c in cues]
        if reflow: portrait_cues=[Cue('CLIP',**c) for c in reflow['captions']]
        subtitle.write_text(srt(portrait_cues),encoding='utf-8')
        video = root / 'final_v1.mp4'; poster = root / 'A.jpg'
        ass=root/'captions_v1.ass'
        if reflow: write_captions(portrait_cues,frame_size(cfg.video,'9:16'),ass)
        if simulated:
            video.write_text(json.dumps({'simulated':True,'source_sha256':checksum,'start_s':start,'end_s':end}),encoding='utf-8')
            Image.new('RGB',(1280,720),'#263b58').save(poster,quality=85)
        else:
            binary = str(cfg.video.get('binary','ffmpeg')); width,height = map(int,frame_size(cfg.video,'9:16').split('x'))
            fit = (f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101a2c'
                   if request.fit == 'contain' else f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}:(iw-ow)*{request.focus_x}:(ih-oh)/2')
            try:
                clip_source=source; clip_start=start
                if reflow:
                    native=copy.deepcopy(cfg); native.output_dir=root
                    suite=make_media(native)
                    clip_source=root/'portrait-source.mp4';clip_start=start-reflow['offset']
                    suite.video.assemble([(Path(s['frame']),Path(s['audio']),s['duration_s']) for s in reflow['segments']],
                                         clip_source,int(cfg.video.get('fps',30)),frame_size(cfg.video,'9:16'))
                subprocess.run([binary,'-v','error','-threads','2','-ss',str(clip_start),'-i',str(clip_source),'-t',str(end-start),
                                '-map','0:v:0','-map','0:a:0','-vf',fit+',setsar=1,format=yuv420p','-r',str(cfg.video.get('fps',30)),
                                '-c:v','libx264','-preset','medium','-threads','2','-c:a','aac','-ar','48000','-ac','2',
                                '-movflags','+faststart',str(video)],capture_output=True,check=True,timeout=float(cfg.render.get('timeout_s',600)))
                if reflow: finish(suite.video,video,ass,None,'')
                probe = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)],
                                        capture_output=True,check=True,timeout=30)
                data = json.loads(probe.stdout)
                if {s['codec_type'] for s in data['streams']} != {'video','audio'} or abs(float(data['format']['duration'])-(end-start))>.15:
                    raise ValueError('Clip không đạt kiểm tra thời lượng hoặc stream')
                subprocess.run([binary,'-v','error','-threads','2','-ss','0.1','-i',str(video),'-frames:v','1','-threads','2',str(root / 'poster.png')],
                               capture_output=True,check=True,timeout=30)
                with Image.open(root / 'poster.png') as image: ImageOps.pad(image.convert('RGB'),(1280,720),color='#101a2c').save(poster,quality=85)
            except (OSError,subprocess.SubprocessError,KeyError,TypeError) as exc: raise ValueError('Không dựng được clip dọc; kiểm tra FFmpeg và media nguồn') from exc
        for thumb in reflow.get('thumbnails',[]) if reflow else []:
            if digest(Path(thumb['path']))!=thumb['sha256']:raise ValueError('Thumbnail nguồn đã thay đổi')
            shutil.copyfile(thumb['path'],root/(thumb['variant_id']+'.jpg'))
        if digest(source) != checksum: raise ValueError('Media nguồn đã thay đổi trong khi cắt')
        files = {}
        extra=[root/(t['variant_id']+'.jpg') for t in reflow.get('thumbnails',[]) if t['variant_id']!='A'] if reflow else []
        for file in [video,subtitle,poster,*extra,*([ass] if reflow else [])]:
            files[file.name] = digest(file); file.replace(destination / file.name)
        result = {'start_s':start,'end_s':end,'duration_s':end-start,'simulated':simulated,
                  'source_sha256':checksum,'narration':' '.join(c.text.replace('\n',' ') for c in cues),
                  'layout':'reflow' if reflow else request.fit,'presentation_sources':reflow['sources'] if reflow else [],
                  'scene_ranges':reflow['scene_ranges'] if reflow else [],
                  'thumbnail_source':reflow.get('thumbnail') if reflow else None,
                  'thumbnail_sources':reflow.get('thumbnails',[]) if reflow else [],
                  'thumbnail_spec':reflow.get('thumbnail_spec') if reflow else None}
        from .website import save_json
        save_json(receipt,{'fingerprint':identity,'files':files,'result':result})
    return result


def validate_short(orch, raw: dict) -> tuple[ShortsRequest,MediaAsset,MediaAsset]:
    request = ShortsRequest.model_validate(raw)
    manifest = orch.manifest(request.video_id)
    if manifest is None or manifest.version != request.manifest_version: raise ValueError('Bản nguồn đã thay đổi; chọn lại đoạn cắt')
    assets = list(orch.bus.replay('media-assets',request.video_id))
    final = next((MediaAsset.model_validate(e.payload) for e in reversed(assets) if e.payload['kind']=='final_video' and e.payload['manifest_version']==request.manifest_version),None)
    captions = next((MediaAsset.model_validate(e.payload) for e in reversed(assets) if e.payload['kind']=='captions' and e.payload['manifest_version']==request.manifest_version),None)
    if final is None or captions is None: raise ValueError('Cần bản cuối và phụ đề của đúng phiên bản nguồn')
    path = Path(final.path)
    if (not path.resolve().is_relative_to(orch.renderer.out_dir.resolve()) or not path.is_file()
            or final.checksum != request.source_checksum or digest(path)[:16] != final.checksum): raise ValueError('Checksum hoặc đường dẫn bản nguồn không hợp lệ')
    if final.duration_s is None or request.end_s > final.duration_s + .1: raise ValueError('Đoạn chọn vượt thời lượng bản nguồn')
    cap = Path(captions.path)
    if not cap.is_file() or (captions.checksum and digest(cap)[:16] != captions.checksum): raise ValueError('Phụ đề nguồn đã thay đổi hoặc bị thiếu')
    return request,final,captions


def reflow_context(orch, request: ShortsRequest, captions: MediaAsset) -> dict | None:
    if request.fit!='reflow': return None
    selected=[c for c in read_captions(Path(captions.path)) if c.end>request.start_s and c.start<request.end_s]
    if not selected: raise ValueError('Đoạn chọn không có lời đọc')
    start,end=selected[0].start,selected[-1].end
    manifest=orch.manifest(request.video_id); by_id={s.scene_id:s for s in manifest.scenes}
    records=[e.payload for e in orch.bus.replay('media-assets',request.video_id)]
    segments=[];sources=[];ranges=[];cues: list[Cue]=[];offset=None
    script_event=next(iter(reversed(list(orch.bus.replay('scripts',request.video_id)))),None)
    sections=script_event.payload['sections'] if script_event else []
    intervals=orch.renderer.cues(manifest)
    for index,interval in enumerate(intervals):
        if interval.end+getattr(orch.renderer.media.video,'tail_pad_s',0.)<=start or interval.start>=end: continue
        scene=by_id[interval.scene_id]
        for kind in ['scene_image','scene_audio']:
            path=Path(scene.asset_refs.get(kind,''))
            record=next((a for a in reversed(records) if a['kind']==kind and a['path']==str(path)),None)
            if record is None or not path.is_file() or digest(path)[:16]!=record['checksum']:
                raise ValueError('Tư liệu nguồn thiếu hoặc đã thay đổi; chọn giữ khung/cắt mép nếu nguồn là clip')
            sources.append({'scene_id':scene.scene_id,'kind':kind,'path':str(path),'sha256':digest(path),'provenance':record['provenance']})
        frame=orch.renderer.presentation_frame(manifest,scene,'9:16')
        image_source=next(s for s in reversed(sources) if s['kind']=='scene_image')
        sources.append({'scene_id':scene.scene_id,'kind':'scene_layout','path':str(frame),'sha256':digest(frame),
                        'provenance':{**image_source['provenance'],'generated_by':'studio:editorial-layout-v1',
                                      'prompt_ref':f"source_sha256={image_source['sha256']}; aspect=9:16"}})
        segments.append({'frame':str(frame),'frame_sha256':digest(frame),'audio':scene.asset_refs['scene_audio'],'duration_s':scene.duration_s})
        if offset is None: offset=interval.start
        try:
            timing=json.loads(Path(scene.asset_refs['scene_timing']).read_text(encoding='utf-8'))
            if timing['sha256']!=digest(Path(scene.asset_refs['scene_audio'])): raise ValueError('Audio changed')
            words=punctuated_words(validate_words(timing['words'],scene.duration_s),scene.narration)
            words=[w for w in words if w['start']+interval.start>=start-.001 and w['start']+interval.start<end]
            cues.extend(Cue('CLIP',c['start'],min(c['end'],end-start),c['text']) for c in
                timed_captions(words,interval.start-start,1.,max_chars=caption_width(orch.renderer.media.cfg.video,'9:16')))
        except (OSError,ValueError,KeyError,TypeError):
            fallback=[c for c in selected if c.start>=interval.start and c.start<interval.end]
            cues.extend(caption_pages([Cue('CLIP',c.start-start,c.end-start,c.text) for c in fallback],
                                       max_chars=caption_width(orch.renderer.media.cfg.video,'9:16')))
        boundary=intervals[index+1].start if index+1<len(intervals) else end
        left,right=max(start,interval.start),min(end,boundary)
        section=sections[scene.order] if scene.order<len(sections) else {}
        narration=' '.join(c.text.replace('\n',' ') for c in selected if interval.start<=c.start<boundary)
        ranges.append({'scene_id':scene.scene_id,'start_s':round(left-start,3),'end_s':round(right-start,3),
                       'heading':section.get('heading',request.title),'narration':narration,
                       'visual_prompt':scene.visual_prompt,'claim_ids':section.get('claim_ids',[])})
    thumbnails={a.get('variant_id'):a for a in records if a['kind']=='thumbnail' and a.get('variant_id')}
    thumb_sources=[]
    for variant,thumbnail in thumbnails.items():
        path=Path(thumbnail['path'])
        if not path.is_file() or digest(path)[:16]!=thumbnail['checksum']:raise ValueError('Thumbnail nguồn đã thay đổi')
        source={'kind':'thumbnail','path':str(path),'sha256':digest(path),'provenance':thumbnail['provenance'],'variant_id':variant}
        thumb_sources.append(source);sources.append(source)
    specs=list(orch.bus.replay('thumbnail-specs',request.video_id))
    return {'segments':segments,'offset':offset,'sources':sources,
            'captions':[{'start':c.start,'end':c.end,'text':c.text} for c in cues],
            'scene_ranges':ranges,'thumbnail':next((t for t in thumb_sources if t['variant_id']=='A'),None),
            'thumbnails':thumb_sources,'thumbnail_spec':specs[-1].payload if specs else None}


def create_short(orch, raw: dict, command_id: str) -> str:
    from .orchestrator import StepResult
    request,parent,source_captions = validate_short(orch,raw)
    vid = 'SHORT-'+fingerprint(request.request_id)[:24]
    directory = orch.renderer.out_dir / vid
    previous_operation = orch.renderer.operation_id; orch.renderer.operation_id = 'shorts:'+command_id
    try:
        recovered = orch.renderer._resume_output()
        if recovered:
            manifest = SceneManifest.model_validate(next(e.payload for e in recovered if e.topic=='scene-manifests'))
            final = MediaAsset.model_validate(next(e.payload for e in recovered if e.topic=='media-assets' and e.payload['kind']=='final_video'))
        else:
            context=reflow_context(orch,request,source_captions)
            result = render_short(Path(parent.path),Path(source_captions.path),directory,request,orch.renderer.media.cfg,
                                  simulated=parent.provider=='fake',reflow=context)
            brief = orch.desk.briefs[request.video_id].model_copy(deep=True)
            brief.video_id = vid; brief.working_title = request.title; brief.format = 'short'; brief.target_minutes = result['duration_s']/60
            brief.retry = 0; brief.hint = None
            brief.boundaries.append(f"Trích từ {request.video_id}, giây {result['start_s']:.3f}–{result['end_s']:.3f}; không thêm lời đọc hoặc dữ kiện mới.")
            parent_script = orch.latest('scripts',request.video_id)
            hook=' '.join(result['narration'].split()[:15])
            if parent_script and parent_script.payload['hook'] in result['narration'] and len(parent_script.payload['hook'].split())<=15:
                hook=parent_script.payload['hook']
            cta=parent_script.payload.get('cta','') if parent_script else ''
            script = Script(video_id=vid,working_title=request.title,hook=hook,cta=cta if cta in result['narration'] else '',
                            sections=[ScriptSection(heading=r['heading'],narration=r['narration'],claim_ids=r['claim_ids'])
                                      for r in result['scene_ranges']] or [ScriptSection(heading='Đoạn trích',narration=result['narration'])],
                            claims=Script.model_validate(parent_script.payload).claims if parent_script else [],
                            word_count=len(result['narration'].split()),estimated_minutes=result['duration_s']/60)
            parent_meta = orch.latest('metadata-packages',request.video_id)
            metadata = MetadataPackage(video_id=vid,title=request.title,description=result['narration'],
                                       tags=parent_meta.payload['tags'] if parent_meta else [],chapters=[])
            seen = {e.event_id for e in orch.bus.replay()}
            for topic,actor,payload in [('video-briefs','console:shorts',brief.model_dump()),('scripts','console:shorts',script.model_dump()),('metadata-packages','chapters',metadata.model_dump())]:
                id = str(uuid5(NAMESPACE_URL,command_id+':'+topic))
                if id not in seen: orch.bus.publish(Envelope.model_validate(dict(event_id=id,topic=topic,key=vid,actor=actor,payload=payload)))
            provenance = parent.provenance.model_copy(deep=True)
            source_manifest = orch.manifest(request.video_id)
            used_paths = {parent.path, source_captions.path}
            used_paths.update(path for scene in source_manifest.scenes for path in scene.asset_refs.values())
            used_paths.update(source['path'] for source in result['presentation_sources'])
            # Old audio takes keep their audit records; they are not inputs to this export.
            latest_assets = {e.payload['path']: MediaAsset.model_validate(e.payload)
                             for e in orch.bus.replay('media-assets',request.video_id)}
            parent_assets = [a for a in latest_assets.values() if a.path in used_paths
                             or (a.kind=='music' and a.manifest_version==request.manifest_version)]
            provenance=component_provenance(provenance,[a.provenance for a in parent_assets])
            provider = 'fake' if result['simulated'] else 'ffmpeg'
            final_path = directory / 'final_v1.mp4'; caption_path = directory / 'captions_v1.srt'
            output_sha = digest(final_path); caption_sha = digest(Path(source_captions.path))
            source_ref = (f"{request.video_id}; manifest_version={request.manifest_version}; source_sha256={result['source_sha256']}; "
                          f"start_s={result['start_s']}; end_s={result['end_s']}; fit={request.fit}; focus_x={request.focus_x}")
            manifest = SceneManifest(video_id=vid,scenes=[Scene(scene_id='S1',order=0,narration=result['narration'],visual_prompt='Đoạn trích từ bản nguồn đã lưu',
                     duration_s=result['duration_s'],locked=True,asset_refs={'scene_video':str(final_path),'scene_audio':str(final_path)})],
                     voice=orch.manifest(request.video_id).voice,aspect='9:16',cinema=orch.manifest(request.video_id).cinema)
            if result['scene_ranges']:
                manifest.scenes=[Scene(scene_id=r['scene_id'],order=i,narration=r['narration'],visual_prompt=r['visual_prompt'],
                    duration_s=round(r['end_s']-r['start_s'],3),clip_start_s=r['start_s'],locked=True,
                    asset_refs={'scene_video':str(final_path),'scene_audio':str(final_path)}) for i,r in enumerate(result['scene_ranges'])]
            if manifest.voice.get('resolved_voice_id'):
                manifest.voice['requested_voice_id']=manifest.voice.get('voice_id')
                manifest.voice['voice_id']=manifest.voice['resolved_voice_id']
            assets = []
            scene_assets=[(kind,final_path,scene.scene_id) for scene in manifest.scenes for kind in ['scene_video','scene_audio']]
            thumbnails=[('thumbnail',directory/(t['variant_id']+'.jpg'),None) for t in result['thumbnail_sources']]
            for kind,path,scene in [*scene_assets,('captions',caption_path,None),*(thumbnails or [('thumbnail',directory/'A.jpg',None)]),('final_video',final_path,None)]:
                asset_provenance = provenance.model_copy(deep=True)
                asset_provenance.generated_by = provider+':shorts-excerpt'
                asset_provenance.prompt_ref = source_ref
                if kind == 'captions':
                    asset_provenance = source_captions.provenance.model_copy(deep=True)
                    asset_provenance.generated_by = 'studio:srt-clip-rebase'
                    asset_provenance.prompt_ref = source_ref+f'; source_caption_sha256={caption_sha}; rebase_s={result["start_s"]}'
                elif kind == 'thumbnail':
                    asset_provenance.generated_by = 'pillow:synthetic-poster' if result['simulated'] else 'ffmpeg+pillow:poster'
                    asset_provenance.prompt_ref = (f'{vid}; output_sha256={output_sha}; '+
                        ('synthetic solid RGB fixture 1280x720' if result['simulated'] else 'extract frame at 0.1s; Pillow ImageOps.pad 1280x720; JPEG quality 85'))
                    reused=next((t for t in result['thumbnail_sources'] if t['variant_id']==path.stem),None)
                    if reused:
                        from .events import Provenance
                        asset_provenance=Provenance.model_validate(reused['provenance'])
                        asset_provenance.generated_by='studio:thumbnail-reuse'
                        asset_provenance.prompt_ref=f"source_thumbnail_sha256={reused['sha256']}; attached_video_sha256={output_sha}"
                assets.append(MediaAsset.model_validate(dict(video_id=vid,kind=kind,path=str(path),scene_id=scene,provider=provider,checksum=digest(path)[:16],
                                         duration_s=result['duration_s'] if kind in {'scene_video','scene_audio','final_video'} else None,
                                         variant_id=path.stem if kind=='thumbnail' else None,provenance=asset_provenance)))
            final = assets[-1]
            orch.renderer._publish(assets,'render.short',manifest)
            orch._audit('clip.derived',{'video_id':vid,'source_video_id':request.video_id,'source_sha256':result['source_sha256'],
                                      'source_manifest_version':request.manifest_version,'source_caption_sha256':caption_sha,'output_sha256':output_sha,
                                      'layout':result['layout'],'presentation_sources':result['presentation_sources'],
                                      'scene_ranges':result['scene_ranges'],'thumbnail_source':result['thumbnail_source'],
                                      'thumbnail_spec':result['thumbnail_spec'],
                                      'start_s':result['start_s'],'end_s':result['end_s'],'fit':request.fit,'simulated':result['simulated']},video_id=vid)
        orch._after_final(manifest,None,final,StepResult(command_id,'media-assets',vid))
    finally: orch.renderer.operation_id = previous_operation
    return vid
