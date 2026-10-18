# ADR-0016 — Học từ kho `stored`: soát slideshow, contact sheet, sổ chi phí media

Ngày: 2026-10-12. Trạng thái: accepted.

## Bối cảnh

Kho tham khảo của chủ xưởng, [`seeker19110/stored`](https://github.com/seeker19110/stored) (khảo sát 2026-10-12), có ba
mục chạm trực tiếp xưởng video: mục 19 (OpenMontage — "skills-as-orchestrator" cho sản xuất video), mục 22 (16 repo
"video as code": HyperFrames, Remotion, claude-motion, animate…) và mục 23 §3.2 (audit chính X-Studio). Đối chiếu theo
quy trình adopt-from-outside của kho (ba cột, "grep cổng đang chạy, đừng đọc văn xuôi") cho kết quả:

| Bài học trong kho | X-Studio đã có, sâu hơn | Đã có, nông hơn | Chưa có |
|---|---|---|---|
| Gate theo stage, agent phải dừng ở `awaiting_human` (OpenMontage) | 4 human gate bền trên SQLite, four-eyes, policy ký riêng (ADR-0002/0010) | | |
| Artifact chuẩn hóa + schema mỗi stage | 19 topic có JSON Schema + model pydantic | | |
| Decision log append-only | `audit-log` trên bus, dựng lại trạng thái khi khởi động (`_rehydrate`) | | |
| Reviewer có giới hạn vòng | sửa cảnh ≤ 3 vòng (ADR-0004), retry có hint | | |
| −14 LUFS / −1 dBTP, đo bằng script | `loudnorm` khi ghép + `qc.py` đo ebur128/true peak | | |
| Cache tránh gọi lại provider | cache SHA-256 có checksum, commit nguyên tử (ADR-0009/0011) | | |
| "Slideshow-risk check", cấm still-image fallback khi cần chuyển động | | Ken Burns mặc định; `docs/standards.md` ghi "không cảnh tĩnh > 8 s" nhưng **không cổng nào đo** | ✔ |
| Contact sheet 1 khung/beat, "không báo xong khi chưa soi" (claude-motion, animate) | | Phòng dựng hiện ảnh nguồn từng cảnh — không phải khung của **bản cuối** (sau phụ đề/nhạc/ghép) | ✔ |
| Cost tracker, trần tổng, hỏi trước lượt gọi tốn tiền | | Ngân sách token text 80/100 (supervisor); TTS/ảnh/clip **không có trần tiền** (README "Giới hạn") | ✔ |
| Storyboard gate trước render | | Gate `plan` duyệt brief; phim/hoạt hình có storyboard xem được | Không làm: thêm gate thứ năm là đổi kiến trúc approval, cần ADR riêng |
| Registry runtime, "silent runtime swap is forbidden" | `fake` bị từ chối ở chế độ live; provider khai tường minh, không tự đổi | | |

## Quyết định

Làm đúng ba ô "chưa có" đo được bằng code. Không đổi prompt agent, không thêm gate, không thêm dependency.

1. **Soát slideshow trong QC** (`qc.py`). Cùng một lượt ffmpeg đo bản cuối thêm `freezedetect`; đoạn hình đứng yên
   ≥ `STUDIO_QC_MAX_STATIC_S` (mặc định 8 s, `0` = tắt) thành finding `warn` vị trí `motion` và `metrics.freeze_spans`.
   Đứng yên tới hết file (ffmpeg chỉ in `freeze_start`) được khép bằng thời lượng ffprobe. Là `warn` chứ không `block`:
   người duyệt quyết; nhưng autopilot `publish` vốn giữ lại mọi `warn` khi `allow_qc_warnings: false`, nên video
   slideshow không còn tự lên nền tảng. Ken Burns trên ảnh có họa tiết và footage không bị bắt (kiểm bằng ffmpeg thật).
2. **Contact sheet của bản cuối** (`FFmpegAssembler.contact_sheet`, `Renderer.finalize`). Một khung giữa mỗi cảnh,
   trích từ chính file MP4 cuối bằng `-ss` trước `-i` (tua, không giải mã cả video), nhãn `cảnh · mốc giờ` qua
   `textfile=`, lưới 4 cột bằng `tile`; quá 24 cảnh thì lấy mẫu đều, luôn giữ cảnh đầu và cuối. Phát cùng lượt
   `render.final` dưới dạng `media-assets` kind `contact_sheet` (checksum, provenance `ffmpeg:contact-sheet`), nên outbox
   khôi phục được như mọi asset khác. Không route agent nào nhận kind này và nó không bao giờ được upload. Lỗi khi dựng
   sheet chỉ ghi `render.contact_sheet_failed` — công cụ soát không được làm mất bản dựng. Checklist gate publish có
   dòng `contact_sheet:` chỉ khi sheet cùng `manifest_version` với bản cuối; console hiện sheet ở thẻ duyệt publish và
   tab "Kiểm tra & đăng". Bản `fake` là JSON nên không có sheet.
3. **Sổ chi phí media** (`media_cost.py`). Giá niêm yết người vận hành khai: `tts.price_per_1k_chars`,
   `image.price_per_image` (ảnh cảnh và nền thumbnail), `scene_video.price_per_second`; trần mỗi video
   `production.media_budget_usd`. Chỉ lượt cache trượt (provider thật được gọi) bị tính; provider `fake` luôn 0. Mỗi
   lượt ghi một dòng `media.cost` vào audit-log của renderer (bền, đọc lại khi khởi động). Trần kiểm **trước** lượt gọi
   sẽ vượt và ném `MediaBudgetError`; orchestrator phát `budget_cut` ngay (không retry — thử lại không làm rẻ đi), cùng
   đường với ngân sách token: video dừng tới khi người quyết qua gate escalation. Nâng `media_budget_usd` rồi mở lại
   thì media đã tạo được dùng lại từ cache, không trả tiền lần hai. Không khai giá thì không có dòng sổ, checklist không có
   dòng `media_cost:` và console ghi "Chưa khai bảng giá media" — không hiện 0 USD bịa. Khoá giá bị loại khỏi khoá
   cache (`without_prices`, cả `cache_identity` của ComfyUI): khai giá không được làm trượt cache và sinh lại asset đã
   trả tiền. Contact sheet không nằm trong payload của rights-checker (không đổi đầu vào model khi chưa ghi lại eval).

## Hệ quả và giới hạn

- Sổ là **ước tính theo bảng giá**, không phải hóa đơn: provider tính theo token/ký tự/khung riêng, có bậc giá. Giá
  do người vận hành điền; repo không ghi giá provider nào để khỏi lỗi thời.
- Crash giữa lúc provider trả file và lúc ghi sổ làm sổ thiếu đúng lượt đó; lần chạy lại trúng cache nên không trả hai
  lần. Model text vẫn đếm bằng token ở supervisor, không quy ra USD ở sổ này. Luồng Website → Video (ADR-0015) dùng
  Edge TTS và CLI subscription nên chưa nối sổ.
- `freezedetect` tốn thêm CPU trong lượt QC vốn đã giải mã cả video; ảnh một màu (ví dụ provider `fake`) với Ken Burns
  vẫn bị coi là đứng yên — đúng, vì người xem không thấy chuyển động.
- Contact sheet chưa được gửi cho reviewer model (reviewer vẫn chỉ đọc văn bản, xem `DANH-GIA` §5 C1); nó là đầu vào
  sẵn cho reviewer đa phương thức sau này. Cập nhật: ADR-0020 gửi nó cho quality-reviewer.
- Chưa làm từ kho: storyboard gate trước render, beat sync nhạc, safe-area/collision cho chữ phủ, cảnh báo 80% cho
  ngân sách media.

## Tham chiếu

- `stored` mục 19 §1.2 (OpenMontage: slideshow-risk check, cost tracker, gate theo stage), mục 22 §0, §5.1, §5.2, §9
  (contact sheet, "never claim done without re-render", −14 LUFS), mục 23 §3.2 (audit X-Studio), `WORKFLOW.md`.
- [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html): `freezedetect`, `tile`, `drawtext`.
