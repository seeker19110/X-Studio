# AGENTS.md — tóm tắt cho mọi agent lập trình làm việc trong repo này

Chi tiết đầy đủ ở `CLAUDE.md` (bắt buộc đọc). Tệp này là bản rút gọn, theo chuẩn AGENTS.md, để công cụ khác
(Codex, Cursor, Copilot, Gemini CLI…) cũng đọc được cùng một luật.

## Ba điều không được phá

1. **Approval-first (ADR-0002):** không lên lịch / đăng / trả lời công khai trước gate `publish` / `replies`.
   Không có ngoại lệ "chỉ là thử". Policy tự duyệt phải có chữ ký riêng (ADR-0010), không giả human.
2. **Model quyết định, code hành động (ADR-0003):** TTS, ảnh, clip, ghép, QC, upload là code. Tool duy nhất của
   model là web chỉ đọc. Không thêm tool thực thi cho model.
3. **Số liệu thật, không bịa:** `platform_ref`/`url` do adapter điền; thiếu quyền → 0 + evidence.

## Cách làm

- **TDD:** test đỏ trong `tests/` trước (`uv run pytest -q --cov -k <tên>`), thấy đỏ đúng lý do, rồi mới sửa
  `src/studio/`. Coverage 100 % dòng mỗi package. Test ghép video tự bỏ qua khi thiếu `ffmpeg` — không tính là đỏ.
- **Lệnh:** `make sync`, `uv run pytest -q --cov`, `uv run ruff check src tests`,
  `uv run mypy src/studio --ignore-missing-imports`.
- Sửa `topics/schemas/` thì sửa `src/studio/events.py` cùng lúc; sửa `agents/`/`skills/` thì phải có golden + eval
  với model thật — không có thì đừng chạm prompt. Thêm file test / ADR thì cập nhật số đếm trong `README.md`.
- Provider media mới: adapter `urllib` thuần trong `src/studio/media.py` / `video_gen.py`, ví dụ trong
  `media.example.yaml`, test bằng provider `fake` hoặc HTTP giả — không bao giờ gọi API thật trong test.

## Tiền và khóa

- Hỏi trước khi làm việc tiêu tiền (provider trả phí, quota YouTube). Sổ chi phí media có trần
  `production.media_budget_usd` và cảnh báo 80 %; không lách.
- Không bao giờ hỏi hay in API key / token trong chat, commit, log. Khóa đi qua biến môi trường
  (`GEMINI_API_KEY`, `ELEVENLABS_API_KEY`…) hoặc `*.api_key_env` trong media.yaml.
- Không ghi tên model AI đã viết code vào commit, code, PR.

## Đọc thêm

`ARCHITECTURE.md`, `CODEMAP.md`, `TRAPS.md`, `docs/adr/` (0001–0019), `gates/checklists.md`.
