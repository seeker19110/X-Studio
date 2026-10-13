"""Small declarative video-as-code templates, rendered by FFmpeg without a browser.

The model/user supplies data (`MotionSpec`), never executable code. Text goes through UTF-8 files;
charts require a source and finite values. Heavier engines can export licensed clips
into the production library without changing the orchestration contract.

ADR-0017: the layout is measured by CODE (`plan`): every text block is wrapped and shrunk until it fits
its box inside the safe area (above burned captions, clear of the Shorts interface), and text that
cannot fit at the minimum size is refused instead of being cut. Numbers on `bars`/`stat` must appear
in a sourced claim of the fact-checked script (`verify_graphics`) before a manifest is published.
"""
from __future__ import annotations

import math
import re
import shutil
import tempfile
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .media import FFmpegAssembler, MediaError, MediaResult, find_font
from .scene_graphic import NUMERIC
from .scene_graphic import MotionSpec as MotionSpec

LAYOUT_VERSION = 2  # in the render cache key: a layout change must re-render, never reuse an old clip
MIN_TEXT = 0.026    # smallest font size, as a fraction of the short side of the frame
CHAR_W = 0.62       # average glyph width / font size (DejaVu Sans, with slack for capitals and diacritics)
LINE = 1.25         # line height / font size
COUNT_S = 0.8       # stat counts up during this many seconds
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass(frozen=True)
class Text:
    name: str
    text: str
    x: float
    y: float
    size: float
    color: str
    chars: int                    # wrap width used, in characters per line
    start: float = 0.0            # appears at this second (list items are staggered)
    count_to: int | None = None   # stat: count up from 0 to this whole number

    @property
    def height(self) -> float:
        return len(self.text.split("\n")) * self.size * LINE

    @property
    def width(self) -> float:
        return max(len(line) for line in self.text.split("\n")) * self.size * CHAR_W


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float
    color: str


def safe_bottom(w: int, h: int, captions: bool) -> float:
    """Lowest y a graphic may use. Shorts (vertical) keep the bottom fifth for the channel name, description and
    buttons; landscape keeps about a quarter for two lines of burned captions (`production.finish`)."""
    if h > w: return h * 0.78
    return h * (0.76 if captions else 0.92)


def _fit(text: str, width: float, max_h: float, pref: float, minimum: float) -> tuple[str, float, int]:
    size = pref
    while True:
        chars = max(1, int(width / (size * CHAR_W)))
        lines = textwrap.wrap(text, width=chars, break_on_hyphens=False) or [""]
        if len(lines) * size * LINE <= max_h + 1e-9:
            return "\n".join(lines), size, chars
        if size <= minimum + 1e-9:
            raise MediaError(f"chữ '{text[:40]}' không vừa khung đồ họa ({len(lines)} dòng cỡ {size:.0f}px > "
                             f"{max_h:.0f}px): rút ngắn chữ hoặc chia cảnh")
        size = max(minimum, size * 0.9)


def _fmt(value: float) -> str:
    """Show the claim's number as written: whole numbers without '.0', others as the shortest exact form (repr),
    never rounded the way '%g' would (123456789.5 → '1.23457e+08')."""
    return str(int(value)) if value.is_integer() else repr(value)


def plan(spec: MotionSpec, w: int, h: int, duration: float, bottom: float) -> tuple[list[Text], list[Box]]:
    """Pure layout of one graphic: where each text block goes and at which size. No FFmpeg here, so tests can
    check every template against the safe area. The content block is centred vertically between the top margin
    and the safe bottom (or the source line pinned above it)."""
    u = min(w, h); low = MIN_TEXT * u; gap = 0.035 * u
    margin = w * 0.08; width = w - 2 * margin
    top = h * (0.12 if h > w else 0.08)
    accent, white = spec.accent, "FFFFFF"
    flow: list[Text] = []; bars: list[Box] = []
    pinned: list[Text] = []
    y = top

    def fit(name: str, value: str, pref: float, max_h: float, color: str = white, start: float = 0.0,
            box_w: float = width) -> Text:
        wrapped, size, chars = _fit(value, box_w, max(0.0, max_h), max(pref, low), low)
        return Text(name, wrapped, margin, y, size, color, chars, start=start)

    def add(t: Text) -> Text:
        nonlocal y
        t = Text(t.name, t.text, t.x, y, t.size, t.color, t.chars, t.start, t.count_to)
        flow.append(t); y += t.height + gap
        return t

    floor = bottom
    if spec.source and spec.template in NUMERIC:  # numbers carry their source pinned to the bottom of the safe area
        wrapped, size, chars = _fit(spec.source, width, (bottom - top) * 0.18, 0.03 * u, low)
        src = Text("source", wrapped, margin, bottom - len(wrapped.split("\n")) * size * LINE, size, "B8C4CC", chars)
        pinned.append(src); floor = src.y - gap
    if spec.template == "title":
        add(fit("title", spec.title, 0.09 * u, (floor - y) * 0.6, accent))
        if spec.subtitle: add(fit("subtitle", spec.subtitle, 0.05 * u, floor - y - gap - 3))
        bars.append(Box(margin, y - gap / 2, w * 0.2, 3, accent))
    elif spec.template == "quote":
        add(fit("title", spec.title, 0.075 * u, (floor - y) * 0.7, accent))
        if spec.subtitle: add(fit("subtitle", spec.subtitle, 0.045 * u, floor - y))
    elif spec.template == "list":
        add(fit("title", spec.title, 0.075 * u, (floor - y) * 0.3, accent))
        n = len(spec.labels); share = (floor - y) / n
        step = min(0.6, duration * 0.5 / n)
        for i, label in enumerate(spec.labels):
            add(fit(f"item{i}", "• " + label, 0.055 * u, share - gap, start=round(0.3 + i * step, 3)))
    elif spec.template == "bars":
        add(fit("title", spec.title, 0.075 * u, (floor - y) * 0.3, accent))
        # each row: label (left) and value (right) on one text line, the bar below at half the text size
        n = len(spec.values); rg = 0.0125 * u; row = (floor - y + rg) / n
        size = min(0.05 * u, (row - rg) / (LINE + 0.5))
        if size < low - 1e-9:
            raise MediaError(f"{n} cột không vừa khung đồ họa: bớt cột hoặc chia cảnh")
        biggest = max(max(spec.values), 1e-9)
        for i, (label, value) in enumerate(zip(spec.labels, spec.values, strict=True)):
            shown = _fmt(value) + spec.unit
            # a narrow (vertical) frame may wrap the label onto a second line when the row has the height
            label_t = fit(f"label{i}", label, size, row - rg - size * 0.5,
                          box_w=width - (len(shown) + 1) * size * CHAR_W)
            label_t = Text(label_t.name, label_t.text, margin, y, min(label_t.size, size), white, label_t.chars)
            flow.append(label_t)
            flow.append(Text(f"value{i}", shown, margin + width - len(shown) * size * CHAR_W, y, size, white, len(shown)))
            bars.append(Box(margin, y + label_t.height, max(2.0, width * value / biggest), size * 0.5, accent))
            y += label_t.height + size * 0.5 + rg
        y += gap - rg
    else:  # stat: caption above, one big number (+ unit), optional subtitle below
        add(fit("title", spec.title, 0.075 * u, (floor - y) * 0.3, accent))
        sub = fit("subtitle", spec.subtitle, 0.045 * u, (floor - y) * 0.4) if spec.subtitle else None
        value = spec.values[0]; number = _fmt(value)
        whole = value.is_integer() and value < 1e9
        room = floor - y - (sub.height + gap if sub else 0)
        # number + half a space + unit at 45% size (never below the minimum), centred; both bounds hold either way
        digits = len(number) + (0.5 if spec.unit else 0)
        size = min(0.22 * u, room / LINE, width / ((digits + 0.45 * len(spec.unit)) * CHAR_W),
                   (width - len(spec.unit) * low * CHAR_W) / (digits * CHAR_W))
        if size < low - 1e-9:
            raise MediaError("con số không vừa khung đồ họa: rút ngắn chú thích hoặc chia cảnh")
        small = min(size, max(low, size * 0.45))
        total = digits * size * CHAR_W + len(spec.unit) * small * CHAR_W
        x = margin + (width - total) / 2
        flow.append(Text("number", number, x, y, size, accent, len(number), count_to=int(value) if whole else None))
        if spec.unit:  # baselines aligned: glyph ascent ≈ 0.78 of the font size
            flow.append(Text("unit", spec.unit, x + (len(number) + 0.5) * size * CHAR_W, y + (size - small) * 0.78,
                             small, white, len(spec.unit)))
        y += size * LINE + gap
        if sub: add(sub)
    # centre the flowing block between the top margin and the floor
    shift = max(0.0, (floor - (y - gap)) / 2)
    texts = [Text(t.name, t.text, t.x, t.y + shift, t.size, t.color, t.chars, t.start, t.count_to) for t in flow]
    boxes = [Box(0, 0, w, max(3.0, h * 0.015), accent)] + [Box(b.x, b.y + shift, b.w, b.h, b.color) for b in bars]
    return texts + pinned, boxes


def numbers_in(text: str) -> set[float]:
    """Every reading of the numbers in `text`: '1.234,5' and '1,234.5' are 1234.5; '1,5' may be 1.5 or 15."""
    out: set[float] = set()
    for token in _NUMBER.findall(text):
        out.add(float(re.sub(r"[.,]", "", token)))
        last = max(token.rfind("."), token.rfind(","))
        if last >= 0:
            out.add(float(re.sub(r"[.,]", "", token[:last]) + "." + token[last + 1:]))
    return out


def _known(value: float, known: Iterable[float]) -> bool:
    return any(math.isclose(value, n, rel_tol=1e-9, abs_tol=1e-9) for n in known)


def verify_graphics(payload: dict[str, Any], script: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Drop every numeric graphic whose values are not in a SOURCED claim of the fact-checked script. The scene keeps
    its narration and falls back to its image: an unverified chart never stops the video, it is just not drawn."""
    known: set[float] = set()
    for claim in (script or {}).get("claims") or []:
        if isinstance(claim, dict) and str(claim.get("source") or "").strip():
            known |= numbers_in(str(claim.get("text") or ""))
    dropped: list[dict[str, Any]] = []
    scenes: list[Any] = []
    for scene in payload.get("scenes") or []:
        graphic = scene.get("graphic") if isinstance(scene, dict) else None
        if isinstance(graphic, dict) and graphic.get("template") in NUMERIC:
            missing = [float(v) for v in graphic.get("values") or [] if not _known(float(v), known)]
            if missing:
                dropped.append({"scene_id": scene.get("scene_id"), "template": graphic["template"], "unverified": missing})
                scene = {**scene, "graphic": None}
        scenes.append(scene)
    return ({**payload, "scenes": scenes} if dropped else payload), dropped


def _drawtext(t: Text, root: Path, duration: float) -> str:
    if t.count_to is not None:
        # The only expanded text: written by code from a validated whole number, never by the model.
        (root / f"{t.name}.txt").write_text(f"%{{eif:min({t.count_to},{t.count_to}*t/{COUNT_S}):d}}", encoding="utf-8")
        expansion = "normal"
    else:
        (root / f"{t.name}.txt").write_text(t.text, encoding="utf-8")
        expansion = "none"  # '%' / '%{...}' in model text stays literal
    if t.start > 0:
        x = f"{t.x:.1f}"
        alpha = f":alpha=if(lt(t\\,{t.start})\\,0\\,min(1\\,(t-{t.start})/0.3))"
    else:
        x, alpha = f"{t.x:.1f}+20*exp(-4*t)", ""
    return (f"drawtext=fontfile=font.ttf:textfile={t.name}.txt:expansion={expansion}:fontsize={t.size:.1f}:"
            f"fontcolor=0x{t.color}:x={x}:y={t.y:.1f}:line_spacing={t.size * (LINE - 1):.1f}{alpha}")


def render_motion(spec: MotionSpec, out: Path, video: FFmpegAssembler, duration: float,
                  fps: int, resolution: str, bottom: float | None = None) -> MediaResult:
    font = find_font()
    if font is None:
        raise MediaError("motion graphics require a Unicode font")
    w, h = map(int, resolution.split("x"))
    texts, boxes = plan(spec, w, h, duration, safe_bottom(w, h, False) if bottom is None else bottom)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".motion-", dir=out.parent) as temp:
        root = Path(temp); shutil.copyfile(font, root / "font.ttf")
        filters = [f"drawbox=x={b.x:.1f}:y={b.y:.1f}:w={b.w:.1f}:h={b.h:.1f}:color=0x{b.color}:t=fill" for b in boxes]
        filters += [_drawtext(t, root, duration) for t in texts]
        filters += ["fade=t=in:d=0.25", f"fade=t=out:st={max(0, duration-.25)}:d=0.25"]
        video._run(["-f", "lavfi", "-i", f"color=c=0x{spec.background}:s={resolution}:r={fps}:d={duration}",
                    "-vf", ",".join(filters), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "20", "-threads", str(video.threads), "-movflags", "+faststart", str(out.resolve())], cwd=root)
    return MediaResult(out, "studio", f"motion-{spec.template}", duration)
