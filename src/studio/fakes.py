"""Client giả có kịch bản: sinh payload hợp lệ cho từng agent theo tên agent trong system prompt (`# <id>`).
Dùng cho demo và test end-to-end; không có model thật nào được gọi. Đầu vào đọc từ user message (JSON trong code
fence đầu tiên) để giữ đúng video_id / channel_id."""
from __future__ import annotations

import json
import re
from typing import Any

from .llm import FakeClient

_ID = re.compile(r"^# ([\w-]+)\n", re.MULTILINE)
_JSON = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


def _inputs(user: str) -> tuple[dict[str, Any], dict[str, Any]]:
    blocks = _JSON.findall(user)
    inp = json.loads(blocks[0]) if blocks else {}
    extra = json.loads(blocks[1]) if len(blocks) > 1 and "Dữ liệu bổ sung" in user else {}
    return inp, extra


def scripted(agent: str, inp: dict[str, Any], extra: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    vid = inp.get("video_id") or (extra.get("brief") or {}).get("video_id") or "V1"
    cid = inp.get("channel_id") or "CH1"
    if agent == "trend-researcher":
        if "video_id" in inp:
            return {"video_id": vid, "topic": inp.get("working_title", ""), "sources": [
                {"title": "Báo cáo ngành 2026", "url": "https://example.org/report", "type": "report", "accessed": "2026-09-02"}],
                "competitor_videos": [{"title": "Video đối thủ", "url": "https://youtube.com/watch?v=x", "views": 120000}],
                "evidence": ["số liệu A: 42% (nguồn report)"], "gaps": ["chưa ai nói góc nhìn B"]}
        return {"channel_id": cid, "trends": [{"topic": "AI dựng video", "momentum": "rising", "evidence": "search +40% 30 ngày"}],
                "opportunities": ["so sánh công cụ", "hướng dẫn cho người mới"], "sources": ["https://example.org/trends"]}
    if agent == "channel-strategist":
        if "Không publish topic nào" in opts.get("user", ""):
            return {"context_writes": [{"namespace": "strategy", "content_ref": "strategy.md", "summary": "insight cập nhật"}]}
        n = opts.get("plan_size", 1)
        items = [{"video_id": f"{cid}-V{i + 1}", "channel_id": cid, "working_title": f"Video {i + 1}: AI dựng video cho người mới",
                  "pillar": "hướng dẫn", "angle": "so sánh thực tế", "audience": "người mới", "format": "long",
                  "target_minutes": 6, "key_points": ["ý 1", "ý 2"], "boundaries": ["không hứa thu nhập"],
                  "risk_tags": [], "priority": 1 + i, "estimate_tokens": 60000, "budget_tokens": 100000} for i in range(n)]
        return {"items": items, "context_writes": [{"namespace": "strategy", "content_ref": "strategy.md", "summary": "kế hoạch tháng"}]}
    if agent == "script-writer":
        v = int((extra.get("previous_script") or {}).get("version", 0)) + 1
        return {"payload": {"video_id": vid, "version": v, "working_title": "AI dựng video cho người mới", "hook": "Bạn mất 6 giờ cho một video?",
                            "sections": [{"heading": "Vấn đề", "narration": "Hầu hết người mới mất nhiều giờ dựng video.", "visual_notes": "bàn làm việc", "claim_ids": ["C1"]},
                                         {"heading": "Giải pháp", "narration": "Một pipeline tự động rút xuống còn 30 phút.", "visual_notes": "sơ đồ pipeline", "claim_ids": []}],
                            "cta": "Đăng ký để xem phần 2.", "claims": [{"claim_id": "C1", "text": "42% người mới bỏ cuộc sau 3 video", "source": "https://example.org/report", "needs_verification": True}],
                            "word_count": 40, "estimated_minutes": 0.5},
                "context_writes": [{"namespace": "voice", "content_ref": "voice.md", "summary": "giọng thân thiện, câu ngắn"}]}
    if agent == "fact-checker":
        unsourced = any(not c.get("source") for c in inp.get("claims", []))
        verdict = opts.get("fact_verdict") or ("block" if unsourced else "pass")
        return {"video_id": vid, "source": "fact", "verdict": verdict,
                "findings": [] if verdict == "pass" else [{"level": "block", "text": "C1 không có nguồn kiểm chứng được", "location": "C1"}],
                "metrics": {"claims_checked": 1}}
    if agent == "production-manager":
        return {"payload": {"video_id": vid, "version": 1, "script_version": 1, "aspect": "16:9", "voice": {"voice_id": "alloy", "pace": "medium", "language": "vi"},
                            "scenes": [{"scene_id": "S1", "order": 0, "narration": "Hầu hết người mới mất nhiều giờ dựng video.", "visual_prompt": "bàn làm việc bừa bộn, ánh sáng ấm", "duration_s": 5},
                                       {"scene_id": "S2", "order": 1, "narration": "Một pipeline tự động rút xuống còn 30 phút.", "visual_prompt": "sơ đồ pipeline tối giản", "duration_s": 5}]},
                "context_writes": [{"namespace": "production", "content_ref": f"{vid}/manifest.json", "summary": "2 cảnh"}]}
    if agent == "editor":
        rounds = int(extra.get("repair_rounds_used", 0)); want = opts.get("repairs", 0)
        if rounds < want:
            return {"video_id": vid, "manifest_version": int(inp.get("manifest_version", 1)), "decision": "repair",
                    "repairs": [{"scene_id": "S2", "action": "regenerate_image", "reason": "ảnh quá tối", "new_visual_prompt": "sơ đồ pipeline sáng, nền trắng"}], "order": [], "notes": "sửa S2"}
        return {"video_id": vid, "manifest_version": int(inp.get("manifest_version", 1)), "decision": "approve", "repairs": [], "order": [], "notes": "ok"}
    if agent == "thumbnail-designer":
        return {"payload": {"video_id": vid, "variants": [{"variant_id": "A", "prompt": "người ngồi trước máy tính, biểu cảm ngạc nhiên", "overlay_text": "6 GIỜ → 30 PHÚT", "style": "bold"},
                                                          {"variant_id": "B", "prompt": "sơ đồ pipeline nổi bật", "overlay_text": "AI DỰNG VIDEO", "style": "clean"}], "chosen": "A"},
                "context_writes": [{"namespace": "brand", "content_ref": "brand.md", "summary": "chữ to, 3 từ, tương phản cao"}]}
    if agent == "seo-optimizer":
        bad = opts.get("seo_bad_first", False) and not extra.get("preflight_findings")
        title = ("X" * 120) if bad else "AI dựng video cho người mới: 6 giờ xuống 30 phút"
        return {"payload": {"video_id": vid, "title": title, "description": ("AI dựng video cho người mới. " * 12).strip(),
                            "tags": ["ai dựng video", "youtube automation", "người mới"], "primary_keyword": "AI dựng video", "language": "vi", "category": "Education",
                            "chapters": [{"time": "00:00", "label": "Mở đầu"}, {"time": "00:15", "label": "Vấn đề"}, {"time": "00:30", "label": "Giải pháp"}],
                            "alt_titles": ["Dựng video bằng AI trong 30 phút"]},
                "context_writes": [{"namespace": "seo", "content_ref": "keywords.md", "summary": "cụm chính: AI dựng video"}]}
    if agent == "quality-reviewer":
        v = opts.get("quality_verdict", "pass")
        return {"video_id": vid, "source": "quality", "verdict": v, "findings": [] if v == "pass" else [{"level": "block", "text": "hook 12s quá dài", "location": "S1"}], "metrics": {"hook_seconds": 4}}
    if agent == "rights-checker":
        v = opts.get("rights_verdict", "pass")
        return {"payload": {"video_id": vid, "source": "rights", "verdict": v, "findings": [] if v == "pass" else [{"level": "block", "text": "ảnh S2 nguồn unknown", "location": "S2"}], "metrics": {"assets_checked": 5}},
                "context_writes": [{"namespace": "rights", "content_ref": f"{vid}/provenance.json", "summary": "mọi asset generated"}]}
    if agent == "publisher":
        if inp.get("comment_id"):
            return {"video_id": vid, "kind": "reply", "status": "published", "platform_ref": f"reply:{inp['comment_id']}", "evidence": "đã đăng trả lời"}
        return {"video_id": vid, "kind": "video", "status": "scheduled", "scheduled_at": "2026-09-05T12:00:00Z", "platform_ref": "yt:abc123", "evidence": "upload ok, private → scheduled"}
    if agent == "analytics-analyst":
        return {"payload": {"channel_id": cid, "video_id": vid, "insights": [{"text": "rơi mạnh ở S2", "evidence": "retention -12% tại 6s", "action": "rút ngắn phần giải thích"}],
                            "retention_drops": extra.get("retention_drops", []), "experiments": [extra["experiment"]] if extra.get("experiment") else [],
                            "recommendations": ["hook ≤ 5s", "thêm chương"]},
                "context_writes": [{"namespace": "insights", "content_ref": "insights.md", "summary": "S2 rơi 12%"}]}
    if agent == "community-manager":
        items = [{"comment_id": c["comment_id"], "video_id": vid, "reply": f"Cảm ơn bạn! {c.get('text', '')[:40]}", "theme": "hỏi đáp",
                  "sentiment": "question" if "?" in c.get("text", "") else "positive", "requires_human": "giá" in c.get("text", "").lower()}
                 for c in inp.get("comments", [])]
        return {"items": items, "context_writes": [{"namespace": "community", "content_ref": "faq.md", "summary": "2 chủ đề"}]}
    if agent == "supervisor":
        ev = str(inp.get("evidence", "")).lower(); tokens = int(inp.get("tokens", 0) or 0)
        action = "budget_cut" if "budget" in ev or tokens > 150_000 else ("escalate" if "lần 2" in ev or "lặp" in ev else "warn")
        return {"payload": {"target": vid, "action": action, "reason": f"audit {inp.get('action')}: {ev[:60]}", "evidence": ev[:120]},
                "context_writes": [{"namespace": "knowledge", "content_ref": "audit-log:lesson:" + vid, "summary": "{}"}]}
    raise ValueError(f"fake không có kịch bản cho {agent}")


def make_scripted_client(**opts: Any) -> FakeClient:
    def handler(system: str, user: str) -> dict[str, Any]:
        m = _ID.search(system); agent = m.group(1) if m else "?"
        inp, extra = _inputs(user)
        return scripted(agent, inp, extra, {**opts, "user": user})
    return FakeClient(handler=handler)
