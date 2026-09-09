# CODEMAP.md — Studio-creators: muốn đổi X thì sửa ở đâu

## Tài sản prompt (đổi là đi 7 bước `../CONTRIBUTING.md` §3)

| Muốn | Sửa |
|---|---|
| Hành vi một agent | `agents/<khối>/<id>.md` — `strategy` (channel-strategist, trend-researcher), `creative` (script-writer), `production` (production-manager, editor, thumbnail-designer), `distribution` (seo-optimizer, publisher, community-manager), `quality` (fact-checker, rights-checker, quality-reviewer), `analytics` (analytics-analyst), `supervisor` |
| Quy tắc chuyên môn | `skills/*.md` (24): scriptwriting, retention-storytelling, scene-production, video-editing, narration-tts, visual-direction, thumbnail-design, youtube-seo, discoverability-preflight, publishing-ops, community-engagement, fact-checking, source-evaluation, media-rights, content-policy, quality-review, youtube-analytics, growth-experiments, channel-strategy, trend-research, cost-estimation, finops, ai-governance, prompt-engineering |
| Mẫu tài liệu | `templates/` (brief, kịch bản, scene manifest, metadata, gói đăng, postmortem, ADR) |
| Ca eval | `evals/<id>.yaml`, bản ghi `evals/recordings/` |

## Luồng và hợp đồng

| Muốn | Sửa |
|---|---|
| Agent nào nhận topic nào | `ROUTES` trong `src/studio/orchestrator.py`; front matter `reads`/`writes` |
| Khung event chung (`Envelope`, `AuditLog`, `SharedContext`, `SupervisorAction`, `can_transition`) | `xagents-core/src/xagents_core/events.py` — **LỚP CƠ SỞ** từ K3.5a; `src/studio/events.py` kế thừa và thêm trường phạm vi của mình, thu hẹp `topic`/`namespace` về Literal. Model miền, `PAYLOAD_MODELS`, `TRANSITIONS` ở LẠI package | `xagents-core/tests/test_events.py`, `tests/test_events_core.py` |
| Nạp agent/skill từ đĩa | `xagents-core/src/xagents_core/registry.py` từ K3.6a — `src/studio/registry.py` chỉ còn `AgentSpec` (thêm trường `tools`, ADR-0007) và `check_owners=False` mặc định (ba skill chưa có agent chủ quản: `content-policy`, `cost-estimation`, `finops`) | `xagents-core/tests/test_registry.py`, `tests/test_registry.py` |
| Blackboard (`shared-context`) | `xagents-core/src/xagents_core/blackboard.py` từ K3.6b — `src/studio/blackboard.py` chỉ còn lớp con; studio nhận thêm khoá, `rehydrate()`, `content` toàn văn | `xagents-core/tests/test_blackboard.py`, `tests/test_blackboard_core.py` |
| Ai được publish topic nào | bảng `TOPIC_PRODUCERS`/`HUMAN_TOPICS`/`OPEN_TOPICS` ở `src/studio/core.py` — **đo từ event thật, không đọc từ front matter `writes`** (quá nửa event studio do CODE phát); cơ chế ở `xagents-core/src/xagents_core/bus.py` `_check_publish` từ K3.5b | `tests/test_bus_acl.py`, `xagents-core/tests/test_bus.py` |
| Cơ chế bus (validate, ACL, `latest`, `_notify_safely`, khoá) | `xagents-core/src/xagents_core/bus.py` — `src/studio/bus.py` chỉ còn lớp con mỏng khai `envelope_cls` + `CORE` | `xagents-core/tests/test_bus.py`, `tests/test_bus.py` |
| Bus bền vững trên đĩa (ghi, `poll` giữa tiến trình, `replay`, `latest`, `Lease`) | `xagents-core/src/xagents_core/sqlite_bus.py` từ K3.5c — `src/studio/sqlite_bus.py` chỉ còn lớp con mỏng; tên file mặc định ở `CORE.db_name` | `xagents-core/tests/test_sqlite_bus.py`, `tests/test_sqlite_bus_core.py` |
| Trường của topic | `topics/schemas/<topic>.json` **và** `src/studio/events.py` — 19 topic: channel-briefs, trend-reports, research-dossiers, video-briefs, scripts, scene-manifests, media-assets, cut-lists, thumbnail-specs, metadata-packages, review-results, publish-events, reply-drafts, audience-comments, performance-snapshots, analytics-reports, shared-context, audit-log, supervisor-actions |
| Namespace blackboard (10) | `topics/README.md` + owner trong `events.py` |
| Checklist 4 gate | `gates/checklists.md` |
| Vòng đời video, gom review, rework có hint | `src/studio/desk.py` |
| Gate: hạn, four-eyes | `src/studio/gates.py`, `gate_cli.py` |

## Media và nền tảng (code, không phải model)

| Muốn | Sửa |
|---|---|
| Provider TTS / ảnh / video | `src/studio/media.py` (adapter; `fake` cho test); cấu hình `media.yaml` / `STUDIO_MEDIA_*` |
| Pipeline render, sửa từng cảnh | `src/studio/renderer.py` (ADR-0004, ADR-0009) |
| Mốc cảnh → SRT, chapter | `src/studio/timeline.py` |
| Đo file thật (thời lượng, độ phân giải, âm) | `src/studio/qc.py` (ffprobe/ffmpeg) |
| Cách chạy lệnh con (ffmpeg, TTS cục bộ): env lọc khoá, timeout, container | `src/studio/sandbox.py` + `media.yaml` khoá `render` (bản tạm, vào lõi chung ở K3.2) |
| Preflight khả năng được tìm thấy | `src/studio/preflight.py` (ADR-0005) |
| Retention map cảnh, A/B ≥ 95% | `src/studio/analytics.py` |
| Upload, thumbnail, phụ đề, lên lịch, bình luận, số liệu | `src/studio/platform.py` (adapter `fake` \| `youtube`, ADR-0008) |
| CLI YouTube login / status / sync | `src/studio/youtube.py` |
| Tool web chỉ đọc | `src/studio/tools.py` (ADR-0007) |

## Model và chi phí

| Muốn | Sửa |
|---|---|
| Adapter provider, retry | `src/studio/llm.py` |
| Chọn backend theo tier, xoay quota | `xagents-core/src/xagents_core/routing.py` (ADR-0006; K3.3d — `src/studio/routing.py` chỉ còn là shim); `llm.yaml` |
| Ngân sách, watchdog, calibration ước lượng | `src/studio/supervisor.py` |
| Vòng lặp tool | `src/studio/runner.py` |
| Chống prompt injection | Bảng mẫu + lọc: `xagents-core/src/xagents_core/guard.py` (K3.4). Mẫu RIÊNG của phòng ban (`developer mode`, `jailbreak`) và chính sách topic/trường: `src/studio/core.py`; `src/studio/guard.py` là shim gắn `CORE`. Luật bỏ TỪNG bình luận của lô `audience-comments` ở lại `runner._filter_comments` | `tests/test_guard_studio_duoc_nang.py`, `xagents-core/tests/test_guard.py` |
| Eval ghi / phát lại | `src/studio/evals.py` |
| Client giả, media giả | `src/studio/fakes.py` |

## Tài liệu sửa kèm

| Khi | Sửa |
|---|---|
| Thêm test / ADR | `README.md` (số ca/file test; `ADR 0001–00xx`) |
| Đổi kiến trúc | `docs/adr/00xx-*.md` |
| Bẫy mới | `TRAPS.md` |
