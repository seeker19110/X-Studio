# ADR-0017 — Graphic theo cảnh do model chọn, code kiểm số và dựng

Ngày: 2026-10-13. Trạng thái: accepted. Sửa ADR-0003 (phạm vi "model mô tả") và ADR-0009 (pipeline cảnh).

## Bối cảnh

Lần rà thứ hai kho [`seeker19110/stored`](https://github.com/seeker19110/stored) (HEAD `c66fc21`, 2026-10-13) thêm ba
nguồn chạm thẳng xưởng video và một gợi ý tích hợp riêng cho X-Studio (mục 23 §7b): "engine thật ở mục 22
(HyperFrames/Remotion), quality gate kiểu motionmaxxing/claude-motion, `data/videos.json` của awesome-opus5-5-videos
làm tập prompt tham khảo, ManimGX cho explainer". Điểm chung của cả ba (mục 22 §0, §8b, §9c): video giải thích tốt là
**đồ họa có chuyển động mang thông tin** được viết thành code rồi dựng (Opus 5.5 "viết animation thành code rồi quay
màn hình"), không phải ảnh sinh + Ken Burns.

Trước ADR này X-Studio đã có `MotionSpec` (title/quote/bars, FFmpeg vẽ chữ Unicode), nhưng **chỉ người vận hành** bật
được nó qua `production.graphics` trong media.yaml. production-manager chỉ được viết `visual_prompt` cho model ảnh —
mà model ảnh vẽ chữ và biểu đồ sai. Kết quả: video explainer mặc định là chuỗi ảnh tĩnh, đúng thứ ADR-0016 mới bắt
được ở QC (đứng yên ≥ 8 s) nhưng chưa ngăn được từ khâu lập kế hoạch.

Đối chiếu ba cột (WORKFLOW của kho):

| Bài học trong kho | X-Studio đã có, sâu hơn | Đã có, nông hơn | Chưa có |
|---|---|---|---|
| Video as code: template dữ liệu, code dựng xác định (Remotion/HyperFrames §2) | | `MotionSpec` + FFmpeg, không Chromium | Model không chọn được template; chỉ 3 template |
| Agent phải chứng minh output: kiểm layout trước render (`manimgx inspect`, `lint.mjs` của motionmaxxing) | QC đo file thật; contact sheet (ADR-0016) | Chữ được bọc dòng theo ước lượng cố định, không kiểm tràn | Không có kiểm vùng an toàn / chồng phụ đề |
| Số liệu trên đồ họa phải có nguồn (§8b: `post_url`, `prompt_partial`) | fact-checker chặn claim không nguồn | `bars` bắt buộc trường `source` | Không đối chiếu con số vẽ với claim đã kiểm |
| Safe-area/collision cho chữ phủ (ADR-0016 ghi "chưa làm") | | `visual-direction` dặn vùng an toàn cho ảnh | Đồ họa vẽ dòng nguồn ở 90 % chiều cao, đè lên phụ đề đốt cứng |
| Engine nặng: HyperFrames (Chromium), ManimGX (Rust/wgpu, Python ≥ 3.13, wheel GPL) | | | **Không nhúng** — xem "Phương án bị loại" |

## Quyết định

1. **`Scene.graphic: MotionSpec | None`** trong `scene-manifests` (schema + `events.py` cùng lúc). Model là
   `scene_graphic.MotionSpec` (chỉ phụ thuộc pydantic, `extra="forbid"`): **dữ liệu, không phải code**. Năm template:
   `title`, `quote`, `bars` (≤ 6 cột, nhãn ≤ 40 ký tự), `list` (2–6 ý ≤ 80 ký tự, hiện lần lượt), `stat` (một số lớn
   đếm lên, `unit`). `bars`/`stat` bắt buộc `source`; số hữu hạn ≥ 0. Đầu ra model sai schema bị runner từ chối như
   mọi payload khác (bus kiểm cả pydantic lẫn JSON Schema).
2. **Model chọn, code kiểm số** (`motion.verify_graphics`, gọi trong `Orchestrator._call` trước khi publish manifest):
   mọi giá trị trên `bars`/`stat` phải có trong `text` của một claim **có `source`** trong kịch bản đã qua fact-check
   (đọc số theo cả kiểu Việt `1.234,5` lẫn kiểu Anh `1,234.5`). Lệch → bỏ graphic, cảnh quay về ảnh từ
   `visual_prompt`, ghi `graphic.unverified` vào audit-log. Không chặn cả video: một biểu đồ thiếu căn cứ không đáng giá
   một vòng sửa. Đây là bất biến "số liệu thật, không bịa" áp cho chữ trên hình.
3. **Layout do code đo** (`motion.plan`, thuần, không FFmpeg): mỗi khối chữ được bọc dòng và thu nhỏ tới khi vừa ô; cỡ
   nhỏ nhất `MIN_TEXT` = 2,6 % cạnh ngắn. Vùng an toàn `safe_bottom`: 16:9 chừa ~24 % đáy khi đốt phụ đề (2 dòng
   `production.finish`), 8 % khi không; 9:16 luôn chừa 22 % đáy cho giao diện Shorts và 12 % đỉnh. Khối nội dung căn
   giữa theo chiều dọc; dòng nguồn ghim sát vùng an toàn. Không vừa ở cỡ nhỏ nhất → `MediaError` "không vừa khung",
   **không cắt chữ** (cắt chữ là đổi nội dung). Có test cho mọi template × 3 độ phân giải × có/không phụ đề với chữ dài
   nhất schema cho phép.
4. **Renderer**: lựa chọn tường minh của người thắng graphic của model — `production.graphics` thay graphic, asset
   thư viện có license (`production.scenes`) thay cả cảnh. Graphic của model bị bỏ qua khi cảnh có asset thư viện hoặc
   `video.provider` không phải ffmpeg (`render.graphic_skipped` kèm lý do) và cảnh dùng ảnh/footage — nó là phần tăng
   cường; graphic người vận hành khai mà thiếu ffmpeg vẫn báo lỗi rõ như trước. Khoá cache thêm `layout` (`LAYOUT_VERSION`) và `bottom`: đổi layout
   hay bật phụ đề phải dựng lại, không trúng clip cũ.
5. **Prompt**: production-manager v2 + skill `scene-production` v2 hướng dẫn khi nào dùng template nào, chép nguyên số
   từ claim có nguồn, vẫn giữ `visual_prompt` dự phòng; phim/hoạt hình không dùng graphic. Eval thêm ca
   `claim-co-so-lieu-phai-thanh-graphic-dung-so`, sàn `cases: 3`; đã ghi lại bằng model thật (3/3).

`stat` đếm số bằng `drawtext` `expansion=normal` — chuỗi biểu thức do CODE viết từ một số nguyên đã kiểm, không bao
giờ là chữ của model; mọi chữ khác vẫn `expansion=none`. Đơn vị vẽ bằng một `drawtext` riêng vì `%` trong chuỗi
expansion là lỗi cú pháp ("Stray %").

## Phương án bị loại

- **Nhúng HyperFrames/Remotion (Chromium) làm engine**: kéo trình duyệt vào đường render mọi video và cho model viết
  HTML/JS — vi phạm ADR-0003 (model không xuất code chạy) và thêm bề mặt tấn công; Playwright đã có chỉ dùng cho luồng
  Website → Video (ADR-0015) với trang của người dùng.
- **ManimGX cho explainer toán**: v0.1.x 5 ngày tuổi, 2 người, Python ≥ 3.13 (repo hỗ trợ 3.11+), wheel GPL-3.0 vì x264.
  Đường vào đúng là `production.library` (clip có license, ADR-0009) khi cần, không phải dependency.
- **Thêm gate storyboard thứ năm**: đổi kiến trúc approval cho một lợi ích nay đã có cách rẻ hơn (trần ngân sách media
  ADR-0016 + kiểm layout/số liệu trước render ở đây).
- **Dùng `data/videos.json` của awesome-opus5-5-videos làm ví dụ prompt**: tập "showreel" HTML/Canvas, phễu thương mại
  (`utm_campaign`), nhiều prompt chỉ đăng một phần — không khớp kênh explainer tiếng Việt; không đưa vào prompt.

## Hệ quả và giới hạn

- Video explainer có đồ họa dữ liệu mà không ai phải sửa media.yaml; QC slideshow (ADR-0016) ít bắt hơn vì cảnh số liệu
  giờ chuyển động (chữ trượt vào, ý hiện lần lượt, số đếm lên).
- Kiểm số chỉ là **khớp giá trị**, không hiểu ngữ nghĩa: model vẫn có thể gắn đúng số 42 với nhãn sai. Người duyệt
  gate publish thấy nó trên contact sheet (ADR-0016). Claim không có chữ số (ví dụ "gần một nửa") thì không vẽ được
  `stat` — đúng ý: không có số để vẽ.
- Bề rộng chữ là ước lượng (`CHAR_W` = 0,62 cỡ chữ, DejaVu Sans) chứ không đo glyph thật; dư cho chữ hoa và dấu tiếng
  Việt, đã soi bằng mắt ở 1280×720 và 720×1280.
- Graphic không qua editor: editor sửa cảnh bằng ảnh/giọng như cũ; đổi graphic là việc của production-manager ở vòng
  manifest sau.

## Tham chiếu

- `stored` mục 22 §0, §2, §8 (motionmaxxing), §8b (awesome-opus5-5-videos), §9c (ManimGX, `manimgx inspect`), mục 23 §7b,
  §8.1 (ma trận vệ sinh: X-Studio thiếu Dependabot, SECURITY.md — bổ sung cùng thay đổi này).
- ADR-0003, ADR-0009, ADR-0016; [FFmpeg drawtext](https://ffmpeg.org/ffmpeg-filters.html#drawtext-1) (`expansion`, `eif`).
