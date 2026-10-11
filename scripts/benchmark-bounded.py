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

from studio import qc as qc_module
from studio.long_render import BoundedAssembler
from studio.media import MediaError, probe_duration
from studio.qc import qc_video


class DeadlineAssembler(BoundedAssembler):
    """Each subprocess gets only the time left in this benchmark's wall budget."""

    def __init__(self, deadline: float):
        super().__init__(threads=1, motion="none", timeout=300, segment_cache_max_bytes=512 * 1024 * 1024)
        self.deadline = deadline
        self.encodes = 0

    def _run(self, args: list[str], cwd: Path | None = None) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise MediaError("benchmark wall budget exhausted")
        self.timeout = min(300, remaining)
        if "libx264" in args:
            self.encodes += 1
        super()._run(args, cwd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenes", type=int, default=12)
    parser.add_argument("--scene-seconds", type=int, default=2)
    parser.add_argument("--resolution", choices=["320x180", "640x360"], default="320x180")
    parser.add_argument("--wall-budget", type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.scenes <= 1000 or not 1 <= args.scene_seconds <= 60:
        parser.error("scenes must be 1–1000 and scene-seconds 1–60")
    if args.scenes * args.scene_seconds > 3600 or not 1 <= args.wall_budget <= 1800:
        parser.error("synthetic content limited to 1 hour; wall-budget must be 1–1800 seconds")
    deadline = time.monotonic() + args.wall_budget
    cpu_start = resource.getrusage(resource.RUSAGE_CHILDREN)
    with tempfile.TemporaryDirectory(prefix="xstudio-benchmark-") as directory:
        root = Path(directory)
        visual = root / "visual.ppm"
        width, height = (int(x) for x in args.resolution.split("x"))
        visual.write_bytes(f"P6\n{width} {height}\n255\n".encode() + bytes((48, 128, 192)) * width * height)
        segments = []
        for index in range(args.scenes):
            if time.monotonic() >= deadline:
                raise MediaError("benchmark wall budget exhausted while making fixtures")
            audio = root / f"voice-{index}.wav"
            with wave.open(str(audio), "wb") as stream:
                stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                # One second at a time: fixture memory does not grow with scene length.
                second = b"".join(struct.pack("<h", int(4000 * math.sin(
                    2 * math.pi * (220 + index) * sample / 24000))) for sample in range(24000))
                for _ in range(args.scene_seconds):
                    stream.writeframes(second)
            segments.append((visual, audio, float(args.scene_seconds)))
        renderer = DeadlineAssembler(deadline)
        start = time.monotonic()
        first = renderer.assemble(segments, root / "cold.mp4", 15, args.resolution)
        cold = time.monotonic() - start
        cold_encodes = renderer.encodes
        cache = root / ".segments"
        before = {p.name: p.stat().st_mtime_ns for p in cache.glob("*.mkv")}
        start = time.monotonic()
        second = renderer.assemble(segments, root / "warm.mp4", 15, args.resolution)
        warm = time.monotonic() - start
        after = {p.name: p.stat().st_mtime_ns for p in cache.glob("*.mkv")}
        duration = probe_duration(second.path)
        assert duration is not None and abs(duration - first.duration_s) < 0.2
        assert before == after and len(before) == args.scenes
        assert renderer.encodes == cold_encodes == args.scenes
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MediaError("benchmark wall budget exhausted before QC")
        qc_module.TIMEOUT_S = remaining
        start = time.monotonic()
        qc = qc_video(second.path, expected_duration_s=first.duration_s,
                      expected_resolution=args.resolution, expected_fps=15)
        qc_wall = time.monotonic() - start
        assert qc.metrics.get("scan_complete") and not qc.blocked, qc.as_dict()
        cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
        print(json.dumps({
            "scenes": args.scenes, "scene_seconds": args.scene_seconds,
            "resolution": args.resolution, "fps": 15, "threads": 1,
            "duration_s": duration, "cold_wall_s": round(cold, 3), "warm_wall_s": round(warm, 3),
            "child_peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            "python_peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "segment_bytes": sum(p.stat().st_size for p in cache.rglob("*") if p.is_file()),
            "output_bytes": second.path.stat().st_size, "cache_unchanged": before == after,
            "qc_wall_s": round(qc_wall, 3), "qc_scan_complete": qc.metrics["scan_complete"],
            "cold_scene_encodes": cold_encodes, "warm_scene_encodes": renderer.encodes - cold_encodes,
            "child_cpu_s": round(cpu.ru_utime + cpu.ru_stime - cpu_start.ru_utime - cpu_start.ru_stime, 3),
            "wall_budget_s": args.wall_budget,
            "segment_cache_budget_bytes": renderer.segment_cache_max_bytes,
            "workspace_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
            "fixture": "solid PPM and distinct sine waves; not speech or provider quality",
        }, indent=2))


if __name__ == "__main__":
    main()
