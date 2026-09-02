"""Discoverability preflight (ADR-0005): kiểm gói metadata bằng CODE trước gate publish. Phát hiện là tư vấn
(advisory): người duyệt giữ hay bỏ với lý do; chỉ mức `block` (vượt giới hạn nền tảng, từ cấm) mới bắt seo-optimizer
làm lại một lần trước khi đưa lên gate. Không gọi model.

Giới hạn theo nền tảng YouTube (tra cứu 2026-09): tiêu đề ≤ 100 ký tự, mô tả ≤ 5000, tổng tag ≤ 500 ký tự,
chapter đầu phải "00:00" và ≥ 3 chapter mỗi cái ≥ 10s. Quy tắc chất lượng: từ khoá chính trong tiêu đề, tiêu đề
không viết hoa quá nửa, mô tả ≥ 200 ký tự, không nhồi từ khoá, không lời hứa tuyệt đối (YMYL).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .events import Finding, MetadataPackage

TITLE_MAX, TITLE_SAFE, DESC_MAX, DESC_MIN, TAGS_MAX_CHARS, MIN_CHAPTERS = 100, 70, 5000, 200, 500, 3
BANNED_PHRASES = ("chữa khỏi 100%", "cam kết lợi nhuận", "100% lợi nhuận", "guaranteed cure", "guaranteed profit",
                  "không rủi ro", "risk-free")
_CHAPTER_TIME = re.compile(r"^(\d{1,2}:)?\d{1,2}:\d{2}$")


@dataclass
class PreflightReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.level == "block" for f in self.findings)

    def checklist(self) -> list[str]:
        return [f"preflight:{f.level}:{f.location or '-'}:{f.text}" for f in self.findings]


def _seconds(t: str) -> int:
    parts = [int(x) for x in t.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def preflight(meta: MetadataPackage, format: str = "long") -> PreflightReport:
    f: list[Finding] = []
    title, desc = meta.title.strip(), meta.description.strip()
    if len(title) > TITLE_MAX: f.append(Finding(level="block", text=f"tiêu đề {len(title)} ký tự > {TITLE_MAX}", location="title"))
    elif len(title) > TITLE_SAFE: f.append(Finding(level="warn", text=f"tiêu đề {len(title)} ký tự, bị cắt trong kết quả tìm kiếm (> {TITLE_SAFE})", location="title"))
    letters = [c for c in title if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.5:
        f.append(Finding(level="warn", text="tiêu đề viết hoa quá nửa (clickbait, CTR ảo)", location="title"))
    if meta.primary_keyword and meta.primary_keyword.lower() not in title.lower():
        f.append(Finding(level="warn", text=f"từ khoá chính `{meta.primary_keyword}` không có trong tiêu đề", location="title"))
    if len(desc) > DESC_MAX: f.append(Finding(level="block", text=f"mô tả {len(desc)} ký tự > {DESC_MAX}", location="description"))
    elif len(desc) < DESC_MIN: f.append(Finding(level="warn", text=f"mô tả {len(desc)} ký tự < {DESC_MIN}: thiếu ngữ cảnh cho tìm kiếm/AI overview", location="description"))
    if meta.primary_keyword and meta.primary_keyword.lower() not in desc[:200].lower():
        f.append(Finding(level="warn", text="từ khoá chính không xuất hiện trong 200 ký tự đầu mô tả", location="description"))
    total_tags = sum(len(t) for t in meta.tags)
    if total_tags > TAGS_MAX_CHARS: f.append(Finding(level="block", text=f"tổng tag {total_tags} ký tự > {TAGS_MAX_CHARS}", location="tags"))
    if not meta.tags: f.append(Finding(level="warn", text="không có tag", location="tags"))
    low = desc.lower()
    for t in meta.tags:
        if len(t) > 3 and low.count(t.lower()) > 3:
            f.append(Finding(level="warn", text=f"tag `{t}` lặp {low.count(t.lower())} lần trong mô tả (nhồi từ khoá)", location="description"))
    for p in BANNED_PHRASES:
        if p in low or p in title.lower():
            f.append(Finding(level="block", text=f"cụm bị cấm `{p}` (lời hứa tuyệt đối/YMYL)", location="description"))
    if format == "long":
        if not meta.chapters: f.append(Finding(level="warn", text="video dài không có chapter", location="chapters"))
        else:
            if meta.chapters[0].time != "00:00": f.append(Finding(level="block", text="chapter đầu phải bắt đầu 00:00", location="chapters"))
            if len(meta.chapters) < MIN_CHAPTERS: f.append(Finding(level="warn", text=f"< {MIN_CHAPTERS} chapter: YouTube không hiện", location="chapters"))
            prev = -1
            for c in meta.chapters:
                if not _CHAPTER_TIME.match(c.time): f.append(Finding(level="block", text=f"mốc chapter sai định dạng: {c.time}", location="chapters")); break
                s = _seconds(c.time)
                if prev >= 0 and s - prev < 10: f.append(Finding(level="warn", text=f"chapter {c.time} cách chapter trước < 10s", location="chapters"))
                if s <= prev: f.append(Finding(level="block", text=f"chapter {c.time} không tăng dần", location="chapters"))
                prev = s
    return PreflightReport(findings=f)
