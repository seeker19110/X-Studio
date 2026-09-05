"""Dòng thời gian của bản dựng: CODE tính, không model nào đoán.

Mọi thứ phụ thuộc "cảnh này bắt đầu ở giây thứ mấy" đều lấy từ đây, và con số đó chỉ đúng khi khớp đúng cách
assembler ghép: mỗi cảnh dài bằng giọng đọc cộng `pad` giây im lặng, mỗi mối nối ăn mất `transition` giây.

- `timeline(manifest)`: danh sách cảnh kèm mốc bắt đầu/kết thúc trong video cuối.
- `srt(cues)`: phụ đề SRT sinh thẳng từ narration của manifest — không cần nhận dạng giọng nói, vì narration CHÍNH LÀ
  văn bản đã đọc; phụ đề vừa là tiếp cận, vừa là SEO, vừa là điều kiện xem không tiếng trên di động.
- `snap_chapters(...)`: seo-optimizer viết nhãn chapter TRƯỚC khi có video nên mốc thời gian của nó là số đoán; code
  nắn từng mốc về đầu cảnh gần nhất, bỏ mốc vi phạm giới hạn nền tảng. Model đặt tên, code đặt giờ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .events import Chapter, SceneManifest

MIN_CHAPTER_GAP_S = 10.0  # YouTube: hai chapter cách nhau < 10 giây thì không hiện
_TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")


@dataclass
class Cue:
    scene_id: str
    start: float
    end: float  # hết giọng đọc, chưa tính đoạn im lặng đệm
    text: str


def parse_time(t: str) -> float | None:
    m = _TIME.match(t.strip())
    if not m: return None
    h, mm, ss = m.group(1) or "0", m.group(2), m.group(3)
    return int(h) * 3600 + int(mm) * 60 + int(ss)


def stamp(seconds: float, sep: str = ",") -> str:
    s = max(0.0, seconds)
    h, rem = divmod(int(s), 3600); m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}{sep}{round((s - int(s)) * 1000):03d}"


def chapter_time(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600); m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def timeline(m: SceneManifest, order: list[str] | None = None, pad: float = 0.0, transition: float = 0.0) -> list[Cue]:
    """Mốc từng cảnh trong bản dựng cuối. Công thức khớp assembler: cảnh i bắt đầu ở tổng (thời lượng + pad) của các
    cảnh trước, trừ đi `transition` cho mỗi mối nối đã đi qua."""
    by_id = {s.scene_id: s for s in m.scenes}
    ids = [sid for sid in (order or [])] or [s.scene_id for s in sorted(m.scenes, key=lambda x: x.order)]
    cues: list[Cue] = []; t = 0.0
    for i, sid in enumerate(ids):
        s = by_id.get(sid)
        if s is None: continue
        start = max(0.0, t - i * transition)
        cues.append(Cue(sid, round(start, 3), round(start + s.duration_s, 3), s.narration))
        t = start + s.duration_s + pad + i * transition
    return cues


def srt(cues: list[Cue]) -> str:
    out: list[str] = []
    for i, c in enumerate(cues, 1):
        if not c.text.strip(): continue
        out.append(f"{i}\n{stamp(c.start)} --> {stamp(max(c.end, c.start + 0.5))}\n{c.text.strip()}\n")
    return "\n".join(out)


def snap_chapters(chapters: list[Chapter], cues: list[Cue], min_gap: float = MIN_CHAPTER_GAP_S) -> list[Chapter]:
    """Nắn mốc chapter về đầu cảnh gần nhất, giữ nhãn của seo-optimizer. Bỏ mốc trùng cảnh, mốc quá gần mốc trước
    (< `min_gap`) và mốc nằm ngoài video; chapter đầu luôn 00:00. Danh sách rỗng vào thì rỗng ra."""
    if not chapters or not cues: return list(chapters)
    starts = [c.start for c in cues]
    out: list[Chapter] = []; used: set[float] = set(); prev = -min_gap
    for i, ch in enumerate(chapters):
        want = 0.0 if i == 0 else parse_time(ch.time)
        if want is None: continue
        snapped = min(starts, key=lambda s: (abs(s - want), s))
        if i == 0: snapped = starts[0]
        if snapped in used or snapped - prev < min_gap: continue
        used.add(snapped); prev = snapped
        out.append(Chapter(time=chapter_time(snapped), label=ch.label))
    return out
