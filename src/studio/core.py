"""`CORE` — phòng ban video YouTube, nhìn từ `xagents_core` (ADR gốc 0001 §2).

Một chỗ DUY NHẤT nói "công ty này tên STUDIO, gốc ở đây, bus tên `studio.sqlite`". Mọi module của studio cần một
trong ba thứ đó thì đọc từ đây, không tự dựng lại từ `__file__` của mình.

Tên phân phối của package là `video-creators` còn tên import là `studio`: `prefix` bám theo tên IMPORT vì đó là
tên đã có trong mọi biến môi trường (`STUDIO_LLM_PROVIDER`…) và trong `media.yaml` của người dùng.

K3.4 điền ba trường của guard (`external_topics`, `derived_topics`, `untrusted_fields`) và `extra_injection_patterns`.
Các trường còn lại điền ở K3.5 khi bus chuyển sang core.
"""
from __future__ import annotations

from pathlib import Path

from xagents_core.config import CoreConfig

CORE = CoreConfig(
    prefix="STUDIO",
    root=Path(__file__).resolve().parents[2],   # Studio-creators/ : llm.yaml, agents/, skills/, topics/schemas/
    db_name="studio.sqlite",
    # Topic mà payload đến từ NGOÀI phòng ban: khán giả (`audience-comments`), người/kênh (`channel-briefs`),
    # số liệu nền tảng (`performance-snapshots`). Lọc thay vì từ chối — đọc bình luận khán giả CHÍNH LÀ việc của
    # `community-manager`, từ chối cả lô vì một bình luận độc là để kẻ viết bình luận tắt được một tính năng.
    external_topics=frozenset({"audience-comments", "channel-briefs", "performance-snapshots"}),
    # Topic nội bộ nhưng nội dung dẫn xuất từ web/nguồn ngoài: `trend-researcher` và `fact-checker` trích thẳng
    # trang lạ vào đây (ADR-0007), nên từ chối là kẹt vĩnh viễn trên cùng một event.
    derived_topics=frozenset({"trend-reports", "research-dossiers", "review-results"}),
    # Trường mang nội dung ngoài dù event nội bộ.
    untrusted_fields=frozenset({"web", "fetched", "sources", "evidence", "text", "description", "summary",
                                "title", "body", "notes", "comment", "quote", "transcript", "output"}),
    # Mẫu RIÊNG của phòng ban — xem `xagents_core/guard.py` quyết định 2. Với một phòng làm video thì đây là câu
    # tấn công; với công ty gia công phần mềm thì là từ vựng nghiệp vụ, nên chúng KHÔNG ở bảng chung.
    extra_injection_patterns=(
        ("developer-mode", r"\bdeveloper\s+mode\b"),
        ("jailbreak", r"\bjailbreak\b"),
    ),
)
