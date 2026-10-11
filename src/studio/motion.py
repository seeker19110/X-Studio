"""Small declarative video-as-code templates, rendered by FFmpeg without a browser.

The model/user supplies data, never executable code. Text goes through UTF-8 files;
charts require a source and finite values. Heavier engines can export licensed clips
into the production library without changing the orchestration contract.
"""
from __future__ import annotations

import math
import shutil
import tempfile
import textwrap
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .media import FFmpegAssembler, MediaError, MediaResult, find_font


class MotionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: Literal["title", "quote", "bars"] = "title"
    title: str = Field(min_length=1, max_length=160)
    subtitle: str = Field(default="", max_length=240)
    accent: str = Field(default="55DDB0", pattern=r"^[0-9A-Fa-f]{6}$")
    background: str = Field(default="101F29", pattern=r"^[0-9A-Fa-f]{6}$")
    labels: list[str] = Field(default_factory=list, max_length=6)
    values: list[float] = Field(default_factory=list, max_length=6)
    source: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def valid_chart(self):
        if self.template == "bars" and (not self.source.strip() or not self.values or
                len(self.labels) != len(self.values) or any(not math.isfinite(v) or v < 0 for v in self.values)):
            raise ValueError("bars require a source, matching labels and finite nonnegative values")
        return self


def render_motion(spec: MotionSpec, out: Path, video: FFmpegAssembler, duration: float,
                  fps: int, resolution: str) -> MediaResult:
    font = find_font()
    if font is None:
        raise MediaError("motion graphics require a Unicode font")
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = map(int, resolution.split("x"))
    with tempfile.TemporaryDirectory(prefix=".motion-", dir=out.parent) as temp:
        root = Path(temp); shutil.copyfile(font, root / "font.ttf")
        margin = w * .08
        filters = [f"drawbox=x=0:y=0:w={w}:h={max(3, h*.015)}:color=0x{spec.accent}:t=fill"]

        def text(value: str, name: str, y: float, size: float, color: str = "FFFFFF") -> None:
            width = max(10, int((w - 2 * margin) / (size * .7)))
            (root / f"{name}.txt").write_text("\n".join(textwrap.wrap(value, width=width)), encoding="utf-8")
            # expansion=none prevents '%' / '%{...}' in input from being interpreted.
            filters.append(f"drawtext=fontfile=font.ttf:textfile={name}.txt:expansion=none:fontsize={size}:"
                           f"fontcolor=0x{color}:x={margin}+20*exp(-4*t):y={y}:line_spacing={size*.25}")

        text(spec.title, "title", h * .13, h * .075, spec.accent)
        if spec.template == "bars":
            maximum = max(max(spec.values), 1)
            row = h * .5 / len(spec.values)
            for i, (label, value) in enumerate(zip(spec.labels, spec.values, strict=True)):
                y = h * .33 + row * i
                bar = max(2, (w * .52) * value / maximum)
                filters.append(f"drawbox=x={w*.3}:y={y}:w={bar}:h={row*.5}:color=0x{spec.accent}:t=fill")
                text(label[:28], f"label{i}", y, h * .033)
                (root / f"value{i}.txt").write_text(f"{value:g}", encoding="utf-8")
                filters.append(f"drawtext=fontfile=font.ttf:textfile=value{i}.txt:fontsize={h*.033}:"
                               f"fontcolor=white:x={w*.3+bar+8}:y={y}")
            text(spec.source, "source", h * .90, h * .022)
        else:
            text(spec.subtitle, "subtitle", h * .48, h * (.055 if spec.template == "quote" else .045))
            filters.append(f"drawbox=x={margin}:y={h*.82}:w={w*.2}:h=3:color=0x{spec.accent}:t=fill")
        filters += ["fade=t=in:d=0.25", f"fade=t=out:st={max(0, duration-.25)}:d=0.25"]
        video._run(["-f", "lavfi", "-i", f"color=c=0x{spec.background}:s={resolution}:r={fps}:d={duration}",
                    "-vf", ",".join(filters), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "20", "-threads", str(video.threads), "-movflags", "+faststart", str(out.resolve())], cwd=root)
    return MediaResult(out, "studio", f"motion-{spec.template}", duration)
