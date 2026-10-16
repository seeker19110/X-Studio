"""Ảnh cho reviewer (ADR-0020): CODE chọn và thu nhỏ ảnh từ asset thật trên bus, model chỉ nhìn.

`inputs` trong front matter agent nói agent được xem gì: `scene_images` (lưới ảnh nguồn từng cảnh, nhãn scene_id),
`contact_sheet` (một khung giữa mỗi cảnh của BẢN CUỐI, ADR-0016), `thumbnails`. File thiếu/hỏng bị bỏ và ghi lý do,
không làm hỏng lượt chạy. Băm khoá eval là băm FILE NGUỒN.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from studio.attachments import MAX_ATTACHMENTS, attachment_note, collect


def _img(path: Path, size=(640, 360), color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _open(att):
    return Image.open(io.BytesIO(att.data))


def test_scene_images_become_labelled_grids_of_twelve(tmp_path):
    assets = [{"kind": "scene_image", "scene_id": f"S{i}", "path": str(_img(tmp_path / f"S{i}.png", color=(i * 15, 0, 0)))}
              for i in range(1, 15)]
    assets.append({"kind": "scene_audio", "scene_id": "S1", "path": str(tmp_path / "S1.wav")})
    atts, notes = collect(["scene_images"], {"extra": {"scene_assets": assets}})
    assert notes == [] and len(atts) == 2
    assert [a.label for a in atts] == ["lưới ảnh cảnh S1–S12", "lưới ảnh cảnh S13–S14"]
    first = _open(atts[0])
    assert atts[0].media_type == "image/jpeg" and first.size == (1536, 648)
    expected = hashlib.sha256("".join(f"S{i}:" + hashlib.sha256((tmp_path / f"S{i}.png").read_bytes()).hexdigest()
                                      for i in range(1, 13)).encode()).hexdigest()
    assert atts[0].sha256 == expected
    # cùng ảnh nguồn → cùng băm (eval phát lại được); đổi một ảnh → băm khác
    assert collect(["scene_images"], {"extra": {"scene_assets": assets}})[0][0].sha256 == expected
    _img(tmp_path / "S3.png", color=(0, 255, 0))
    assert collect(["scene_images"], {"extra": {"scene_assets": assets}})[0][0].sha256 != expected


def test_contact_sheet_and_thumbnails_are_downscaled_and_capped(tmp_path):
    sheet = _img(tmp_path / "sheet_v1.jpg", size=(3200, 1800))
    old = _img(tmp_path / "sheet_v0.jpg", size=(320, 180))
    thumbs = [{"kind": "thumbnail", "variant_id": v, "path": str(_img(tmp_path / f"t{v}.jpg", size=(1280, 720)))}
              for v in "ABCD"]
    data = {"payload": {"video_id": "V"}, "extra": {"package": {
        "contact_sheet": {"kind": "contact_sheet", "path": str(old)}, "sheets": [{"kind": "contact_sheet", "path": str(sheet)}],
        "thumbnails": thumbs}}}
    atts, notes = collect(["contact_sheet", "thumbnails"], data)
    assert [a.label for a in atts] == ["contact sheet bản cuối", "thumbnail A", "thumbnail B", "thumbnail C"]
    assert max(_open(atts[0]).size) == 1568 and max(_open(atts[1]).size) == 768
    assert atts[0].sha256 == hashlib.sha256(sheet.read_bytes()).hexdigest()   # bản cuối cùng tìm thấy
    assert len(atts) <= MAX_ATTACHMENTS and notes == []


def test_missing_broken_or_foreign_files_are_skipped_with_a_reason(tmp_path):
    broken = tmp_path / "broken.png"; broken.write_bytes(b"not an image")
    text = tmp_path / "notes.txt"; text.write_text("x")
    huge = tmp_path / "huge.png"; huge.write_bytes(b"\x89PNG" + b"0" * 10)
    data = {"extra": {"assets": [
        {"kind": "scene_image", "scene_id": "S1", "path": str(tmp_path / "missing.png")},
        {"kind": "scene_image", "scene_id": "S2", "path": str(broken)},
        {"kind": "scene_image", "scene_id": "S3", "path": str(text)},
        {"kind": "thumbnail", "path": 7},
    ]}}
    atts, notes = collect(["scene_images", "thumbnails"], data)
    assert atts == [] and len(notes) == 3
    assert any("missing.png" in n and "không có file" in n for n in notes)
    assert any("broken.png" in n and "không đọc được" in n for n in notes)
    assert any("notes.txt" in n and "không phải ảnh" in n for n in notes)
    assert collect([], data) == ([], [])
    with pytest.raises(ValueError, match="inputs"):
        collect(["audio"], data)


def test_attachment_note_lists_images_in_order(tmp_path):
    atts, _ = collect(["thumbnails"], {"thumbnails": [{"kind": "thumbnail", "variant_id": "A",
                                                      "path": str(_img(tmp_path / "a.png"))}]})
    note = attachment_note(atts)
    assert note.startswith("\n\n# Ảnh đính kèm") and "1. thumbnail A" in note
    assert attachment_note([]) == ""



def test_oversized_sources_and_too_many_images_are_dropped_with_reasons(tmp_path, monkeypatch):
    import studio.attachments as attachments
    monkeypatch.setattr(attachments, "MAX_SOURCE_BYTES", 10)
    atts, notes = collect(["thumbnails"], {"t": [{"kind": "thumbnail", "path": str(_img(tmp_path / "big.png"))}]})
    assert atts == [] and "quá" in notes[0]
    monkeypatch.setattr(attachments, "MAX_SOURCE_BYTES", 50_000_000)
    monkeypatch.setattr(attachments, "MAX_ATTACHMENTS", 2)
    many = [{"kind": "thumbnail", "variant_id": v, "path": str(_img(tmp_path / f"{v}.png"))} for v in "ABC"]
    atts, notes = collect(["thumbnails"], {"t": many})
    assert len(atts) == 2 and "vượt trần 2" in notes[-1]
