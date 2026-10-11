"""Durable creative direction shared by script, storyboard and visual providers."""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field


class CinemaSpec(BaseModel):
    production_type: Literal["explainer", "film", "animation"] = "explainer"
    visual_mode: Literal["images", "generated_video"] = "images"
    visual_style: str = Field(default="", max_length=1000)
    logline: str = Field(default="", max_length=1500)
    character_bible: str = Field(default="", max_length=3000)
    negative_prompt: str = Field(default="", max_length=1000)
    seed: int = Field(default=0, ge=0, le=2147483647)

    def direction(self) -> list[str]:
        directions = []
        if self.production_type in {"film", "animation"}:
            kind = "phim ngắn điện ảnh" if self.production_type == "film" else "phim hoạt hình"
            directions.append(f"Sản xuất {kind}: câu chuyện có mở đầu, xung đột, cao trào và kết thúc. "
                              "Giữ liên tục bối cảnh và nhân vật. Hư cấu phải thể hiện là hư cấu; "
                              "claim thực tế vẫn phải kiểm chứng. Không tự thêm CTA giáo dục vào kết thúc phim.")
            directions.append("Storyboard: mỗi cảnh mô tả một hành động rõ ràng, cỡ cảnh, góc máy, chuyển động máy, "
                              "ánh sáng và cảm xúc. Lời đọc dùng cho một giọng kể; không hứa lip-sync/đa diễn viên.")
        for label, text in (("Phong cách hình ảnh", self.visual_style), ("Logline", self.logline),
                            ("Hồ sơ nhân vật dùng chung", self.character_bible)):
            if text.strip(): directions.append(f"{label}: {text.strip()}")
        return directions

    def prompt(self, visual_prompt: str, global_style: str) -> str:
        style = self.visual_style.strip() or global_style.strip()
        if not style:
            style = {"film": "Cinematic film, expressive lighting, coherent visual storytelling.",
                     "animation": "2D animated film, expressive characters, coherent visual storytelling."}.get(self.production_type, "")
        bible = f"Consistent character design: {self.character_bible.strip()}." if self.character_bible.strip() else ""
        return " ".join(part for part in (style, bible, visual_prompt.strip()) if part)

    def seed_for(self, scene_id: str, revision: str = "") -> int:
        """Stable shot seeds across repairs/restarts; textual consistency is not identity locking."""
        data = f"{self.seed}:{scene_id}:{revision}".encode()
        return int.from_bytes(hashlib.sha256(data).digest()[:4], "big") % 2147483648
