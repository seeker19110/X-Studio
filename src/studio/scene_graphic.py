"""Đặc tả đồ họa một cảnh — DỮ LIỆU, không phải code (ADR-0017).

production-manager điền `Scene.graphic` khi cảnh mang một con số, một danh sách, một câu trích hay một tiêu đề chương;
người vận hành ghi đè bằng `production.graphics` trong media.yaml. Cả hai đi qua đúng model này, rồi `motion.py` xếp
chữ và FFmpeg dựng. Không trường nào được hiểu như code: chữ đi qua file UTF-8 với `expansion=none`, màu là hex, số
phải hữu hạn — và số trên biểu đồ còn phải khớp claim có nguồn (`motion.verify_graphics`).

Module này chỉ phụ thuộc pydantic để `events.py` nhập được mà không kéo theo lớp media.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_LABEL = 80
MAX_BAR_LABEL = 40  # nhãn cột nằm trên một dòng phía trên thanh
Template = Literal["title", "quote", "bars", "list", "stat"]
NUMERIC: frozenset[str] = frozenset({"bars", "stat"})  # template vẽ số: phải có nguồn và qua kiểm claim


class MotionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: Template = "title"
    title: str = Field(min_length=1, max_length=160)
    subtitle: str = Field(default="", max_length=240)
    accent: str = Field(default="55DDB0", pattern=r"^[0-9A-Fa-f]{6}$")
    background: str = Field(default="101F29", pattern=r"^[0-9A-Fa-f]{6}$")
    labels: list[str] = Field(default_factory=list, max_length=6)
    values: list[float] = Field(default_factory=list, max_length=6)
    unit: str = Field(default="", max_length=12)
    source: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def valid_template(self) -> MotionSpec:
        if any(len(label) > MAX_LABEL for label in self.labels):
            raise ValueError(f"each label must be at most {MAX_LABEL} characters")
        if any(not math.isfinite(v) or v < 0 for v in self.values):
            raise ValueError("values must be finite and nonnegative")
        if self.template in NUMERIC and not self.source.strip():
            raise ValueError(f"{self.template} requires a source")
        if self.template == "bars" and (not self.values or len(self.labels) != len(self.values)):
            raise ValueError("bars require matching labels and values")
        if self.template == "bars" and any(len(label) > MAX_BAR_LABEL for label in self.labels):
            raise ValueError(f"bar labels must be at most {MAX_BAR_LABEL} characters")
        if self.template == "stat" and len(self.values) != 1:
            raise ValueError("stat shows exactly one value")
        if self.template == "list" and (not 2 <= len(self.labels) <= 6 or any(not x.strip() for x in self.labels)):
            raise ValueError("list requires 2-6 nonempty items")
        return self
