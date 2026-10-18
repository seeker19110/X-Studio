"""Bounded contact sheets and an optional operator-configured read-only visual/audio reviewer."""
from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from .events import Finding, MediaAsset, SceneManifest
from .media import MediaConfig, find_font
from .render_cache import digest, fingerprint
from .sandbox import RunSpec, SandboxError, clean_env, sandbox_from_config


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    verdict: Literal['pass','warn','block']
    findings: list[Finding] = Field(default_factory=list,max_length=100)


def inspect_project(manifest: SceneManifest, final: MediaAsset, cfg: MediaConfig) -> dict:
    if final.provider == 'fake': return {'available':False,'simulated':True,'manifest_version':manifest.version,'findings':[]}
    findings: list[dict] = []
    references = []
    for scene in manifest.scenes:
        for kind,path in scene.asset_refs.items():
            file = Path(path)
            references.append({'scene_id':scene.scene_id,'kind':kind,'path':path,'sha256':digest(file) if file.is_file() else 'missing'})
    final_checksum = digest(Path(final.path)) if Path(final.path).is_file() else 'missing'
    identity = fingerprint([manifest.model_dump(),final_checksum,references,cfg.production.get('review_command'),
                            cfg.production.get('review_required'),cfg.production.get('review_timeout_s')])
    directory = cfg.output_dir / manifest.video_id; directory.mkdir(parents=True,exist_ok=True)
    report = directory / f'review_v{manifest.version}.json'; contact = directory / f'review_v{manifest.version}.jpg'
    try:
        cached = json.loads(report.read_text(encoding='utf-8'))
        if cached['fingerprint'] == identity and cached['contact_sha256'] == digest(contact): return cached
    except (OSError,ValueError,KeyError,TypeError): pass
    scenes = sorted(manifest.scenes,key=lambda s:s.order)
    stride = max(1,len(scenes)//24); selected = scenes[::stride][:24]
    font_path = find_font(cfg.video)
    font = ImageFont.truetype(str(font_path),18) if font_path else ImageFont.load_default()
    sheet = Image.new('RGB',(960,max(1,(len(selected)+2)//3)*220),'#101a2c')
    with tempfile.TemporaryDirectory(prefix='.review-',dir=directory) as temp:
        root = Path(temp)
        for index,scene in enumerate(selected):
            tile = Image.new('RGB',(320,180),'#253247')
            image = scene.asset_refs.get('scene_layout') or scene.asset_refs.get('scene_image'); video = scene.asset_refs.get('scene_video')
            try:
                capture_path = Path(image) if image else root / f'{index}.png'
                if not image and video:
                    position=scene.clip_start_s+scene.duration_s/2 if scene.clip_start_s is not None else .1
                    subprocess.run([str(cfg.video.get('binary','ffmpeg')),'-v','error','-threads','1','-ss',str(position),'-i',video,
                                    '-frames:v','1','-threads','1',str(capture_path)],capture_output=True,timeout=30,check=True)
                with Image.open(capture_path) as source: tile = ImageOps.pad(source.convert('RGB'),(320,180),color='#253247')
            except (OSError,ValueError,subprocess.SubprocessError):
                findings.append(Finding(level='warn',text='Không tạo được frame xem trước của cảnh',location=scene.scene_id).model_dump())
            x,y = index%3*320,index//3*220; sheet.paste(tile,(x,y))
            ImageDraw.Draw(sheet).text((x+8,y+188),scene.scene_id,font=font,fill='white')
        candidate = root / 'contact.jpg'; sheet.save(candidate,quality=88); candidate.replace(contact)
    result = {'available':True,'simulated':False,'manifest_version':manifest.version,'fingerprint':identity,
              'final_sha256':final_checksum,'contact_sheet':str(contact),'contact_sha256':digest(contact),
              'references':references,'sampled_scenes':len(selected),'total_scenes':len(scenes),
              'review_status':'human_required','findings':findings}
    command = cfg.production.get('review_command')
    if command:
        try:
            argv = shlex.split(command) if isinstance(command,str) else command
            if not isinstance(argv,list) or not argv or not all(isinstance(a,str) and a for a in argv): raise ValueError('Invalid review command')
            timeout = float(cfg.production.get('review_timeout_s',60))
            if not 1 <= timeout <= 300: raise ValueError('Invalid review timeout')
            run = sandbox_from_config(cfg).run(RunSpec(argv=argv,cwd=Path.cwd(),env=clean_env(),read_only=True,
                  timeout=timeout,max_output=100000,stdin=json.dumps({**result,'manifest':manifest.model_dump()},ensure_ascii=False)))
            if run.timed_out or run.exit_code != 0: raise ValueError('Reviewer unavailable')
            reviewed = ReviewOutput.model_validate_json(run.stdout)
            result['review_status'] = reviewed.verdict
            findings.extend(f.model_dump() for f in reviewed.findings)
            if reviewed.verdict == 'block' and not any(f['level']=='block' for f in findings):
                findings.append(Finding(level='block',text='Review hình/tiếng không đạt',location='inspection').model_dump())
        except (OSError,ValueError,SandboxError,subprocess.SubprocessError,TypeError):
            result['review_status'] = 'unavailable'
            findings.append(Finding(level='block',text='Không chạy được review hình/tiếng hoặc đầu ra không hợp lệ',location='inspection').model_dump())
    elif cfg.production.get('review_required'):
        findings.append(Finding(level='block',text='Cần cấu hình review hình/tiếng trước khi tự xuất bản',location='inspection').model_dump())
    from .website import save_json
    save_json(report,result)
    return result
