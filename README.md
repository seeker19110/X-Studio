# Studio-creators — Multi-Agent phòng ban sáng tạo video

Phòng ban thứ hai trong hub X-Agents: vận hành một kênh video (YouTube) từ đầu tới cuối bằng hệ đa agent
event-driven — 7 khối, 14 agent, human gate ở 4 điểm. Nguyên tắc: **approval-first** (không có gì lên lịch/đăng/trả lời
công khai trước khi qua cổng duyệt: factual, bản quyền, chất lượng), **model quyết định – code hành động** (TTS, ảnh,
ghép video, preflight, kiểm định A/B, upload/lên lịch/trả lời đều là code xác định), **sửa từng cảnh không dựng lại**, **số liệu thật quay
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

Thành phần code: **renderer** (media), **desk** (vòng đời video, gom review, rework có hint), **preflight**, **analytics**,
**platform** (adapter nền tảng: `fake` | `youtube` — upload, thumbnail, lên lịch, bình luận, số liệu; ADR-0008), **orchestrator**.

## Cấu trúc

```
docs/          kiến trúc, tiêu chuẩn, ADR 0001–0008
agents/        14 system prompt có version, nhóm theo khối; front matter: model_tier, reads/writes, context_namespace_write,
               skills / skills_core, tools, budget_tokens_per_task, max_retries, timeout_minutes
skills/        24 skill có version: tiêu chuẩn + quy trình + quy tắc + checklist; nạp đầy đủ / rút gọn
gates/         checklist 4 human gate
templates/     brief, kịch bản, scene manifest, metadata, gói đăng, postmortem, ADR
topics/        19 JSON Schema topic + bảng owner 10 namespace
src/studio/    events, bus, sqlite_bus, blackboard, registry, gates, gate_cli, llm (text), routing (nhiều gói tài khoản,
               chọn theo tier, xoay khi hết quota — ADR-0006), tools (web chỉ đọc — ADR-0007), media (TTS/ảnh/video),
               renderer, platform (adapter YouTube thật / fake — ADR-0008), youtube (CLI login/status/sync-*),
               preflight, analytics, desk, supervisor, runner, orchestrator, evals, fakes, demo
evals/         ca eval theo agent (YAML) — 14 agent, mỗi agent 2 ca; recordings/ = phản hồi model đã ghi
tests/         pytest 164 ca / 12 file: bus, registry, golden 14 agent, preflight/analytics, media/renderer (ffmpeg nếu có),
               desk/gate, runner/eval ghi-phát lại, tool web + vòng lặp tool, platform (fake, YouTube với HTTP giả, CLI sync),
               routing nhiều backend, provider claude-code, orchestrator end-to-end (gate, rework, sửa cảnh, upload sau gate,
               resume SQLite, injection)
(pyproject: tên dự án `video-creators`, package `studio`, `[tool.uv] package = false`; .gitignore dùng file ở gốc hub)
```

## Chạy

```bash
cd Studio-creators
uv sync
make test                                  # pytest offline (client giả, media giả; ffmpeg test tự bỏ qua nếu thiếu)
make demo                                  # cả phòng ban với client giả + media giả, dừng ở 2 gate rồi tự duyệt
make lint                                  # ruff (make fix = ruff --fix); make status = orchestrator status

# Model thật (provider bất kỳ): cp llm.example.yaml llm.yaml ; cp media.example.yaml media.yaml ; hoặc biến môi trường
#   STUDIO_LLM_PROVIDER=openai STUDIO_LLM_BASE_URL=https://openrouter.ai/api/v1 STUDIO_MODEL_STRONG=... STUDIO_LLM_API_KEY=...
#   STUDIO_LLM_PROVIDER=anthropic STUDIO_MODEL_STRONG=claude-opus-5   (uv sync --extra anthropic)
#   Qua gateway xoay vòng tài khoản Google Antigravity (../gateway, xem README ở đó):
#   STUDIO_LLM_PROVIDER=openai STUDIO_LLM_BASE_URL=http://127.0.0.1:8100/v1 STUDIO_LLM_API_KEY=gateway-local STUDIO_MODEL_STRONG=claude-sonnet-4-6
#   Gói Claude Pro/Max (CLI `claude -p`, không key, không tool-use của công ty): STUDIO_LLM_PROVIDER=claude-code STUDIO_MODEL_STRONG=claude-opus-5
#   Gói ChatGPT Plus/Pro (Codex CLI `codex exec --json`, không key, không tool-use): STUDIO_LLM_PROVIDER=codex STUDIO_MODEL_STRONG=gpt-5.6-terra
#   NHIỀU gói cùng lúc (ADR-0006): `backends:` + `routing.prefer` trong llm.yaml — mẫu ở llm.example.yaml; agent có tier
#   strong/standard/light (5 strong, 5 standard, 4 light), gói hết quota tự nghỉ (routing.cooldown_s / transient_cooldown_s).
#   Mỗi backend: name, provider, models, base_url, api_key | api_key_env, config_dir (CLAUDE_CONFIG_DIR / CODEX_HOME — nhiều tài
#   khoản cùng gói), binary, effort, max_tokens, extra, supports_tools. STUDIO_LLM_BACKENDS=a,b lọc/sắp lại backend.
#   Biến môi trường thắng llm.yaml và bỏ qua `backends:`. Bảng agent → tier: ../docs/DIEU-PHOI-MODEL.md
#   Media (media.yaml: tts.model/voice/base_url, image.model/size, video.fps/resolution, output_dir, platform; mặc định cả ba `fake`):
#   STUDIO_MEDIA_TTS_PROVIDER=openai STUDIO_MEDIA_IMAGE_PROVIDER=openai STUDIO_MEDIA_VIDEO_PROVIDER=ffmpeg STUDIO_MEDIA_API_KEY=...
#   (fallback OPENAI_API_KEY) STUDIO_MEDIA_BASE_URL=... STUDIO_MEDIA_OUTPUT_DIR=output
#   Tool web cho trend-researcher / fact-checker (ADR-0007): STUDIO_SEARCH_URL=http://localhost:8080/search  (SearXNG JSON;
#   không đặt thì web_search báo "chưa cấu hình", web_fetch vẫn chạy; provider claude-code dùng WebSearch/WebFetch của CLI)
PYTHONPATH=src uv run python -m studio.orchestrator publish channel-briefs brief.json --actor human:owner [--key K]
PYTHONPATH=src uv run python -m studio.orchestrator run --watch 5      # hoặc: make watch ; một lượt: make run ; --max-steps N
#   mọi CLI (orchestrator, gate_cli, runner, youtube) nhận --db studio.sqlite (mặc định) trước subcommand
PYTHONPATH=src uv run python -m studio.runner script-writer scripts input.json   # chạy một agent, một topic (không qua orchestrator)
PYTHONPATH=src uv run python -m studio.gate_cli list                    # hoặc: make gate
PYTHONPATH=src uv run python -m studio.gate_cli approve PLAN-CH1-1 --by human:owner
PYTHONPATH=src uv run python -m studio.gate_cli approve PUB-CH1-V1 --by human:editor --reason "đăng 12:00 thứ 6"
#   quyết định: approve | request_changes | reject | hold | rollback; `request KIND SUBJECT --by --checklist` mở gate tay;
#   gate hạn 24h, nhắc 12h (`list` in remind/OVERDUE; `list --full` in nguyên văn checklist thay vì cắt 80 ký tự)
#   four-eyes: --by phải khác người tạo gate; gate ghi `trigger=` người đã kích hoạt bước đó (vd. người duyệt plan) khi biết.
#   Giới hạn ai được duyệt: STUDIO_GATE_APPROVERS=human:owner,human:editor (hoặc media.yaml `gate: {approvers: [...]}`);
#   đặt rồi thì --by ngoài danh sách bị từ chối (mã 3). Checklist gate publish kèm final_video, thumbnail đã chọn, title.
PYTHONPATH=src uv run python -m studio.orchestrator publish publish-events published.json      # nền tảng đã công khai (tay)
PYTHONPATH=src uv run python -m studio.orchestrator publish performance-snapshots stats.json   # số liệu thật (nạp tay)
PYTHONPATH=src uv run python -m studio.orchestrator publish audience-comments comments.json    # bình luận (nạp tay)
#   `publish` chỉ nhận 4 topic do người/adapter nạp ở trên; audit-log (quyết định gate) và topic của agent bị từ chối (mã 2).
#   STUDIO_SYNC_EVERY=300 → `run --watch` tự gọi sync-metrics/sync-comments cho video scheduled/published mỗi 300 s (audit `sync.tick`)

# YouTube THẬT (ADR-0008) — mặc định STUDIO_PLATFORM=fake (offline). Bật: `platform: {provider: youtube}` trong media.yaml
# hoặc STUDIO_PLATFORM=youtube. Đăng nhập là việc của NGƯỜI DÙNG (OAuth Desktop app, loopback 127.0.0.1, mở trình duyệt);
# token lưu ~/.x-agents/auth/youtube_tokens.json (0600, hoặc STUDIO_YOUTUBE_TOKENS), tự refresh; không bao giờ commit.
#   Google Cloud Console → bật YouTube Data API v3 + YouTube Analytics API → OAuth client "Desktop app" → tải client_secret.json
PYTHONPATH=src uv run python -m studio.youtube login --client-secrets client_secret.json [--port 8765] [--no-browser]
#   cờ chung: --tokens PATH (thay STUDIO_YOUTUBE_TOKENS)
PYTHONPATH=src uv run python -m studio.youtube status                                          # có token? hết hạn? scopes? (không in secret)
#   Gate publish approve → publisher quyết định lịch, CODE upload private + thumbnail chosen + publishAt → publish-events có id thật.
#   Gate replies approve → CODE đăng từng reply đã duyệt (comments.insert) → publish-events kind=reply có id thật.
STUDIO_PLATFORM=youtube PYTHONPATH=src uv run python -m studio.youtube sync-comments CH1-V1 [--ref YT_ID] [--since 2026-09-01T00:00:00Z]  # → audience-comments
STUDIO_PLATFORM=youtube PYTHONPATH=src uv run python -m studio.youtube sync-metrics CH1-V1 --window 7 [--variant A] [--ref] [--channel]  # → performance-snapshots
PYTHONPATH=src uv run python -m studio.orchestrator status              # hoặc: make status
PYTHONPATH=src uv run python -m studio.orchestrator report
PYTHONPATH=src uv run python -m studio.evals script-writer            # make eval AGENT=... (model thật)
PYTHONPATH=src uv run python -m studio.evals script-writer --record   # make eval-record — sau khi đổi prompt/skill
PYTHONPATH=src uv run python -m studio.evals all --replay [--strict]  # make eval-replay — như CI, không gọi model; --strict: thiếu bản ghi cũng fail
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
- `performance-snapshots`, `audience-comments`, `channel-briefs` do người/adapter nạp; agent không bịa số. `platform_ref`/`url`
  trong `publish-events` do CODE điền từ kết quả adapter (model không tự khai id); adapter thiếu quyền → số 0 + evidence nói rõ.
- Sửa prompt/skill → tăng `version`, `make golden`, `make eval-record AGENT=<id>` bằng model thật, commit bản ghi; CI phát lại.

## Hiện trạng (2026-09-02)

### Đã có
- Tài liệu: kiến trúc, tiêu chuẩn, ADR 0001–0008; 14 system prompt có version; 24 skill có version; 7 template; checklist 4 gate.
- 19 JSON Schema topic (sinh từ pydantic + 4 schema tay) + bảng owner 10 namespace.
- Lõi xác định `src/studio/`: envelope/payload pydantic, bus validate schema + quyền namespace, bus SQLite (checkpoint/resume),
  registry nạp prompt + skill hai mức, human gate bền vững qua audit-log + CLI four-eyes.
- **LLM trung lập provider** (`llm.py`): `anthropic`, `openai` (mọi server OpenAI-compatible: OpenAI, OpenRouter, Gemini
  OpenAI-compat, Kimi, GLM, Ollama, Groq, vLLM…), `claude-code` (CLI `claude -p`, gói Claude Pro/Max, `config_dir` →
  `CLAUDE_CONFIG_DIR`), `codex` (CLI `codex exec --json`, gói ChatGPT Plus/Pro, `config_dir` → `CODEX_HOME`, `effort` →
  `model_reasoning_effort`), `fake`; structured output theo schema topic, prompt cache, token thật. **Routing nhiều gói**
  (`routing.py`, ADR-0006): `backends:` + `routing.prefer` theo tier, hết quota nghỉ `cooldown_s`, lỗi mạng nghỉ
  `transient_cooldown_s`; mọi backend đều nghỉ thì ném `LLMError` (software-company thì hoãn event).
- **Tool web chỉ đọc có ranh giới** (`tools.py`, ADR-0007): `web_fetch` (http/https công khai, chặn IP riêng kể cả qua
  redirect, ≤ 20k ký tự, bóc HTML) và `web_search` (`STUDIO_SEARCH_URL`, SearXNG JSON) cho agent có `tools: [web]` —
  trend-researcher, fact-checker. Tool-use trung lập provider (`tools`/`messages` trong `ModelClient`, adapter Anthropic,
  OpenAI-compat, fake); provider `claude-code` uỷ quyền vòng tool cho CLI (`--tools WebFetch,WebSearch`). Vòng lặp tool
  trong runner có trần lượt + ngân sách token, audit `tools_used`; eval phát lại bỏ qua lượt tool (CI offline).
- **Media trung lập provider** (`media.py`, ADR-0003): TTS/ảnh qua endpoint OpenAI-compatible, ghép video bằng ffmpeg,
  `fake` offline sinh WAV/PNG/MP4 giả hợp lệ. **Renderer** biến scene manifest thành asset có checksum + provenance.
- **Scene repair** (`renderer.apply_cutlist`, ADR-0004): sinh lại đúng cảnh, khoá cảnh, thay asset, đổi thứ tự, ≤ 3 vòng.
- **Preflight** (`preflight.py`, ADR-0005): giới hạn nền tảng (block: tiêu đề ≤ 100, mô tả ≤ 5000, tag ≤ 500 ký tự, cụm cấm)
  + quy tắc chất lượng (warn: tiêu đề ≤ 70, mô tả ≥ 200, ≥ 3 chapter bắt đầu 00:00 mỗi chapter ≥ 10s), seo-optimizer sửa block
  một lần, finding còn lại vào checklist gate.
- **Analytics bằng code**: điểm rơi retention map vào `scene_id`; A/B thumbnail z-test hai tỷ lệ, tin cậy ≥ 0.95, guard giữ chân.
- **Desk** (`desk.py`): trạng thái video, gom 3 review, `ready_for_publish`, rework có hint, block/reopen, review quá hạn.
- **Orchestrator** (`orchestrator.py`): bảng ROUTES khớp front matter (kiểm lúc khởi tạo); kế hoạch → gate plan → dispatch;
  render/cut-list/thumbnail/preflight bằng code; gate publish/replies/escalation; supervisor pause hoãn event; resume từ SQLite;
  CLI `run | publish | status | report`.
- **Supervisor**: ngân sách token theo video (80%/100%), lỗi review lặp ≥ 2, video im lặng quá 6h, retry > 3 → blocked,
  calibration estimate/actual theo format. Desk: review quá hạn 2h. Kế hoạch mới cũng được kích từ `analytics-reports` cấp kênh.
- **Adapter YouTube thật** (`platform.py`, `youtube.py`, ADR-0008): interface `Platform` trung lập nền tảng (`upload_video`,
  `set_thumbnail`, `schedule`, `list_comments`, `reply`, `snapshot`); `fake` offline (mặc định); `youtube` bằng `urllib` thuần —
  upload resumable 2 bước, `thumbnails/set`, `videos.update` (private + publishAt), `commentThreads.list`, `comments.insert`,
  Analytics `reports.query` (views/phút xem/AVD/like/comment + retention `audienceWatchRatio`; impressions/CTR để 0 kèm evidence
  khi API không cho). OAuth do người dùng chạy (`login`), token tự refresh (kể cả khi 401), quota 403 → `failed` có bằng chứng.
  Orchestrator: gate publish approve → CODE upload + thumbnail chosen + lịch, ghi đè `platform_ref`/`url`/`evidence`; gate replies
  approve → CODE đăng reply; không có approve thì adapter không bị chạm. CLI `sync-comments`/`sync-metrics` nạp số thật lên bus.
- **Eval ghi/phát lại** + **client giả có kịch bản** (`fakes.py`) chạy được mọi ca eval và demo end-to-end offline.
- Test: 164 ca pytest (golden 14 agent, bus, registry, preflight/analytics, media/renderer, desk/gate, runner/eval, tool web + vòng tool,
  platform fake/YouTube HTTP giả/CLI sync, routing nhiều backend, provider claude-code, orchestrator e2e kể cả approval-first
  với adapter); 28/28 ca eval có bản ghi model thật; ruff sạch.

### Chưa có
- **YouTube phần còn lại**: playlist, Shorts flag, đổi lịch/gỡ video (rollback vẫn do người), kéo comments/metrics theo lịch tự động
  (hiện gọi tay/cron `sync-*`), đo quota còn lại; adapter nền tảng khác (TikTok, Facebook) — interface `Platform` đã sẵn.
- **Allowlist domain theo kênh** và cache trang đã đọc cho tool web; tool web chỉ có ở trend-researcher và fact-checker
  (script-writer, community-manager cố ý không có).
- **Provider media khác** (ElevenLabs, Stability, Runway…): interface có, adapter chưa; chuẩn hoá âm lượng -14 LUFS trong ffmpeg.
- **Shorts repurposing** từ video dài đã duyệt (brief format=short đã hỗ trợ, cắt tự động từ long chưa).
- **Giao diện web** cho gate và xem bản nháp; thông báo khi gate quá hạn.
- Bus Redis/Kafka khi chạy nhiều máy (orchestrator hiện tuần tự một tiến trình).

### Bước tiếp theo
1. Lịch tự động cho `sync-comments`/`sync-metrics` (tick của orchestrator hoặc cron) + playlist/Shorts trên YouTube.
2. SearXNG tự host cho `STUDIO_SEARCH_URL` + allowlist domain theo kênh; ghi lại eval trend-researcher khi có search.
3. Shorts từ video dài đã duyệt; chuẩn hoá loudness; giao diện web gate.

Đọc `docs/architecture.md` trước, sau đó `docs/standards.md` và `docs/adr/`.
