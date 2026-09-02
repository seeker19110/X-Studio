# Kiến trúc — Studio-creators (phòng ban sáng tạo video)

Phòng ban thứ hai trong hub X-Agents, kế thừa nguyên tắc của `software-company` và bổ sung những gì một
kênh video cần: lớp media (giọng đọc, ảnh, ghép video) do CODE gọi, scene manifest bền vững để sửa từng cảnh,
preflight khả năng được tìm thấy, retention map vào cảnh, và nguyên tắc **approval-first** — không có gì được
lên lịch/đăng/trả lời công khai trước khi qua human gate.

## Nguyên tắc

1. **Event-driven, không gọi trực tiếp**: agent chỉ nói chuyện qua topic có JSON Schema (`topics/schemas/`); bus từ chối
   message sai schema.
2. **Key = video_id** (cấp kênh: channel_id): mọi message của một video đi cùng partition, giữ thứ tự.
3. **Blackboard có chủ**: `shared-context` chia namespace, mỗi namespace một agent ghi (`topics/README.md`).
4. **Model quyết định, code hành động** (ADR-0003): TTS, sinh ảnh, ghép video, preflight, kiểm định A/B, map retention,
   đăng — đều là code xác định. Model không có tool-use, chỉ trả JSON có cấu trúc.
5. **Approval-first** (ADR-0002): ba review bắt buộc (factual, rights, quality) + preflight → gate `publish`; kế hoạch
   biên tập → gate `plan`; trả lời bình luận → gate `replies`; kẹt → gate `escalation`. Gate không bao giờ tự đi tiếp.
6. **Sửa, không dựng lại** (ADR-0004): scene manifest có version; editor sửa từng cảnh, khoá cảnh đạt; renderer chỉ sinh
   lại phần bị chạm; tối đa 3 vòng.
7. **Số liệu thật, không bịa**: `performance-snapshots`/`audience-comments` do người hoặc adapter nạp; agent phân tích
   chỉ diễn giải số code đã tính.
8. **Hạn mức mọi nơi**: retry, timeout, token, vòng sửa đều có ngưỡng; hết ngưỡng thì escalate.
9. **Prompt là code**: agent/skill có version, golden test, eval ghi/phát lại; đổi prompt phải chạy eval.
10. **Trung lập provider**: text (`llm.yaml`, `STUDIO_*`) và media (`media.yaml`, `STUDIO_MEDIA_*`) đổi bằng cấu hình.
11. **Self-hosted, resume được**: bus SQLite là checkpoint; mở lại là chạy tiếp đúng chỗ.

## Các khối và agent (7 khối, 14 agent + human gate)

| # | Khối | Agent | Vai trò |
|---|------|-------|---------|
| 1 | Chiến lược | channel-strategist, trend-researcher | Kế hoạch biên tập từ brief kênh + xu hướng + insight; hồ sơ nghiên cứu từng video |
| 2 | Sáng tác | script-writer | Kịch bản: hook, cấu trúc giữ chân, CTA, sổ claim có nguồn |
| 3 | Sản xuất | production-manager, editor, thumbnail-designer | Scene manifest; sửa cảnh/chốt dựng; đặc tả thumbnail A/B |
| 4 | Phân phối | seo-optimizer, publisher, community-manager | Metadata + preflight; lên lịch/đăng sau gate; nháp trả lời bình luận |
| 5 | Chất lượng | fact-checker, rights-checker, quality-reviewer | Ba cổng review độc lập: factual, bản quyền/provenance, chất lượng |
| 6 | Phân tích | analytics-analyst | Insight từ số liệu thật; retention theo cảnh; thí nghiệm A/B |
| 7 | Giám sát | supervisor | Watchdog, ngân sách token, bài học, version prompt |
| — | Human gate | (con người) | plan · publish · replies · escalation |

Thành phần code (không phải agent): **renderer** (media), **desk** (vòng đời video, gom review), **preflight**,
**analytics** (retention/A-B), **orchestrator**.

## Topic

| Topic | Producer | Consumer | Key |
|-------|----------|----------|-----|
| channel-briefs | human | trend-researcher | channel_id |
| trend-reports | trend-researcher | channel-strategist (lập kế hoạch → gate plan) | channel_id |
| video-briefs | channel-strategist (qua desk sau gate plan), desk (làm lại) | trend-researcher (retry=0), script-writer (retry>0) | video_id |
| research-dossiers | trend-researcher | script-writer | video_id |
| scripts | script-writer | fact-checker | video_id |
| review-results | fact-checker, rights-checker, quality-reviewer | production-manager + seo-optimizer (fact pass), desk | video_id |
| scene-manifests | production-manager, renderer (version mới sau sửa) | renderer, thumbnail-designer | video_id |
| media-assets | renderer | editor (draft), quality-reviewer + rights-checker (final), desk | video_id |
| cut-lists | editor | renderer (sửa / chốt) | video_id |
| thumbnail-specs | thumbnail-designer | renderer | video_id |
| metadata-packages | seo-optimizer | preflight (code), seo-optimizer (sửa block), publisher (sau gate) | video_id |
| publish-events | publisher, human (published/rolled_back) | desk | video_id |
| performance-snapshots | human / adapter | analytics-analyst (kèm retention_drops, experiment do code tính) | video_id |
| analytics-reports | analytics-analyst | channel-strategist (insight → `strategy`; cấp kênh → kế hoạch mới) | video_id / channel_id |
| audience-comments | human / adapter | community-manager | video_id |
| reply-drafts | community-manager | gate replies → publisher | video_id |
| shared-context | theo namespace | tất cả | namespace |
| audit-log | tất cả | supervisor, orchestrator (gate.decide) | actor |
| supervisor-actions | supervisor | orchestrator | target |

## Vòng đời một video

```
channel-strategist: video-briefs (nhiều một lượt) → code kiểm estimate/budget → gate plan → desk dispatch
trend-researcher:   research-dossiers (nguồn, bằng chứng, đối thủ, khoảng trống)
script-writer:      scripts (hook, sections, claims có nguồn)
fact-checker:       review-results(source=fact) — block → brief retry+1 kèm hint → script-writer làm lại
production-manager: scene-manifests v1 ──► renderer: TTS + ảnh từng cảnh + draft_video
seo-optimizer:      metadata-packages ──► preflight (code): block → seo-optimizer sửa một lần
thumbnail-designer: thumbnail-specs ──► renderer: thumbnail A/B
editor:             cut-lists: repair (renderer sinh lại đúng cảnh → manifest v2 → draft mới, ≤ 3 vòng) | approve → final_video
quality-reviewer:   review-results(source=quality)      ┐ trên gói hoàn chỉnh
rights-checker:     review-results(source=rights)        ┘ (song song, độc lập)
desk:               fact + rights + quality pass, có final + metadata + thumbnail → gate publish (checklist = review + preflight)
                    fail/block → brief retry+1 với hint; retry > 3 → blocked → gate escalation
publisher:          gate approve → publish-events(scheduled) → (nền tảng) published
analytics-analyst:  performance-snapshots → analytics-reports (điểm rơi theo cảnh, A/B) → channel-strategist ghi `strategy`
community-manager:  audience-comments → reply-drafts (lô) → gate replies → publisher đăng (bỏ requires_human)
supervisor:         retry, token, lỗi lặp, im lặng quá timeout → supervisor-actions(warn/pause/budget_cut/escalate)
```

## Trạng thái video

`briefed → researched → scripted → in_production → in_review → approved → scheduled → published → analyzed → closed`
cộng `changes_requested` (làm lại), `blocked`, `escalated` vào được từ bất kỳ trạng thái nào. Code: `events.TRANSITIONS`.

## Human gate

| Gate | Subject | Khi nào | Approve | Reject / request_changes |
|------|---------|---------|---------|---------------------------|
| plan | PLAN-\<channel\>-\<n\> | channel-strategist sinh kế hoạch, code kiểm xong | desk dispatch từng brief theo priority | kế hoạch bỏ |
| publish | PUB-\<video\> | 3 review pass + final + metadata + thumbnail | publisher lên lịch | request_changes → làm lại với hint; reject → đóng |
| replies | REP-\<video\>-\<n\> | community-manager có lô nháp | publisher đăng các reply không `requires_human` | không đăng |
| escalation | ESC-\<video\> | blocked / supervisor escalate | mở lại với hint, retry về 0 | đóng video |

Timeout 24h, nhắc ở 12h, four-eyes (người duyệt ≠ người tạo). Checklist trong `gates/checklists.md`.

## Lớp media (ADR-0003)

`media.py`: `TTS.synthesize`, `ImageGen.generate`, `VideoAssembler.assemble`; provider `fake` (offline, file giữ chỗ hợp lệ),
`openai` (endpoint OpenAI-compatible cho TTS/ảnh), `ffmpeg` (ghép MP4). `renderer.py` biến manifest thành asset có
checksum + provenance (provider:model, prompt_ref, license) và publish `media-assets`. Asset nằm trong `output/<video_id>/`.

## Orchestrator

`studio.orchestrator` nối bảng topic ở trên: mỗi event → tra `ROUTES` (khớp front matter, kiểm lúc khởi tạo) → gọi runner
→ publish. Bước code chạy trước route model trên cùng event (render, apply cut-list, thumbnails, preflight, rework). Kế
hoạch: `PLAN_INPUTS` → `generate(many=True)` → `desk.check_plan` → audit `plan.proposed` → gate. Gate decision đến qua
`audit-log` (`gate.decide`) kể cả từ tiến trình khác (`gate_cli`, qua `SQLiteBus.poll`). Video bị supervisor pause thì
event hoãn đến `resume`. Mỗi event xử lý xong ghi `orchestrated`; mở lại SQLite là replay dựng lại desk/supervisor/gate/plan.
