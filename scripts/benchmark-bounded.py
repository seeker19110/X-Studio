"""Offline Linux benchmark; synthetic fixtures, never a provider or upload call.

Run each size in a fresh process for comparable child-process peak RSS:
  uv run python scripts/benchmark-bounded.py --scenes 12
  uv run python scripts/benchmark-bounded.py --scenes 120
"""
from __future__ import annotations

import argparse
import json
import math
import resource
import struct
import tempfile
import time
import wave
from pathlib import Path

from studio.long_render import BoundedAssembler
from studio.media import probe_duration
from studio.qc import qc_video


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenes", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.scenes <= 1000:
        parser.error("scenes must be between 1 and 1000")
    with tempfile.TemporaryDirectory(prefix="xstudio-benchmark-") as directory:
        root = Path(directory)
        visual = root / "visual.ppm"
        visual.write_bytes(b"P6\n320 180\n255\n" + bytes((48, 128, 192)) * 320 * 180)
        segments = []
        for index in range(args.scenes):
            audio = root / f"voice-{index}.wav"
            with wave.open(str(audio), "wb") as stream:
                stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                stream.writeframes(b"".join(struct.pack("<h", int(4000 * math.sin(
                    2 * math.pi * (220 + index) * sample / 24000))) for sample in range(48000)))
            segments.append((visual, audio, 2.0))
        renderer = BoundedAssembler(threads=1, motion="none", timeout=300)
        start = time.monotonic()
        first = renderer.assemble(segments, root / "cold.mp4", 15, "320x180")
        cold = time.monotonic() - start
        cache = root / ".segments"
        before = {p.name: p.stat().st_mtime_ns for p in cache.glob("*.mkv")}
        start = time.monotonic()
        second = renderer.assemble(segments, root / "warm.mp4", 15, "320x180")
        warm = time.monotonic() - start
        after = {p.name: p.stat().st_mtime_ns for p in cache.glob("*.mkv")}
        duration = probe_duration(second.path)
        assert duration is not None and abs(duration - first.duration_s) < 0.2
        assert before == after and len(before) == args.scenes
        start = time.monotonic()
        qc = qc_video(second.path, expected_duration_s=first.duration_s,
                      expected_resolution="320x180", expected_fps=15)
        qc_wall = time.monotonic() - start
        assert qc.metrics.get("scan_complete") and not qc.blocked, qc.as_dict()
        print(json.dumps({
            "scenes": args.scenes, "resolution": "320x180", "fps": 15, "threads": 1,
            "duration_s": duration, "cold_wall_s": round(cold, 3), "warm_wall_s": round(warm, 3),
            "child_peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            "python_peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "segment_bytes": sum(p.stat().st_size for p in cache.rglob("*") if p.is_file()),
            "output_bytes": second.path.stat().st_size, "cache_unchanged": before == after,
            "qc_wall_s": round(qc_wall, 3), "qc_scan_complete": qc.metrics["scan_complete"],
            "fixture": "solid PPM and distinct 2-second sine waves; not speech or provider quality",
        }, indent=2))


if __name__ == "__main__":
    main()
