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

import math
import re
import textwrap
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
    total, ms = divmod(round(max(0.0, seconds) * 1000), 1000)
    h, rem = divmod(total, 3600); m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}{sep}{ms:03d}"


def chapter_time(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600); m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def timeline(m: SceneManifest, order: list[str] | None = None, pad: float = 0.0, transition: float = 0.0,
             frame_rate: int | None = None) -> list[Cue]:
    """Mốc từng cảnh trong bản dựng cuối. Công thức khớp assembler: cảnh i bắt đầu ở tổng (thời lượng + pad) của các
    cảnh trước, trừ đi `transition` cho mỗi mối nối đã đi qua."""
    by_id = {s.scene_id: s for s in m.scenes}
    ids = [sid for sid in (order or [])] or [s.scene_id for s in sorted(m.scenes, key=lambda x: x.order)]
    cues: list[Cue] = []; t = 0.0
    for i, sid in enumerate(ids):
        s = by_id.get(sid)
        if s is None: continue
        if s.clip_start_s is not None:
            cues.append(Cue(sid,s.clip_start_s,round(s.clip_start_s+s.duration_s,3),s.narration)); continue
        start = max(0.0, t - i * transition)
        cues.append(Cue(sid, round(start, 3), round(start + s.duration_s, 3), s.narration))
        span = s.duration_s + pad
        if frame_rate:
            span = math.ceil(span * frame_rate - 1e-8) / frame_rate
        t = start + span + i * transition
    return cues


Word = tuple[float, float, str]  # (start, end, từ) tính từ đầu audio của cảnh


def _norm(word: str) -> str:
    """Từ để so khớp mốc: bỏ dấu câu, không phân biệt hoa thường (edge-tts trả "bạn" cho "bạn,")."""
    return re.sub(r"[^\w]", "", word.casefold())


def _page_layout(tokens: list[str], max_chars: int, max_lines: int) -> list[list[list[int]]]:
    """Trang → dòng → chỉ số từ: dòng gom từ tới `max_chars` (từ dài hơn đứng riêng), trang = `max_lines` dòng."""
    lines: list[list[int]] = []
    for i, word in enumerate(tokens):
        if lines and len(" ".join(tokens[j] for j in lines[-1]) + " " + word) <= max_chars:
            lines[-1].append(i)
        else:
            lines.append([i])
    return [lines[k:k + max_lines] for k in range(0, len(lines), max_lines)]


def _timed(cue: Cue, words: dict[str, list[Word]] | None) -> tuple[list[Word], list[str]] | None:
    """Mốc của cảnh khi dãy từ khớp lời đọc (bỏ dấu câu, không phân biệt hoa thường); None = chia theo tỷ lệ."""
    timed, tokens = (words or {}).get(cue.scene_id), " ".join(cue.text.split()).split()
    if timed and [_norm(str(w[2])) for w in timed] == [_norm(t) for t in tokens]:
        return timed, tokens
    return None


def _word_pages(cue: Cue, words: list[Word], tokens: list[str], max_chars: int, max_lines: int) -> list[Cue]:
    """Trang phụ đề theo mốc từng từ: trang bắt đầu lúc từ đầu được đọc, kết thúc lúc từ cuối dứt, kẹp trong khoảng
    cảnh. Chữ hiển thị là `tokens` của lời đọc gốc (giữ dấu câu), mốc lấy từ `words` cùng vị trí."""
    out: list[Cue] = []
    for page in _page_layout(tokens, max_chars, max_lines):
        first, last = page[0][0], page[-1][-1]
        start = min(cue.end, round(cue.start + float(words[first][0]), 3))
        end = min(cue.end, round(cue.start + float(words[last][1]), 3))
        out.append(Cue(cue.scene_id, start, max(end, start + 0.001),
                       "\n".join(" ".join(tokens[j] for j in line) for line in page)))
    return out


def caption_pages(cues: list[Cue], max_chars: int = 42, max_lines: int = 2,
                  words: dict[str, list[Word]] | None = None) -> list[Cue]:
    """Readable caption pages. With provider word timestamps (`words[scene_id]`, same tokens as the narration) pages
    follow the real speech; otherwise timings are proportional estimates, NOT word alignment.

    Keep every word inside its measured narration interval. No ASR/model/API call.
    """
    if max_chars < 1 or max_lines < 1:
        raise ValueError("caption limits must be positive")
    out: list[Cue] = []
    for cue in cues:
        text = " ".join(cue.text.split())
        duration = cue.end - cue.start
        if not text or duration <= 0:
            continue
        matched = _timed(cue, words)
        if matched:
            out += _word_pages(cue, *matched, max_chars, max_lines)
            continue
        lines = textwrap.wrap(text, width=max_chars, break_on_hyphens=False)
        chunks = ["\n".join(lines[i:i + max_lines]) for i in range(0, len(lines), max_lines)]
        if len(chunks) > max(1, int(duration * 1000)):
            chunks = [text]  # degenerate interval: never emit zero-length SRT entries
        weights = [len(chunk.replace("\n", " ")) for chunk in chunks]
        total = sum(weights)
        elapsed = 0
        for chunk, weight in zip(chunks, weights, strict=True):
            start = cue.start + duration * elapsed / total
            elapsed += weight
            end = cue.end if elapsed == total else cue.start + duration * elapsed / total
            out.append(Cue(cue.scene_id, start, end, chunk))
    return out


def _ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_text(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\\", "/")


def karaoke_ass(cues: list[Cue], words: dict[str, list[Word]], resolution: str, max_chars: int = 42,
                max_lines: int = 2) -> str:
    """Phụ đề karaoke để ĐỐT CỨNG (libass): mỗi trang một Dialogue, mỗi từ một thẻ `\\k` (centi-giây từ lúc từ bắt đầu
    tới lúc từ sau bắt đầu) nên từ đổi màu đúng lúc được đọc. Cảnh không có mốc khớp lời đọc vẫn có trang chia theo tỷ
    lệ, không thẻ `\\k`. YouTube không nhận ASS: SRT vẫn là phụ đề đăng lên nền tảng."""
    width, height = (int(x) for x in resolution.lower().split("x"))
    size, margin = round(height * 0.055), round(height * 0.06)
    out = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2", "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
           "MarginR, MarginV, Encoding",
           f"Style: Karaoke,DejaVu Sans,{size},&H0000D7FF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,"
           f"{max(2, size // 20)},0,2,{margin},{margin},{margin},1", "",
           "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for cue in cues:
        if not cue.text.strip() or cue.end <= cue.start:
            continue
        matched = _timed(cue, words)
        if matched is None:
            for plain in caption_pages([cue], max_chars=max_chars, max_lines=max_lines):
                out.append(f"Dialogue: 0,{_ass_time(plain.start)},{_ass_time(plain.end)},Karaoke,,0,0,0,,"
                           + _ass_text(plain.text).replace("\n", "\\N"))
            continue
        timed, tokens = matched
        for page in _page_layout(tokens, max_chars, max_lines):
            flat = [i for line in page for i in line]
            parts = []
            for n, line in enumerate(page):
                for j, i in enumerate(line):
                    nxt = float(timed[i + 1][0]) if i != flat[-1] else float(timed[i][1])
                    gap = "\\N" if n and j == 0 else (" " if j else "")
                    parts.append(f"{gap}{{\\k{max(1, round((nxt - float(timed[i][0])) * 100))}}}{_ass_text(tokens[i])}")
            start = min(cue.end, cue.start + float(timed[flat[0]][0]))
            end = min(cue.end, cue.start + float(timed[flat[-1]][1]))
            out.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(max(end, start + 0.01))},Karaoke,,0,0,0,,"
                       + "".join(parts))
    return "\n".join(out) + "\n"


def srt(cues: list[Cue]) -> str:
    out: list[str] = []
    for c in cues:
        if not c.text.strip(): continue
        out.append(f"{len(out) + 1}\n{stamp(c.start)} --> {stamp(max(c.end, c.start + 0.001))}\n{c.text.strip()}\n")
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
