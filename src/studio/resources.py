"""Observed media work, operator pricing, disk inventory and preview-checked derived-cache cleanup."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from .bus import InMemoryBus
from .events import AuditLog, Envelope
from .media import MediaConfig, MediaError

T = TypeVar('T')
PRICE_KEYS = {'tts':'tts_per_1000_chars', 'image':'image_per_request', 'video':'video_per_second'}


class MediaAccounting:
    def __init__(self, bus: InMemoryBus, cfg: MediaConfig):
        self.bus, self.cfg = bus,cfg

    def summary(self) -> dict:
        projects: dict[str,dict] = {}; cache_hits = 0; text_tokens = 0
        for event in self.bus.replay('audit-log'):
            text_tokens += int(event.payload.get('tokens') or 0)
            action = event.payload.get('action')
            if action not in {'media.usage','render.cache'}: continue
            if action == 'media.usage' and event.actor != 'media-accounting': continue
            if action == 'render.cache' and event.actor != 'renderer': continue
            try: data = json.loads(event.payload.get('evidence') or '{}')
            except (ValueError,TypeError): continue
            if not isinstance(data,dict): continue
            if action == 'render.cache':
                hits = data.get('hits',{})
                if isinstance(hits,dict): cache_hits += sum(v is True for v in hits.values())
                continue
            vid = str(event.payload.get('video_id') or 'unassigned')
            item = projects.setdefault(vid,{'estimated_usd':0.,'generations':0,'failed':0,'unknown_cost':0,'units':{}})
            if data.get('status') == 'succeeded': item['generations'] += 1
            else: item['failed'] += 1
            cost = data.get('estimated_usd')
            if isinstance(cost,(float,int)) and math.isfinite(cost) and cost >= 0: item['estimated_usd'] += cost
            else: item['unknown_cost'] += 1
            kind = str(data.get('kind','unknown'))
            try: units = float(data.get('units') or 0.)
            except (ValueError,TypeError): units = 0.
            if not math.isfinite(units) or units < 0: units = 0.
            item['units'][kind] = item['units'].get(kind,0.) + units
        for item in projects.values(): item['estimated_usd'] = round(item['estimated_usd'],8)
        return {'projects':projects, 'text_tokens':text_tokens, 'cache_hits':cache_hits,
                'estimated_usd':round(sum(p['estimated_usd'] for p in projects.values()),8),
                'unknown_cost':sum(p['unknown_cost'] for p in projects.values()),'currency':'USD','basis':'operator_estimate'}

    def generate(self, video_id: str, kind: str, units: float, create: Callable[[],T]) -> T:
        price = self.cfg.production.get('pricing',{}).get(PRICE_KEYS[kind])
        if not math.isfinite(units) or units < 0: raise MediaError('Invalid media accounting units')
        if price is not None and (isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price < 0):
            raise MediaError('Media price must be a finite nonnegative number')
        estimate = round(price*units,8) if price is not None else None
        budget = self.cfg.production.get('media_budget_usd')
        if budget is not None:
            if not isinstance(budget,(int,float)) or not math.isfinite(budget) or budget < 0: raise MediaError('Invalid media budget')
            spent = self.summary()['projects'].get(video_id,{}).get('estimated_usd',0.)
            if estimate is None: raise MediaError('Cấu hình giá media trước khi dùng hạn mức chi phí ước tính')
            if spent + estimate > budget + 1e-9: raise MediaError('Đã hết hạn mức chi phí media ước tính của dự án')
        try: result = create()
        except Exception:
            self.record(video_id,kind,units,None,'failed'); raise
        self.record(video_id,kind,units,estimate,'succeeded')
        return result

    def record(self, video_id: str, kind: str, units: float, estimate: float | None, status: str) -> None:
        data = {'kind':kind,'units':units,'estimated_usd':estimate,'status':status}
        audit = AuditLog(actor='media-accounting',action='media.usage',video_id=video_id,evidence=json.dumps(data))
        self.bus.publish(Envelope(topic='audit-log',key=video_id,actor='media-accounting',payload=audit.model_dump()))

    def record_text(self, video_id: str, tokens: int) -> None:
        if type(tokens) is not int or tokens < 0: return
        audit = AuditLog(actor='website-text',action='usage.text',video_id=video_id,tokens=tokens,evidence='Reported by text backend')
        self.bus.publish(Envelope(topic='audit-log',key=video_id,actor='website-text',payload=audit.model_dump()))


def inventory(cfg: MediaConfig, website_jobs: list[dict] | None = None) -> dict:
    roots = [cfg.output_dir.resolve(),(cfg.upload_dir or cfg.output_dir / 'uploads').resolve()]
    roots.extend(Path(job['directory']).resolve() for job in website_jobs or [])
    unique: list[Path] = []
    for root in sorted(set(roots),key=lambda p:len(str(p))):
        if not any(root.is_relative_to(existing) for existing in unique): unique.append(root)
    sizes: dict[str,int] = {}; projects: dict[str,int] = {}; count = 0
    website_roots = {Path(job['directory']).resolve():'website:'+job['id'] for job in website_jobs or []}
    types = {'.mp4':'video','.mkv':'video','.webm':'video','.mov':'video','.png':'image','.jpg':'image','.jpeg':'image',
             '.webp':'image','.wav':'audio','.mp3':'audio','.flac':'audio','.srt':'subtitles','.ass':'subtitles','.json':'metadata','.yaml':'metadata'}
    for root in unique:
        for file in root.rglob('*'):
            if not file.is_file() or file.is_symlink() or file.resolve() != file.absolute(): continue
            kind = types.get(file.suffix.lower())
            if kind is None: continue
            try: size = file.stat().st_size
            except OSError: continue
            sizes[kind] = sizes.get(kind,0)+size; count += 1
            website = next((name for folder,name in website_roots.items() if file.is_relative_to(folder)),None)
            project = website or ('uploads' if file.is_relative_to(roots[1]) else file.relative_to(root).parts[0])
            projects[project] = projects.get(project,0)+size
    disk = cfg.output_dir.resolve()
    while not disk.exists(): disk = disk.parent
    return {'bytes':sum(sizes.values()),'by_kind':sizes,'by_project':projects,'files':count,'free_bytes':shutil.disk_usage(disk).free}


def preview_segments(cfg: MediaConfig, max_bytes: int) -> dict:
    if type(max_bytes) is not int or not 0 <= max_bytes <= 10*1024**4: raise ValueError('Invalid segment budget')
    entries: list[dict[str,Any]] = []
    for directory in cfg.output_dir.resolve().rglob('.segments'):
        if directory.is_symlink() or not directory.is_dir(): continue
        for file in directory.glob('*.mkv'):
            meta = file.with_suffix('.json')
            if not re.fullmatch(r'[0-9a-f]{64}',file.stem) or file.is_symlink() or meta.is_symlink(): continue
            try:
                data = json.loads(meta.read_text(encoding='utf-8'))
                if data.get('provider') != 'ffmpeg' or data.get('model') != 'bounded-scene': continue
                stat = file.stat(); meta_stat = meta.stat()
                if file.resolve() != file.absolute(): continue
            except (OSError,ValueError,AttributeError): continue
            entries.append({'path':str(file),'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns,'meta_ns':meta_stat.st_mtime_ns})
    entries.sort(key=lambda item:(item['mtime_ns'],item['path']))
    total = sum(row['bytes'] for row in entries); selected = []
    for row in entries:
        if total <= max_bytes: break
        selected.append(row); total -= row['bytes']
    fingerprint = hashlib.sha256(json.dumps([max_bytes,entries],sort_keys=True).encode()).hexdigest()
    return {'files':selected,'bytes':sum(row['bytes'] for row in selected),'fingerprint':fingerprint,'max_bytes':max_bytes}


def cleanup_segments(cfg: MediaConfig, max_bytes: int, fingerprint: str) -> dict:
    plan = preview_segments(cfg,max_bytes)
    if fingerprint != plan['fingerprint']: raise ValueError('Cache đã thay đổi; xem lại danh sách trước khi dọn')
    for row in plan['files']:
        file = Path(row['path']); file.unlink(); file.with_suffix('.json').unlink()
    return {'bytes':plan['bytes'],'files':len(plan['files'])}
