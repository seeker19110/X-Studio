from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

Topic = Literal[
    "channel-briefs", "trend-reports", "video-briefs", "research-dossiers", "scripts",
    "scene-manifests", "media-assets", "cut-lists", "thumbnail-specs", "metadata-packages",
    "review-results", "publish-events", "performance-snapshots", "analytics-reports",
    "audience-comments", "reply-drafts",
    "shared-context", "audit-log", "supervisor-actions",
]
Namespace = Literal["strategy", "research", "voice", "production", "brand", "seo", "rights", "insights", "community", "knowledge"]
ReviewSource = Literal["fact", "rights", "quality"]
AssetKind = Literal["scene_audio", "scene_image", "draft_video", "final_video", "thumbnail"]
VideoFormat = Literal["long", "short"]

NAMESPACE_OWNERS: dict[str, set[str]] = {
    "strategy": {"channel-strategist"}, "research": {"trend-researcher"}, "voice": {"script-writer"},
    "production": {"production-manager"}, "brand": {"thumbnail-designer"}, "seo": {"seo-optimizer"},
    "rights": {"rights-checker"}, "insights": {"analytics-analyst"}, "community": {"community-manager"},
    "knowledge": {"supervisor"},
}

# Review bắt buộc trước gate publish (approval-first, ADR-0002): factual, bản quyền/provenance, chất lượng.
REQUIRED_REVIEWS: frozenset[str] = frozenset({"fact", "rights", "quality"})
# Chủ đề chạm các tag này → brief phải khai `risk_tags`; fact-checker và rights-checker siết theo skill (YMYL, nhạc, footage...).
RISK_TAGS = frozenset({"health", "finance", "legal", "minors", "politics", "music", "footage", "brand", "person"})
BUDGET_FACTOR = 1.5  # budget_tokens ≥ estimate_tokens × BUDGET_FACTOR (skill cost-estimation)
MAX_REPAIR_ROUNDS = 3  # editor chỉ được yêu cầu sửa cảnh tối đa 3 vòng (ADR-0004), rồi phải chốt hoặc bị block


class Envelope(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    topic: Topic
    key: str
    actor: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any]


class VideoBrief(BaseModel):
    """Một video trong kế hoạch biên tập (channel-strategist tạo, human gate `plan` duyệt rồi code dispatch)."""
    video_id: str
    channel_id: str
    working_title: str
    pillar: str
    angle: str
    audience: str
    format: VideoFormat = "long"
    target_minutes: float = 8.0
    key_points: list[str] = []
    boundaries: list[str] = []  # điều kênh không làm (nội dung, giọng điệu, nguồn)
    risk_tags: list[str] = []
    priority: int = 3  # 1 cao nhất
    estimate_tokens: int | None = None
    budget_tokens: int = 150_000
    retry: int = 0
    hint: str | None = None


class Claim(BaseModel):
    claim_id: str
    text: str
    source: str | None = None  # URL/tài liệu; None = chưa có nguồn → fact-checker phải xử lý
    needs_verification: bool = True


class ScriptSection(BaseModel):
    heading: str
    narration: str
    visual_notes: str = ""
    claim_ids: list[str] = []


class Script(BaseModel):
    video_id: str
    version: int = 1
    working_title: str
    hook: str
    sections: list[ScriptSection]
    cta: str = ""
    claims: list[Claim] = []
    word_count: int = 0
    estimated_minutes: float = 0.0


class Scene(BaseModel):
    scene_id: str
    order: int
    narration: str
    visual_prompt: str
    duration_s: float = 6.0
    locked: bool = False  # cảnh đã đạt: editor/renderer không được sinh lại
    asset_refs: dict[str, str] = {}  # kind → path (renderer điền)


class SceneManifest(BaseModel):
    """Bản ghi bền vững của một sản xuất (ADR-0004): sửa một cảnh không phải làm lại cả video."""
    video_id: str
    version: int = 1
    script_version: int = 1
    scenes: list[Scene]
    voice: dict[str, Any] = {}  # voice_id, pace, language
    aspect: Literal["16:9", "9:16"] = "16:9"


class Provenance(BaseModel):
    generated_by: str  # provider:model hoặc "human-upload"
    prompt_ref: str | None = None
    license: str = "generated"  # generated | cc-by | licensed | owned | unknown
    source_url: str | None = None


class MediaAsset(BaseModel):
    video_id: str
    kind: AssetKind
    path: str
    scene_id: str | None = None
    manifest_version: int = 1
    provider: str = "fake"
    checksum: str = ""
    duration_s: float | None = None
    provenance: Provenance
    variant_id: str | None = None  # thumbnail A/B


class Repair(BaseModel):
    scene_id: str
    action: Literal["regenerate_audio", "regenerate_image", "regenerate_both", "replace_asset", "lock"]
    reason: str
    new_visual_prompt: str | None = None
    new_narration: str | None = None
    replacement_path: str | None = None


class CutList(BaseModel):
    """Quyết định dựng của editor trên bản nháp: chốt, hoặc danh sách sửa cảnh (giới hạn MAX_REPAIR_ROUNDS)."""
    video_id: str
    manifest_version: int
    decision: Literal["approve", "repair"]
    repairs: list[Repair] = []
    order: list[str] = []  # thứ tự scene_id sau khi dựng (rỗng = giữ nguyên)
    notes: str = ""


class ThumbnailVariant(BaseModel):
    variant_id: str
    prompt: str
    overlay_text: str
    style: str = ""


class ThumbnailSpec(BaseModel):
    video_id: str
    variants: list[ThumbnailVariant]
    chosen: str | None = None


class Chapter(BaseModel):
    time: str  # "00:00"
    label: str


class MetadataPackage(BaseModel):
    video_id: str
    title: str
    description: str
    tags: list[str] = []
    chapters: list[Chapter] = []
    primary_keyword: str = ""
    language: str = "vi"
    category: str = ""
    alt_titles: list[str] = []  # ứng viên A/B


class Finding(BaseModel):
    level: Literal["block", "warn", "nit"]
    text: str
    location: str | None = None  # scene_id / claim_id / trường metadata


class ReviewResult(BaseModel):
    video_id: str
    source: ReviewSource
    verdict: Literal["pass", "block", "fail"]
    findings: list[Finding] = []
    root_cause: str | None = None
    metrics: dict[str, Any] = {}


class PublishEvent(BaseModel):
    video_id: str
    kind: Literal["video", "reply"] = "video"
    status: Literal["scheduled", "published", "failed", "rolled_back"]
    scheduled_at: str | None = None
    url: str | None = None
    platform_ref: str | None = None
    evidence: str = ""


class RetentionPoint(BaseModel):
    t: float
    pct: float


class PerformanceSnapshot(BaseModel):
    """Số liệu thật từ nền tảng (người/adapter nạp vào). Agent không tự bịa số."""
    video_id: str
    channel_id: str
    window_days: int = 7
    views: int = 0
    impressions: int = 0
    ctr: float = 0.0
    avg_view_duration_s: float = 0.0
    retention_curve: list[RetentionPoint] = []
    likes: int = 0
    comments: int = 0
    variant_id: str | None = None  # thumbnail/tiêu đề đang chạy


class Insight(BaseModel):
    text: str
    evidence: str
    action: str = ""


class RetentionDrop(BaseModel):
    scene_id: str | None = None
    t: float
    drop_pct: float


class Experiment(BaseModel):
    experiment_id: str
    kind: Literal["title", "thumbnail"]
    variants: list[str]
    winner: str | None = None
    ctr_lift: float | None = None
    confidence: float | None = None  # phải ≥ 0.95 mới kết luận
    retention_guard_ok: bool | None = None


class AnalyticsReport(BaseModel):
    channel_id: str
    video_id: str | None = None
    insights: list[Insight] = []
    retention_drops: list[RetentionDrop] = []
    experiments: list[Experiment] = []
    recommendations: list[str] = []


class ReplyDraft(BaseModel):
    comment_id: str
    video_id: str
    reply: str
    theme: str = ""
    sentiment: Literal["positive", "neutral", "negative", "question"] = "neutral"
    requires_human: bool = False  # mọi reply đều chờ gate `replies`; True = cần người soạn lại, không dùng bản nháp


class SharedContext(BaseModel):
    namespace: Namespace
    version: int
    content_ref: str
    summary: str = ""


class AuditLog(BaseModel):
    actor: str
    action: str
    video_id: str | None = None
    channel_id: str | None = None
    evidence: str | None = None
    tokens: int = 0


class SupervisorAction(BaseModel):
    target: str
    action: Literal["pause", "resume", "escalate", "budget_cut", "warn"]
    reason: str
    evidence: str | None = None


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "video-briefs": VideoBrief, "scripts": Script, "scene-manifests": SceneManifest, "media-assets": MediaAsset,
    "cut-lists": CutList, "thumbnail-specs": ThumbnailSpec, "metadata-packages": MetadataPackage,
    "review-results": ReviewResult, "publish-events": PublishEvent, "performance-snapshots": PerformanceSnapshot,
    "analytics-reports": AnalyticsReport, "reply-drafts": ReplyDraft,
    "shared-context": SharedContext, "audit-log": AuditLog, "supervisor-actions": SupervisorAction,
}

VideoState = Literal["briefed", "researched", "scripted", "in_production", "in_review", "changes_requested",
                     "approved", "scheduled", "published", "analyzed", "closed", "blocked", "escalated"]
TRANSITIONS: dict[str, set[str]] = {
    "briefed": {"researched", "scripted"}, "researched": {"scripted"}, "scripted": {"in_production", "changes_requested"},
    "in_production": {"in_review", "changes_requested"}, "in_review": {"approved", "changes_requested"},
    "changes_requested": {"scripted", "in_production"},
    "approved": {"scheduled"}, "scheduled": {"published", "changes_requested"}, "published": {"analyzed", "changes_requested"},
    "analyzed": {"closed"}, "blocked": {"scripted", "in_production", "escalated"},
    "escalated": {"scripted", "in_production", "closed"}, "closed": set(),
}


def can_transition(src: str, dst: str) -> bool:
    return dst in {"blocked", "escalated"} or dst in TRANSITIONS.get(src, set())
