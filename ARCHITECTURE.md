# ARCHITECTURE.md — Studio-creators

Bản đồ đầy đủ: [`docs/architecture.md`](docs/architecture.md) (10 nguyên tắc, topic, vòng đời video, gate). Quyết
định: `docs/adr/` 0001–0009. File này là lối vào nhanh.

## Một video đi qua đâu

```
brief kênh ─► trend-researcher ─► channel-strategist (kế hoạch) ─► GATE plan
   ─► research dossier ─► script-writer ─► fact-checker (sổ claim có nguồn)
   ─► production-manager (scene manifest) ─► RENDER: TTS + ảnh + draft (code)
   ─► editor sửa từng cảnh ≤ 3 vòng (cut-list; renderer chỉ sinh lại phần bị chạm)
   ─► rights-checker + quality-reviewer + seo-optimizer (metadata) + PREFLIGHT (code) ─► GATE publish
   ─► publisher lên lịch / đăng (adapter YouTube) ─► sync số liệu thật ─► analytics-analyst (retention theo cảnh, A/B)
   ─► chiến lược vòng sau      |    bình luận ─► community-manager nháp ─► GATE replies ─► đăng
```

## Khác software-company ở đâu

| | software-company | Studio-creators |
|---|---|---|
| Sản phẩm | code trong repo khách | file video + metadata trên YouTube |
| "Code hành động" | worktree, lint/test, merge, smoke | TTS, ảnh, ffmpeg, preflight, upload |
| Bằng chứng thật | `local_checks`, `smoke` | `qc.py` đo file; `platform_ref` từ API; `performance-snapshots` nạp |
| Gate | spec, plan, release, acceptance | plan, publish, replies |
| Hết quota mọi backend | `TransientError` — hoãn event, nhịp sau thử | `LLMError` — dừng, chạy lại `run` sau |
| Tool của model | đọc/ghi file trong worktree, `run` allowlist | web chỉ đọc (2 agent) |

## Lớp code (`src/studio/`)

Hợp đồng: `events.py`, `registry.py`, `topics/schemas/` · Bus: `bus.py`, `sqlite_bus.py`, `blackboard.py` · Điều phối:
`orchestrator.py`, `desk.py`, `gates.py`, `gate_cli.py`, `supervisor.py` · Chạy agent: `runner.py`, `tools.py` · Media:
`media.py`, `renderer.py`, `timeline.py`, `qc.py` · Nền tảng: `platform.py`, `youtube.py`, `preflight.py`,
`analytics.py` · Model: `llm.py`, `routing.py` · Quan sát: `evals.py` · Test: `fakes.py`, `demo.py`.
