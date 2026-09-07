"""`CORE` — phòng ban video YouTube, nhìn từ `xagents_core` (ADR gốc 0001 §2).

Một chỗ DUY NHẤT nói "công ty này tên STUDIO, gốc ở đây, bus tên `studio.sqlite`". Mọi module của studio cần một
trong ba thứ đó thì đọc từ đây, không tự dựng lại từ `__file__` của mình.

Tên phân phối của package là `video-creators` còn tên import là `studio`: `prefix` bám theo tên IMPORT vì đó là
tên đã có trong mọi biến môi trường (`STUDIO_LLM_PROVIDER`…) và trong `media.yaml` của người dùng.

Các trường còn lại của `CoreConfig` điền ở K3.4/K3.5 khi guard và bus chuyển sang core.
"""
from __future__ import annotations

from pathlib import Path

from xagents_core.config import CoreConfig

CORE = CoreConfig(
    prefix="STUDIO",
    root=Path(__file__).resolve().parents[2],   # Studio-creators/ : llm.yaml, agents/, skills/, topics/schemas/
    db_name="studio.sqlite",
)
