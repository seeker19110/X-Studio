# Studio-creators — Multi-Agent phòng ban sáng tạo video

Phòng ban thứ hai trong hub X-Agents: vận hành một kênh video (YouTube) từ đầu tới cuối bằng hệ đa agent
event-driven — 7 khối, 14 agent, human gate ở 4 điểm. Nguyên tắc: **approval-first** (không có gì lên lịch/đăng/trả lời
công khai trước khi qua cổng duyệt: factual, bản quyền, chất lượng), **model quyết định – code hành động** (TTS, ảnh,
ghép video, preflight, kiểm định A/B, đăng đều là code xác định), **sửa từng cảnh không dựng lại**, **số liệu thật quay
lại nuôi chiến lược**, **self-hosted, resume được**, **trung lập provider** cho cả text lẫn media.

```
brief kênh → trend → KẾ HOẠCH (gate plan) → nghiên cứu → kịch bản → fact-check → scene manifest → render (TTS+ảnh+draft)
→ editor sửa cảnh (≤ 3 vòng) → bản cuối → rights + quality review → metadata + preflight → GATE PUBLISH → lên lịch → đăng
→ số liệu thật → retention theo cảnh, A/B → chiến lược    |    bình luận → nháp trả lời → GATE REPLIES → đăng
```

## Các khối

| # | Khối | Agent | Vai trò |
|---|------|-------|---------|
| 1 | Chiến lược | channel-strategist, trend-researcher | Kế hoạch biên tập có ước lượng/ưu tiên/rủi ro; xu hướng và hồ sơ nghiên cứu có nguồn |
| 2 | Sáng tác | script-writer | Hook, cấu trúc giữ chân, CTA, sổ claim có nguồn; làm lại theo hint |
| 3 | Sản xuất | production-manager, editor, thumbnail-designer | Scene manifest; cut-list sửa/khoá/chốt cảnh; thumbnail A/B |
| 4 | Phân phối | seo-optimizer, publisher, community-manager | Metadata + preflight; lên lịch/đăng chỉ sau gate; nháp trả lời bình luận |
| 5 | Chất lượng | fact-checker, rights-checker, quality-reviewer | Ba cổng review độc lập, bắt buộc trước gate publish |
| 6 | Phân tích | analytics-analyst | Insight có số; điểm rơi retention map vào cảnh; A/B ≥ 95% + guard giữ chân |
| 7 | Giám sát | supervisor | Watchdog, ngân sách token, lỗi lặp, bài học, calibration ước lượng |
| — | Human gate | (con người) | `plan` · `publish` · `replies` · `escalation` |

Thành phần code: **renderer** (media), **desk** (vòng đời video, gom review, rework có hint), **preflight**, **analytics**, **orchestrator**.

## Cấu trúc

```
docs/          kiến trúc, tiêu chuẩn, ADR 0001–0006
agents/        14 system prompt có version, nhóm theo khối
skills/        24 skill có version: tiêu chuẩn + quy trình + quy tắc + checklist; nạp đầy đủ / rút gọn
gates/         checklist 4 human gate
templates/     brief, kịch bản, scene manifest, metadata, gói đăng, postmortem, ADR
topics/        19 JSON Schema topic + bảng owner 10 namespace
src/studio/    events, bus, sqlite_bus, blackboard, registry, gates, gate_cli, llm (text), routing (nhiều gói tài khoản,
               chọn theo tier, xoay khi hết quota — ADR-0006), tools (web chỉ đọc — ADR-0007), media (TTS/ảnh/video),
               renderer, preflight, analytics, desk, supervisor, runner, orchestrator, evals, fakes, demo
evals/         ca eval theo agent (YAML) — 14 agent, mỗi agent 2 ca; recordings/ = phản hồi model đã ghi
tests/         pytest: bus, registry, golden 14 agent, preflight/analytics, media/renderer (ffmpeg nếu có),
               desk/gate, runner/eval ghi-phát lại, tool web + vòng lặp tool, orchestrator end-to-end (gate, rework, sửa cảnh, resume SQLite, injection)
```

## Chạy

```bash
cd Studio-creators
uv sync
make test                                  # pytest offline (client giả, media giả; ffmpeg test tự bỏ qua nếu thiếu)
make demo                                  # cả phòng ban với client giả + media giả, dừng ở 2 gate rồi tự duyệt
make lint

# Model thật (provider bất kỳ): cp llm.example.yaml llm.yaml ; cp media.example.yaml media.yaml ; hoặc biến môi trường
#   STUDIO_LLM_PROVIDER=openai STUDIO_LLM_BASE_URL=https://openrouter.ai/api/v1 STUDIO_MODEL_STRONG=... STUDIO_LLM_API_KEY=...
#   STUDIO_LLM_PROVIDER=anthropic STUDIO_MODEL_STRONG=claude-opus-5   (uv sync --extra anthropic)
#   Qua gateway xoay vòng tài khoản Google Antigravity (../gateway, xem README ở đó):
#   STUDIO_LLM_PROVIDER=openai STUDIO_LLM_BASE_URL=http://127.0.0.1:8100/v1 STUDIO_LLM_API_KEY=gateway-local STUDIO_MODEL_STRONG=claude-sonnet-4-6
#   NHIỀU gói cùng lúc (ADR-0006): `backends:` + `routing.prefer` trong llm.yaml — mẫu ở llm.example.yaml; agent có tier
#   strong/standard/light, gói hết quota tự nghỉ. Bảng agent → tier: ../docs/DIEU-PHOI-MODEL.md
#   STUDIO_MEDIA_TTS_PROVIDER=openai STUDIO_MEDIA_IMAGE_PROVIDER=openai STUDIO_MEDIA_VIDEO_PROVIDER=ffmpeg STUDIO_MEDIA_API_KEY=...
#   Tool web cho trend-researcher / fact-checker (ADR-0007): STUDIO_SEARCH_URL=http://localhost:8080/search  (SearXNG JSON;
#   không đặt thì web_search báo "chưa cấu hình", web_fetch vẫn chạy; provider claude-code dùng WebSearch/WebFetch của CLI)
PYTHONPATH=src uv run python -m studio.orchestrator publish channel-briefs brief.json --actor human:owner
PYTHONPATH=src uv run python -m studio.orchestrator run --watch 5      # hoặc: make watch ; một lượt: make run
PYTHONPATH=src uv run python -m studio.gate_cli list                    # hoặc: make gate
PYTHONPATH=src uv run python -m studio.gate_cli approve PLAN-CH1-1 --by human:owner
PYTHONPATH=src uv run python -m studio.gate_cli approve PUB-CH1-V1 --by human:editor --reason "đăng 12:00 thứ 6"
PYTHONPATH=src uv run python -m studio.orchestrator publish publish-events published.json      # nền tảng đã công khai
PYTHONPATH=src uv run python -m studio.orchestrator publish performance-snapshots stats.json   # số liệu thật
PYTHONPATH=src uv run python -m studio.orchestrator publish audience-comments comments.json
PYTHONPATH=src uv run python -m studio.orchestrator status | report
PYTHONPATH=src uv run python -m studio.evals script-writer            # make eval AGENT=... (model thật)
PYTHONPATH=src uv run python -m studio.evals script-writer --record   # make eval-record — sau khi đổi prompt/skill
PYTHONPATH=src uv run python -m studio.evals all --replay             # make eval-replay — như CI, không gọi model
UPDATE_GOLDEN=1 uv run pytest tests/test_golden_agents.py             # make golden — sau khi cố ý sửa agents/ hoặc skills/
```

Asset sinh ra nằm ở `output/<video_id>/` (bị gitignore): `S1.wav`, `S1.png`, `draft_v1.mp4`, `final_v2.mp4`, `thumbnails/A.png`…

## Quy ước bắt buộc
- Brief phải có `estimate_tokens` trước dispatch; `budget_tokens ≥ estimate × 1.5` (code từ chối kế hoạch nếu không).
- Gate `publish` chỉ được xin khi: final video + metadata + thumbnail + review `fact`, `rights`, `quality` đều pass; publisher
  chỉ chạy từ quyết định gate (`approved_by`), không có phê duyệt thì `failed`.
- Review fail/block → brief phát lại với `retry+1` và `hint`; retry > 3 → blocked → gate `escalation`.
- Editor sửa cảnh tối đa 3 vòng; cảnh `locked` không bao giờ sinh lại; asset người tải lên phải có license.
- Mọi asset có `provenance` (provider:model, prompt_ref, license); `unknown` bị rights-checker block.
- `performance-snapshots`, `audience-comments`, `channel-briefs` do người/adapter nạp; agent không bịa số.
- Sửa prompt/skill → tăng `version`, `make golden`, `make eval-record AGENT=<id>` bằng model thật, commit bản ghi; CI phát lại.

## Hiện trạng (2026-09-02)

### Đã có
- Tài liệu: kiến trúc, tiêu chuẩn, ADR 0001–0006; 14 system prompt có version; 24 skill có version; 7 template; checklist 4 gate.
- 19 JSON Schema topic (sinh từ pydantic + 4 schema tay) + bảng owner 10 namespace.
- Lõi xác định `src/studio/`: envelope/payload pydantic, bus validate schema + quyền namespace, bus SQLite (checkpoint/resume),
  registry nạp prompt + skill hai mức, human gate bền vững qua audit-log + CLI four-eyes.
- **LLM trung lập provider** (`llm.py`): `anthropic`, `openai` (mọi server OpenAI-compatible: OpenAI, OpenRouter, Gemini
  OpenAI-compat, Kimi, GLM, Ollama, Groq, vLLM…), `fake`; structured output theo schema topic, prompt cache, token thật.
- **Tool web chỉ đọc có ranh giới** (`tools.py`, ADR-0007): `web_fetch` (http/https công khai, chặn IP riêng kể cả qua
  redirect, ≤ 20k ký tự, bóc HTML) và `web_search` (`STUDIO_SEARCH_URL`, SearXNG JSON) cho agent có `tools: [web]` —
  trend-researcher, fact-checker. Tool-use trung lập provider (`tools`/`messages` trong `ModelClient`, adapter Anthropic,
  OpenAI-compat, fake); provider `claude-code` uỷ quyền vòng tool cho CLI (`--tools WebFetch,WebSearch`). Vòng lặp tool
  trong runner có trần lượt + ngân sách token, audit `tools_used`; eval phát lại bỏ qua lượt tool (CI offline).
- **Media trung lập provider** (`media.py`, ADR-0003): TTS/ảnh qua endpoint OpenAI-compatible, ghép video bằng ffmpeg,
  `fake` offline sinh WAV/PNG/MP4 giả hợp lệ. **Renderer** biến scene manifest thành asset có checksum + provenance.
- **Scene repair** (`renderer.apply_cutlist`, ADR-0004): sinh lại đúng cảnh, khoá cảnh, thay asset, đổi thứ tự, ≤ 3 vòng.
- **Preflight** (`preflight.py`, ADR-0005): giới hạn nền tảng (block) + quy tắc chất lượng (warn), seo-optimizer sửa block một lần,
  finding còn lại vào checklist gate.
- **Analytics bằng code**: điểm rơi retention map vào `scene_id`; A/B thumbnail z-test hai tỷ lệ, tin cậy ≥ 0.95, guard giữ chân.
- **Desk** (`desk.py`): trạng thái video, gom 3 review, `ready_for_publish`, rework có hint, block/reopen, review quá hạn.
- **Orchestrator** (`orchestrator.py`): bảng ROUTES khớp front matter (kiểm lúc khởi tạo); kế hoạch → gate plan → dispatch;
  render/cut-list/thumbnail/preflight bằng code; gate publish/replies/escalation; supervisor pause hoãn event; resume từ SQLite;
  CLI `run | publish | status | report`.
- **Supervisor**: ngân sách token theo video (80%/100%), lỗi review lặp ≥ 2, im lặng quá timeout, calibration estimate/actual theo format.
- **Eval ghi/phát lại** + **client giả có kịch bản** (`fakes.py`) chạy được mọi ca eval và demo end-to-end offline.
- Test: 135 ca pytest (golden 14 agent, bus, registry, preflight/analytics, media/renderer, desk/gate, runner/eval, tool web + vòng tool, orchestrator e2e); ruff sạch.

### Chưa có
- **Adapter nền tảng thật** (YouTube Data API: upload/schedule/playlist, Analytics API, Comments API): publisher hiện ghi ý định
  vào `publish-events`; số liệu và bình luận nạp qua CLI.
- **Allowlist domain theo kênh** và cache trang đã đọc cho tool web; tool web chỉ có ở trend-researcher và fact-checker
  (script-writer, community-manager cố ý không có).
- **Provider media khác** (ElevenLabs, Stability, Runway…): interface có, adapter chưa; chuẩn hoá âm lượng -14 LUFS trong ffmpeg.
- **Shorts repurposing** từ video dài đã duyệt (brief format=short đã hỗ trợ, cắt tự động từ long chưa).
- **Giao diện web** cho gate và xem bản nháp; thông báo khi gate quá hạn.
- Bus Redis/Kafka khi chạy nhiều máy (orchestrator hiện tuần tự một tiến trình).

### Bước tiếp theo
1. Adapter YouTube (upload private → scheduled; kéo analytics/comments theo lịch) đứng sau publisher/desk, giữ nguyên topic.
2. SearXNG tự host cho `STUDIO_SEARCH_URL` + allowlist domain theo kênh; ghi lại eval trend-researcher khi có search.
3. Shorts từ video dài đã duyệt; chuẩn hoá loudness; giao diện web gate.

Đọc `docs/architecture.md` trước, sau đó `docs/standards.md` và `docs/adr/`.
