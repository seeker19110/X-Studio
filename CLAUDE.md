# Studio-creators — luật riêng (bổ sung `../AGENTS.md`, không thay)

Package `studio` (tên phân phối `video-creators`). Phòng ban video YouTube: 14 agent, 24 skill, 19 topic, 4 human
gate (`plan`, `publish`, `replies`, `escalation`). Kế thừa kiến trúc của software-company; khác ở lớp **media** (TTS,
ảnh, ffmpeg) và **nền tảng** (YouTube) — cả hai là code, không phải model.

## Chạy ở đâu

```bash
cd Studio-creators
uv run pytest -q --cov                    # ffmpeg thiếu thì test ghép video tự bỏ qua — không phải lỗi
uv run ruff check src tests && uv run mypy src/studio --ignore-missing-imports
uv run python -m studio.demo              # cả phòng ban offline, dừng ở 2 gate rồi tự duyệt
uv run python -m studio.orchestrator status
uv run python -m studio.youtube login | status | sync-*     # nối YouTube thật (client_secret.json)
```

## TDD ở package này

`../AGENTS.md` luật bắt buộc 4 áp nguyên vẹn: viết test đỏ trong `tests/` trước, chạy `uv run pytest -q --cov -k
<tên test>` thấy đỏ đúng lý do, rồi mới viết code trong `src/studio/` cho nó xanh. Test ghép video tự bỏ qua khi
thiếu `ffmpeg` không tính là "đỏ đúng lý do" — kiểm bằng test khác không phụ thuộc `ffmpeg` nếu máy không có.

## Ba điều không được phá

1. **Approval-first (ADR-0002)**: không có gì lên lịch / đăng / trả lời công khai trước gate `publish` / `replies`.
   Thêm đường đăng mới = thêm gate hoặc đi qua gate có sẵn, không có ngoại lệ "chỉ là thử".
2. **Model quyết định – code hành động (ADR-0003)**: TTS, ảnh, ghép, preflight, A/B, upload là code. Tool duy nhất
   của model là web **chỉ đọc** (ADR-0007) cho `trend-researcher`, `fact-checker`.
3. **Số liệu thật, không bịa**: `platform_ref`/`url` do adapter điền từ API; `performance-snapshots` do `sync-*` nạp;
   thiếu quyền → 0 + evidence, không phải số đẹp.

## Sửa cái gì phải làm gì

| Sửa | Phải |
|---|---|
| `agents/`, `skills/` | 7 bước `../CONTRIBUTING.md` §3 (golden, eval-record với model thật) |
| `topics/schemas/` | model trong `src/studio/events.py` cùng lúc |
| pipeline render / scene manifest | ADR (0004 sửa từng cảnh, 0009 pipeline v2) — sửa cảnh, không dựng lại; ≤ 3 vòng |
| thêm file test / ADR | số ca/file test và ADR mới nhất trong `README.md` (test đếm từ đĩa) |
| media provider mới | `src/studio/media.py` adapter + `media.example.yaml`; test với provider `fake` |

## Đọc thêm

`ARCHITECTURE.md` (trỏ `docs/architecture.md`), `CODEMAP.md`, `TRAPS.md`, `docs/DANH-GIA-NANG-CAP-XUONG-VIDEO.md`
(đánh giá nâng cấp), `docs/adr/` 0001–0009.
