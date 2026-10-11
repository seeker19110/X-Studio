"""Low-memory renderer: encode one scene at a time, concatenate without re-encoding video.

Intermediate audio is losslessly compressed FLAC, avoiding cumulative AAC priming gaps across hundreds of
scenes. Scene durations are rounded up to whole frames and shared with captions.
"""
from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

from .media import FFmpegAssembler, MediaError, MediaResult, motion_for, probe_duration
from .render_cache import RenderCache, digest


def frame_span(duration: float, pad: float, fps: int) -> float:
    if fps < 1 or not math.isfinite(duration) or duration <= 0 or not math.isfinite(pad) or pad < 0:
        raise MediaError("bounded render requires finite positive duration, nonnegative padding and positive fps")
    return max(1, math.ceil((duration + pad) * fps - 1e-8)) / fps


class BoundedAssembler(FFmpegAssembler):
    bounded = True

    def __init__(self, *, segment_cache_max_bytes: int = 0,
                 min_free_disk_bytes: int = 256 * 1024 * 1024, **kwargs):
        if segment_cache_max_bytes < 0 or min_free_disk_bytes < 0:
            raise MediaError("render disk limits must be nonnegative bytes")
        self.segment_cache_max_bytes = segment_cache_max_bytes
        self.min_free_disk_bytes = min_free_disk_bytes
        super().__init__(**kwargs)
        # Each scene fades within its own duration; no overlapping audio/timestamps.
        self.transition_s = 0.0

    def _check_disk(self, root: Path, required: int = 0) -> None:
        free = shutil.disk_usage(root).free
        if free < self.min_free_disk_bytes + required:
            raise MediaError(f"insufficient free disk: {free} bytes available; need {required} estimated output bytes "
                             f"plus render.min_free_disk_bytes reserve {self.min_free_disk_bytes}; "
                             "free disk space or move output_dir")

    def assemble(self, segments: list[tuple[Path, Path, float]], out: Path, fps: int, resolution: str) -> MediaResult:
        if not segments or fps < 1:
            raise MediaError("bounded render requires scenes and positive fps")
        out.parent.mkdir(parents=True, exist_ok=True)
        w, h = (int(x) for x in resolution.lower().split("x"))
        cache = RenderCache(out.parent / ".segments")
        paths: list[Path] = []
        total = 0.0
        prepared = []
        for index, (visual, audio, fallback) in enumerate(segments):
            duration = probe_duration(audio) or fallback
            span = frame_span(duration, self.tail_pad_s, fps)
            moving = visual.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
            motion = "static" if moving else motion_for(index, self.motion)
            key = {"format": 2, "visual": digest(visual), "audio": digest(audio), "span": span,
                   "fps": fps, "resolution": resolution, "motion": motion, "fit": self.fit,
                   "lufs": self.loudness_lufs, "binary": self.binary}

            prepared.append((visual, audio, span, motion, moving, key))
        # Include not-yet-visited cached scenes before deleting any inactive checkpoint.
        protected = {cache.blob_path(item[-1], ".mkv") for item in prepared}
        cache.prune_segments(self.segment_cache_max_bytes, protected)
        for index, (visual, audio, span, motion, moving, key) in enumerate(prepared):
            def encode(target: Path, visual=visual, audio=audio, span=span, motion=motion, moving=moving) -> MediaResult:
                self._check_disk(out.parent)
                opts = ["-stream_loop", "-1"] if moving else ["-loop", "1", "-framerate", str(fps)]
                vf = self._motion_filter(motion, w, h, span, fps)
                fade = min(0.12, span / 4)
                vf += f",fps={fps},format=yuv420p,setsar=1,fade=t=in:d={fade},fade=t=out:st={span-fade}:d={fade}"
                af = f"aresample=48000,apad,atrim=0:{span},asetpts=N/SR/TB"
                if self.loudness_lufs is not None:
                    af += f",loudnorm=I={self.loudness_lufs}:TP=-1:LRA=11"
                self._run([*opts, "-threads", str(self.threads), "-i", str(visual.resolve()),
                           "-threads", str(self.threads), "-i", str(audio.resolve()),
                           "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, "-af", af,
                           "-t", str(span), "-filter_threads", "1", "-threads", str(self.threads),
                           "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
                           "-c:a", "flac", "-ar", "48000", "-ac", "2", str(target.resolve())], cwd=out.parent)
                self._check_disk(out.parent)
                return MediaResult(target, "ffmpeg", "bounded-scene", span)

            # Concat reads immutable blobs directly: no second copy of long scene media.
            result, _ = cache.produce(key, out.parent / ".segments" / f"scene-{index:06d}.mkv", encode,
                                      materialize=False)
            paths.append(result.path); total += span
            cache.prune_segments(self.segment_cache_max_bytes, protected)
        # Video stream copy plus 192 kbps AAC; allow container overhead. This is an
        # estimate, not a filesystem quota; a temporary scene can exceed the budget.
        estimate = math.ceil((sum(p.stat().st_size for p in paths) + total * 24000) * 1.1)
        self._check_disk(out.parent, estimate)
        with tempfile.TemporaryDirectory(prefix=".concat-", dir=out.parent) as temp:
            root = Path(temp)
            listing = root / "inputs.txt"
            # Paths are data in ffconcat, not shell. Quote apostrophes per concat syntax.
            listing.write_text("\n".join("file '" + p.resolve().as_posix().replace("'", "'\\''") + "'" for p in paths), encoding="utf-8")
            target = root / "complete.mp4"
            self._run(["-f", "concat", "-safe", "0", "-threads", str(self.threads), "-i", str(listing.resolve()), "-map", "0:v:0", "-map", "0:a:0",
                       "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-threads", str(self.threads),
                       "-movflags", "+faststart", str(target.resolve())], cwd=out.parent)
            self._check_disk(out.parent)
            target.replace(out)
        return MediaResult(out, "ffmpeg", "bounded-h264", round(total, 3), notes="one scene at a time; cached; lossless FLAC intermediates")
