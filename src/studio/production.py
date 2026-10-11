"""Licensed local media and final finishing; no network or model calls."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from .events import Provenance, SceneManifest
from .media import FakeVideo, FFmpegAssembler, MediaConfig, MediaError, VideoAssembler


class LibraryAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["image", "video", "music"]
    path: str
    license: Literal["owned", "licensed", "cc-by", "cc0"]
    attribution: str = ""
    source_url: str | None = None

    def provenance(self) -> Provenance:
        return Provenance(generated_by="human-library", license=self.license,
                          source_url=self.source_url, attribution=self.attribution)


def contained_file(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or not path.stat().st_size:
        raise MediaError(f"library file must exist inside upload_dir: {value}")
    return path


def selections(cfg: MediaConfig, upload_dir: Path, manifest: SceneManifest) -> tuple[dict[str, LibraryAsset], LibraryAsset | None]:
    """Validate the entire selection before paying for the first TTS/image call."""
    settings = cfg.production
    mapping = settings.get("scenes", {}).get(manifest.video_id, {})
    music_id = settings.get("music", {}).get(manifest.video_id)
    if not mapping and not music_id:
        return {}, None
    try:
        catalog_path = contained_file(upload_dir, str(settings.get("library", "library.yaml")))
        catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))["assets"]
        unknown = set(mapping) - {s.scene_id for s in manifest.scenes}
        if unknown:
            raise MediaError(f"library selection references unknown scenes: {sorted(unknown)}")

        def pick(name: str, kinds: set[str]) -> LibraryAsset:
            asset = LibraryAsset.model_validate(catalog[name])
            if asset.kind not in kinds:
                raise MediaError(f"library asset {name}: wrong kind {asset.kind}")
            if asset.license == "cc-by" and (not asset.attribution.strip() or not asset.source_url):
                raise MediaError(f"library asset {name}: cc-by requires attribution and source_url")
            asset.path = str(contained_file(upload_dir, asset.path))
            return asset

        visuals = {sid: pick(name, {"image", "video"}) for sid, name in mapping.items()}
        return visuals, pick(music_id, {"music"}) if music_id else None
    except (OSError, KeyError, TypeError, ValidationError, yaml.YAMLError) as exc:
        raise MediaError("invalid production library or asset selection") from exc


def copy_asset(asset: LibraryAsset, target: Path) -> Path:
    out = target.with_suffix(Path(asset.path).suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(asset.path, out)
    return out


def finish(video: VideoAssembler, src: Path, captions: Path | None, music: Path | None,
           credits: str, volume: float = 0.12) -> None:
    """Finish atomically. Music ducks under speech; failure keeps the unmodified input.

    Caption paths are fixed filenames in a temporary working directory, so Unicode,
    Windows drive letters and filter syntax in user filenames never reach libass.
    """
    if not captions and not music:
        return
    if not 0 <= volume <= 1:
        raise MediaError("music_volume must be between 0 and 1")
    if isinstance(video, FakeVideo):
        data = json.loads(src.read_text(encoding="utf-8"))
        data["finishing"] = {"captions": str(captions) if captions else None,
                             "music": str(music) if music else None, "credits": credits}
        src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return
    if not isinstance(video, FFmpegAssembler):
        raise MediaError("this video assembler does not support captions/music finishing")
    with tempfile.TemporaryDirectory(prefix=".finish-", dir=src.parent) as temp:
        root = Path(temp)
        args = ["-threads", str(video.threads), "-i", str(src.resolve())]
        if music:
            args += ["-stream_loop", "-1", "-threads", str(video.threads), "-i", str(music.resolve())]
            graph = (f"[1:a]volume={volume},aresample=48000[bed];[0:a]asplit=2[voice][side];"
                     "[bed][side]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=300[duck];"
                     "[voice][duck]amix=inputs=2:duration=first:normalize=0,"
                     "loudnorm=I=-14:TP=-1:LRA=11[mix]")
            args += ["-filter_complex", graph, "-map", "0:v:0", "-map", "[mix]"]
        else:
            args += ["-map", "0:v:0", "-map", "0:a:0"]
        if captions:
            shutil.copyfile(captions, root / "captions.srt")
            args += ["-vf", "subtitles=captions.srt:force_style='FontName=DejaVu Sans,FontSize=20,"
                     "Outline=2,Shadow=0,MarginV=24,Alignment=2'", "-c:v", "libx264", "-crf", "20",
                     "-preset", "medium"]
        else:
            args += ["-c:v", "copy"]
        target = root / "finished.mp4"
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-threads", str(video.threads),
                 "-movflags", "+faststart", str(target.resolve())]
        video._run(args, cwd=root)
        target.replace(src)
