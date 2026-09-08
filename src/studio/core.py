"""`CORE` — phòng ban video YouTube, nhìn từ `xagents_core` (ADR gốc 0001 §2).

Một chỗ DUY NHẤT nói "công ty này tên STUDIO, gốc ở đây, bus tên `studio.sqlite`". Mọi module của studio cần một
trong ba thứ đó thì đọc từ đây, không tự dựng lại từ `__file__` của mình.

Tên phân phối của package là `video-creators` còn tên import là `studio`: `prefix` bám theo tên IMPORT vì đó là
tên đã có trong mọi biến môi trường (`STUDIO_LLM_PROVIDER`…) và trong `media.yaml` của người dùng.

K3.4 điền ba trường của guard (`external_topics`, `derived_topics`, `untrusted_fields`) và `extra_injection_patterns`.
K3.5b điền `topic_acl`, `payload_models`, `namespace_owners` khi bus chuyển sang core.

**`topic_acl` của studio ĐO ra, không suy ra.** Đây là lần đầu studio có kiểm quyền producer, nên không có bảng
cũ để chuyển sang như company. Nguồn hiển nhiên — front matter `writes` của 14 agent — là nguồn SAI: nó chỉ nói
agent nào ghi topic nào, mà quá nửa event của studio do CODE phát, không do agent. Viết ACL từ `writes` là chặn
`renderer`, `desk`, `orchestrator`, `adapter:youtube` và `chapters` ngay lần chạy đầu.

Cách đo: bọc `InMemoryBus.publish`, chạy toàn bộ suite studio, ghi lại `(actor, topic)` kèm khung ngăn xếp gọi.
Ra **76 cặp**, trong đó **40 cặp ngoài `open_topics`**: 26 cặp có khung trong `src/studio/` (production, thành
bảng dưới đây) và 14 cặp chỉ có khung trong `tests/` (tên actor bịa trong fixture — đã sửa fixture chứ không mở
lối cho chúng; mở lối là chọc thủng đúng lớp vừa thêm vào).
"""
from __future__ import annotations

from pathlib import Path

from xagents_core.config import CoreConfig, TopicACL

from .events import NAMESPACE_OWNERS, PAYLOAD_MODELS

# Năm actor là CODE, không phải agent — lý do bảng này phải đo chứ không đọc từ front matter `writes`:
#   `renderer`     (renderer.py:27)     — phát `media-assets`, và `scene-manifests` khi nắn lại manifest sau render
#   `desk`         (desk.py:24)         — phát `video-briefs` khi mở/làm lại một video
#   `orchestrator` (orchestrator.py:66) — phát `publish-events` khi đăng và khi rollback
#   `adapter:youtube` (youtube.py:39)   — nạp `audience-comments` + `performance-snapshots` từ nền tảng
#   `chapters`     (orchestrator.py:595) — nắn mốc chapter về đầu cảnh THẬT rồi phát lại `metadata-packages`.
#     Đây là ca giống hệt `LEAD_ACTOR` của company: code đóng vòng dưới một actor riêng để phân biệt với bản
#     `seo-optimizer` đoán mốc từ lúc chưa có video. Hai producer, hai vai — không phải một cái thừa.
TOPIC_PRODUCERS: dict[str, frozenset[str]] = {
    "analytics-reports": frozenset({"analytics-analyst"}),
    "audience-comments": frozenset({"adapter:youtube"}),
    "channel-briefs": frozenset(),                    # chỉ người mở kênh (`human_topics`)
    "cut-lists": frozenset({"editor"}),
    "media-assets": frozenset({"renderer"}),
    "metadata-packages": frozenset({"seo-optimizer", "chapters"}),
    "performance-snapshots": frozenset({"adapter:youtube"}),
    "publish-events": frozenset({"publisher", "orchestrator"}),
    "reply-drafts": frozenset({"community-manager"}),
    "research-dossiers": frozenset({"trend-researcher"}),
    "review-results": frozenset({"fact-checker", "quality-reviewer", "rights-checker"}),
    "scene-manifests": frozenset({"production-manager", "renderer"}),
    "scripts": frozenset({"script-writer"}),
    "supervisor-actions": frozenset({"supervisor"}),
    "thumbnail-specs": frozenset({"thumbnail-designer"}),
    "trend-reports": frozenset({"trend-researcher"}),
    "video-briefs": frozenset({"channel-strategist", "desk"}),
}
# Giữ nguyên `HUMAN_TOPICS` mà `orchestrator publish` đã dùng từ trước (`orchestrator.py:69`) — nó ĐÃ là câu trả
# lời cho "người nạp tay được topic nào", và dựng một bảng thứ hai ở đây là hai nguồn cho một sự thật.
HUMAN_TOPICS = frozenset({"channel-briefs", "publish-events", "performance-snapshots", "audience-comments"})
OPEN_TOPICS = frozenset({"audit-log", "shared-context"})  # audit: ai cũng ghi; shared-context: kiểm theo namespace

CORE = CoreConfig(
    prefix="STUDIO",
    root=Path(__file__).resolve().parents[2],   # Studio-creators/ : llm.yaml, agents/, skills/, topics/schemas/
    db_name="studio.sqlite",
    topic_acl=TopicACL(producers=TOPIC_PRODUCERS, human_topics=HUMAN_TOPICS, open_topics=OPEN_TOPICS),
    payload_models=PAYLOAD_MODELS,
    namespace_owners=NAMESPACE_OWNERS,
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
