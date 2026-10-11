"""Low-memory renderer: encode one scene at a time, concatenate without re-encoding video.

Intermediate audio is losslessly compressed FLAC, avoiding cumulative AAC priming gaps across hundreds of
scenes. Scene durations are rounded up to whole frames and shared with captions.
"""
from __future__ import annotations

import math
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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Each scene fades within its own duration; no overlapping audio/timestamps.
        self.transition_s = 0.0

    def assemble(self, segments: list[tuple[Path, Path, float]], out: Path, fps: int, resolution: str) -> MediaResult:
        if not segments or fps < 1:
            raise MediaError("bounded render requires scenes and positive fps")
        out.parent.mkdir(parents=True, exist_ok=True)
        w, h = (int(x) for x in resolution.lower().split("x"))
        cache = RenderCache(out.parent / ".segments")
        paths: list[Path] = []
        total = 0.0
        for index, (visual, audio, fallback) in enumerate(segments):
            duration = probe_duration(audio) or fallback
            span = frame_span(duration, self.tail_pad_s, fps)
            moving = visual.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
            motion = "static" if moving else motion_for(index, self.motion)
            key = {"format": 2, "visual": digest(visual), "audio": digest(audio), "span": span,
                   "fps": fps, "resolution": resolution, "motion": motion, "fit": self.fit,
                   "lufs": self.loudness_lufs, "binary": self.binary}

            def encode(target: Path, visual=visual, audio=audio, span=span, motion=motion, moving=moving) -> MediaResult:
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
                return MediaResult(target, "ffmpeg", "bounded-scene", span)

            # Concat reads immutable blobs directly: no second copy of long scene media.
            result, _ = cache.produce(key, out.parent / ".segments" / f"scene-{index:06d}.mkv", encode,
                                      materialize=False)
            paths.append(result.path); total += span
        with tempfile.TemporaryDirectory(prefix=".concat-", dir=out.parent) as temp:
            root = Path(temp)
            listing = root / "inputs.txt"
            # Paths are data in ffconcat, not shell. Quote apostrophes per concat syntax.
            listing.write_text("\n".join("file '" + p.resolve().as_posix().replace("'", "'\\''") + "'" for p in paths), encoding="utf-8")
            target = root / "complete.mp4"
            self._run(["-f", "concat", "-safe", "0", "-threads", str(self.threads), "-i", str(listing.resolve()), "-map", "0:v:0", "-map", "0:a:0",
                       "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-threads", str(self.threads),
                       "-movflags", "+faststart", str(target.resolve())], cwd=out.parent)
            target.replace(out)
        return MediaResult(out, "ffmpeg", "bounded-h264", round(total, 3), notes="one scene at a time; cached; lossless FLAC intermediates")
