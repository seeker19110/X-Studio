"""Ảnh cho reviewer đa phương thức (ADR-0020, DANH-GIA §5 C1): CODE chọn, kiểm và thu nhỏ ảnh; model chỉ nhìn.

Agent khai trong front matter `inputs:` thứ nó được xem:

- `scene_images` — ảnh nguồn từng cảnh, ghép thành lưới 4×3 có nhãn scene_id (video trăm cảnh vẫn là vài ảnh);
- `contact_sheet` — một khung giữa mỗi cảnh trích từ BẢN CUỐI (ADR-0016), bản cuối cùng tìm thấy;
- `thumbnails` — ảnh bìa đã hoàn thiện, tối đa 3.

Nguồn chỉ là asset trên bus mà orchestrator đã đưa vào payload/`extra` (dict có `kind` + `path`); không đọc đường dẫn
nào khác. File thiếu, hỏng hay không phải ảnh bị bỏ kèm lý do để runner ghi audit — thiếu ảnh không làm hỏng lượt
chạy, reviewer vẫn có JSON như trước. `sha256` của mỗi đính kèm là băm FILE NGUỒN (lưới: băm scene_id + băm từng
nguồn), nên khoá eval không đổi khi Pillow khác phiên bản nén ra bytes khác.
"""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from xagents_core.llm import Attachment

INPUTS = ("scene_images", "contact_sheet", "thumbnails")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_ATTACHMENTS = 6          # trần mỗi lượt: ảnh tốn token, lưới đã gom nhiều cảnh vào một ảnh
MAX_SOURCE_BYTES = 50_000_000
SHEET_MAX, THUMB_MAX = 1568, 768   # cạnh dài tối đa (Anthropic khuyên ≤ 1568 px)
TILE, COLS, ROWS = (384, 216), 4, 3


def _assets(data: Any) -> list[dict[str, Any]]:
    """Mọi dict asset (`kind` + `path` là chuỗi) theo thứ tự xuất hiện trong payload/extra."""
    out: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if isinstance(data.get("kind"), str) and isinstance(data.get("path"), str):
            out.append(data)
        for value in data.values():
            out += _assets(value)
    elif isinstance(data, list):
        for value in data:
            out += _assets(value)
    return out


def _load(path_text: str, notes: list[str]) -> tuple[Image.Image, str] | None:
    path = Path(path_text)
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        notes.append(f"bỏ {path.name}: không phải ảnh"); return None
    if not path.is_file():
        notes.append(f"bỏ {path.name}: không có file"); return None
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        notes.append(f"bỏ {path.name}: quá {MAX_SOURCE_BYTES // 1_000_000} MB"); return None
    try:
        img = Image.open(io.BytesIO(raw)); img.load()
    except (UnidentifiedImageError, OSError):
        notes.append(f"bỏ {path.name}: không đọc được ảnh"); return None
    return img.convert("RGB"), hashlib.sha256(raw).hexdigest()


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=85); return buf.getvalue()


def _single(img: Image.Image, limit: int) -> bytes:
    img = img.copy(); img.thumbnail((limit, limit)); return _jpeg(img)


def _grid(tiles: list[tuple[str, Image.Image]]) -> bytes:
    rows = math.ceil(len(tiles) / COLS)
    canvas = Image.new("RGB", (TILE[0] * COLS, TILE[1] * rows), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=20)
    for i, (scene_id, img) in enumerate(tiles):
        x, y = TILE[0] * (i % COLS), TILE[1] * (i // COLS)
        fitted = ImageOps.contain(img, TILE)
        canvas.paste(fitted, (x + (TILE[0] - fitted.width) // 2, y + (TILE[1] - fitted.height) // 2))
        box = draw.textbbox((x + 6, y + 4), scene_id, font=font)
        draw.rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2), fill=(0, 0, 0))
        draw.text((x + 6, y + 4), scene_id, fill=(255, 255, 255), font=font)
    return _jpeg(canvas)


def collect(inputs: list[str], data: Any) -> tuple[list[Attachment], list[str]]:
    """Đính kèm cho một lượt theo `inputs` của agent; trả (ảnh, lý do bỏ). Thứ tự: contact sheet, thumbnail, lưới cảnh."""
    unknown = [x for x in inputs if x not in INPUTS]
    if unknown:
        raise ValueError(f"inputs không hỗ trợ {unknown} (có: {', '.join(INPUTS)})")
    if not inputs:
        return [], []
    assets, notes = _assets(data), list[str]()
    out: list[Attachment] = []
    if "contact_sheet" in inputs:
        sheets = [a for a in assets if a["kind"] == "contact_sheet"]
        loaded = _load(sheets[-1]["path"], notes) if sheets else None
        if loaded:
            out.append(Attachment("image/jpeg", _single(loaded[0], SHEET_MAX), loaded[1], "contact sheet bản cuối"))
    if "thumbnails" in inputs:
        seen: set[str] = set()
        for a in [a for a in assets if a["kind"] == "thumbnail"]:
            if a["path"] in seen or len(seen) >= 3: continue
            seen.add(a["path"])
            loaded = _load(a["path"], notes)
            if loaded:
                label = f"thumbnail {a['variant_id']}" if a.get("variant_id") else "thumbnail"
                out.append(Attachment("image/jpeg", _single(loaded[0], THUMB_MAX), loaded[1], label))
    if "scene_images" in inputs:
        by_scene: dict[str, str] = {}
        for a in assets:
            if a["kind"] == "scene_image" and a.get("scene_id"):
                by_scene[str(a["scene_id"])] = a["path"]
        tiles: list[tuple[str, Image.Image, str]] = []
        for scene_id, path in by_scene.items():
            loaded = _load(path, notes)
            if loaded: tiles.append((scene_id, loaded[0], loaded[1]))
        per = COLS * ROWS
        for k in range(0, len(tiles), per):
            chunk = tiles[k:k + per]
            digest = hashlib.sha256("".join(f"{sid}:{h}" for sid, _, h in chunk).encode()).hexdigest()
            label = f"lưới ảnh cảnh {chunk[0][0]}–{chunk[-1][0]}" if len(chunk) > 1 else f"ảnh cảnh {chunk[0][0]}"
            out.append(Attachment("image/jpeg", _grid([(sid, img) for sid, img, _ in chunk]), digest, label))
    if len(out) > MAX_ATTACHMENTS:
        notes.append(f"bỏ {len(out) - MAX_ATTACHMENTS} ảnh vượt trần {MAX_ATTACHMENTS} ảnh/lượt")
        out = out[:MAX_ATTACHMENTS]
    return out, notes


def attachment_note(attachments: list[Attachment]) -> str:
    """Đoạn nối vào lượt user để model biết ảnh nào là gì (thứ tự khớp thứ tự gửi)."""
    if not attachments:
        return ""
    lines = [f"{i}. {a.label}" for i, a in enumerate(attachments, 1)]
    return "\n\n# Ảnh đính kèm (theo thứ tự gửi; nhãn góc trên trái lưới là scene_id)\n" + "\n".join(lines)
