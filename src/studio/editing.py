"""Human editing requests and an atomic local media catalog; model programs never run here."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
from io import BufferedIOBase
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

import yaml
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .events import ID_PATTERN, CutList, Envelope, Repair, Script, ScriptSection
from .media import MediaConfig, MediaError
from .production import LibraryAsset, contained_file
from .render_cache import digest
from .storage import ConflictError


class MediaLibrary:
    def __init__(self, cfg: MediaConfig):
        self.root = (cfg.upload_dir or cfg.output_dir / 'uploads').resolve()
        self.catalog = self.root / str(cfg.production.get('library', 'library.yaml'))
        if not self.catalog.resolve().is_relative_to(self.root): raise ValueError('Catalog must be inside upload directory')
        self.lock = threading.RLock()

    def _read(self) -> dict:
        if not self.catalog.exists(): return {'revision':0, 'assets':{}}
        data = yaml.safe_load(self.catalog.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('assets'), dict): raise ValueError('Invalid media catalog')
        data.setdefault('revision',0)
        return data

    def snapshot(self) -> dict:
        data = self._read()
        return {'revision':data['revision'], 'upload_dir':str(self.root),
                'assets':[{'id':id, **asset, 'url':'/media/library-'+id} for id, asset in data['assets'].items()]}

    def path(self, id: str) -> Path | None:
        try:
            asset = LibraryAsset.model_validate(self._read()['assets'][id])
            path = contained_file(self.root, asset.path)
            if asset.checksum and digest(path) != asset.checksum: return None
            return path
        except (KeyError, ValueError, OSError, MediaError): return None

    def save(self, revision: int, raw: dict) -> dict:
        id = raw.get('id', '')
        if not isinstance(id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',id): raise ValueError('Invalid library ID')
        asset = LibraryAsset.model_validate({k:v for k,v in raw.items() if k not in {'id','url'}})
        try: path = contained_file(self.root, asset.path)
        except MediaError as exc: raise ValueError(str(exc)) from exc
        if asset.license == 'cc-by' and (not asset.source_url or not asset.attribution): raise ValueError('CC-BY cần nguồn và attribution')
        asset.path = path.relative_to(self.root).as_posix(); asset.checksum = digest(path)
        with self.lock:
            data = self._read()
            if type(revision) is not int or revision != data['revision']: raise ConflictError('Thư viện đã thay đổi; tải lại trước khi lưu')
            data['assets'][id] = asset.model_dump(); data['revision'] += 1
            self.catalog.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.catalog-',dir=self.catalog.parent) as temp:
                stage = Path(temp) / 'catalog.yaml'; stage.write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')
                stage.replace(self.catalog)
        return self.snapshot()

    def upload(self, filename: str, stream: BinaryIO | BufferedIOBase, length: int) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in {'.png','.jpg','.jpeg','.webp','.mp4','.webm','.mov','.wav','.mp3','.flac'} or not 0 < length <= 256*1024*1024:
            raise ValueError('Chọn ảnh, video hoặc audio tối đa 256 MiB')
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock, tempfile.TemporaryDirectory(prefix='.upload-',dir=self.root) as temp:
            path = Path(temp) / ('asset'+suffix); remaining = length
            with path.open('wb') as out:
                while remaining:
                    block = stream.read(min(65536,remaining))
                    if not block: raise ValueError('Upload chưa đủ số byte')
                    out.write(block); remaining -= len(block)
            kind = 'image'
            try:
                if suffix in {'.png','.jpg','.jpeg','.webp'}:
                    with Image.open(path) as image: image.verify()
                else:
                    result = subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(path)],
                                            capture_output=True,timeout=30,check=True)
                    kinds = {s['codec_type'] for s in json.loads(result.stdout)['streams']}
                    if not kinds & {'audio','video'}: raise ValueError('Media has no streams')
                    kind = 'video' if 'video' in kinds else 'music'
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc: raise ValueError('File không phải media hợp lệ hoặc thiếu ffprobe') from exc
            id = uuid4().hex; target = self.root / (id+suffix)
            shutil.copyfile(path,target)
            try: return self.save(self._read()['revision'], {'id':id,'kind':kind,'path':target.name,'license':'unknown'})
            except Exception:
                target.unlink(missing_ok=True); raise


class SceneEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    video_id: str = Field(pattern=ID_PATTERN)
    scene_id: str = Field(pattern=ID_PATTERN)
    manifest_version: int = Field(ge=1,strict=True)
    action: Literal['regenerate_audio','regenerate_image','regenerate_both','replace_asset','lock','unlock']
    narration: str | None = Field(default=None,min_length=1,max_length=12000)
    visual_prompt: str | None = Field(default=None,min_length=1,max_length=12000)
    asset_id: str | None = Field(default=None,max_length=128)
    request_id: str = Field(min_length=1,max_length=128,pattern=r'^[a-zA-Z0-9_-]+$')


def validate_edit(orch, raw: dict) -> SceneEdit:
    request = SceneEdit.model_validate(raw)
    manifest = orch.manifest(request.video_id)
    if manifest is None or manifest.version != request.manifest_version: raise ValueError('Manifest đã thay đổi; tải lại cảnh trước khi sửa')
    if orch.desk.state.get(request.video_id) != 'in_review': raise ValueError('Chỉ sửa cảnh khi bản dựng đang chờ review hoặc duyệt')
    if orch._prior_upload(request.video_id): raise ValueError('Video đã upload; đối chiếu bản trên nền tảng trước khi sửa')
    if orch.desk.repair_rounds[request.video_id] >= 3: raise ValueError('Đã dùng hết 3 vòng sửa cảnh')
    scene = next((s for s in manifest.scenes if s.scene_id == request.scene_id),None)
    if scene is None: raise ValueError('Không tìm thấy cảnh')
    if scene.clip_start_s is not None:raise ValueError('Sửa cảnh ở video nguồn rồi tạo lại Shorts để giữ đúng lời đọc và mốc cắt')
    if scene.locked and request.action not in {'lock','unlock'}: raise ValueError('Mở khóa cảnh trước khi sửa')
    if request.narration is not None and request.action not in {'regenerate_audio','regenerate_both'}: raise ValueError('Đổi lời đọc cần tạo lại audio')
    if request.visual_prompt is not None and request.action not in {'regenerate_image','regenerate_both'}: raise ValueError('Đổi hình cần tạo lại hình/clip')
    return request


def apply_edit(orch, library: MediaLibrary, raw: dict, command_id: str) -> None:
    event_id = str(uuid5(NAMESPACE_URL,'studio-edit:'+command_id))
    if any(e.event_id == event_id for e in orch.bus.replay('cut-lists')): return
    request = validate_edit(orch,raw); manifest = orch.manifest(request.video_id)
    path = library.path(request.asset_id or '') if request.action == 'replace_asset' else None
    if request.action == 'replace_asset' and path is None: raise ValueError('Chọn asset hợp lệ trong thư viện')
    if request.action == 'replace_asset' and not any(a['id']==request.asset_id and a['kind'] in {'image','video'} for a in library.snapshot()['assets']):
        raise ValueError('Cảnh cần ảnh hoặc video, không dùng nhạc làm hình')
    orch.gate.invalidate('PUB-'+request.video_id)
    script_id = str(uuid5(NAMESPACE_URL,'studio-edit-script:'+command_id))
    if not any(e.event_id == script_id for e in orch.bus.replay('scripts',request.video_id)):
        latest = orch.latest('scripts',request.video_id)
        script = Script.model_validate(latest.payload)
        script.version += 1
        sections = []
        for scene in sorted(manifest.scenes,key=lambda s:s.order):
            section = (script.sections[scene.order].model_copy(deep=True) if scene.order < len(script.sections)
                       else ScriptSection(heading=scene.scene_id,narration=scene.narration))
            section.narration = request.narration if scene.scene_id == request.scene_id and request.narration is not None else scene.narration
            sections.append(section)
        script.sections = sections
        script.word_count = sum(len(s.narration.split()) for s in script.sections)
        orch.bus.publish(Envelope(event_id=script_id,topic='scripts',key=request.video_id,actor='console:editor',payload=script.model_dump()))
    repair = Repair(scene_id=request.scene_id,action=request.action,reason='Người dùng sửa từ phòng dựng',
                    new_narration=request.narration,new_visual_prompt=request.visual_prompt,replacement_path=str(path) if path else None)
    cut = CutList(video_id=request.video_id,manifest_version=request.manifest_version,decision='repair',repairs=[repair])
    orch.bus.publish(Envelope(event_id=event_id,topic='cut-lists',key=request.video_id,actor='console:editor',payload=cut.model_dump()))
