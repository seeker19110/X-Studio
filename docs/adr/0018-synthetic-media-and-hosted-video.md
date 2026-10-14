# ADR-0018 — Nhãn `synthetic_media` và provider clip thuê (Veo)

Ngày: 2026-10-14. Trạng thái: accepted. Bổ sung ADR-0002 (gate), ADR-0003 (code hành động), ADR-0013 (phim/hoạt hình).

## Bối cảnh

Lần rà thứ ba kho [`seeker19110/stored`](https://github.com/seeker19110/stored) và quét Internet ngày 2026-10-14
([`docs/2026-10-14-nghien-cuu-nang-cap.md`](../2026-10-14-nghien-cuu-nang-cap.md)) cho ba sự kiện nền tảng chạm thẳng
xưởng:

1. **YouTube bắt khai nội dung AI trông như thật.** `videos.insert`/`update` có `status.containsSyntheticMedia`; từ
   tháng 5/2026 YouTube tự gắn nhãn khi phát hiện, và khai sai để né nhãn là vi phạm. Trước ADR này `MetadataPackage`
   không có chỗ ghi quyết định ấy, adapter không gửi, người duyệt không thấy.
2. **Veo 3.1 GA qua Gemini API** (`veo-3.1-generate-001`, `veo-3.1-fast-generate-001`): `POST
   models/{model}:predictLongRunning` → operation → tải MP4, 4/6/8 giây, 720p/1080p/4k, ≤ 3 ảnh tham chiếu, SynthID.
   X-Studio chỉ có `scene_video` cục bộ (ComfyUI, command): ai không có GPU thì không làm được phim.
3. Quota YouTube đổi sang **rổ** (~100 `videos.insert`/ngày/project) thay vì 1 600 đơn vị/upload — checklist gate
   ghi số cũ.

## Quyết định

1. **`MetadataPackage.synthetic_media: bool | None`** (schema `metadata-packages` + `events.py` cùng lúc).
   `None` = chưa ai quyết. Code quyết khi chắc (`preflight.synthetic_media_of`): `production.synthetic_media` trong
   media.yaml do chủ xưởng đặt thắng tất cả; không có thì **chỉ** khi manifest có asset hình `license: generated` và
   `cinema.visual_mode == generated_video` hoặc `production_type == film` → `True`; hoạt hình và explainer ảnh sinh
   → `None` (nhãn YouTube dành cho "trông như thật", hoạt hình rõ ràng không cần). Orchestrator ghi
   `synthetic_media.decided` vào audit-log và phát lại `metadata-packages` nếu khác bản cũ.
2. **Gate `publish` thấy dòng `synthetic_media:`** trong checklist; `(chưa khai)` là việc của người duyệt, không phải
   lỗi chặn — YouTube tự gắn nhãn nếu bỏ trống; preflight chỉ `warn`. Adapter YouTube gửi
   `status.containsSyntheticMedia` khi khác `None`; `FakePlatform` ghi vào evidence. Không có đường "quên khai" nào
   làm video lên nhanh hơn: đây vẫn là approval-first (ADR-0002).
3. **Provider `scene_video: veo`** (`video_gen.VeoVideo`): `urllib` thuần, khóa từ `GEMINI_API_KEY`/`GOOGLE_API_KEY`
   hoặc `api_key_env`; tham số lấy từ cấu hình chung (`resolution` WxH → `aspectRatio` 16:9/9:16 và tier 720p/1080p/4k
   theo cạnh ngắn; `duration_s` làm tròn lên 4/6/8, 1080p/4k và ảnh tham chiếu buộc 8 s; `seed % 2³²`;
   `negative_prompt` khi không rỗng). Operation name được **checkpoint** trong `output/.cache/video-jobs/` như ComfyUI:
   timeout hay mất mạng không nộp lại job đã trả tiền; operation lỗi giữ checkpoint và từ chối nộp lại. Chỉ tải từ
   URI HTTPS trên `*.googleapis.com`, có trần byte, ffprobe kiểm trước khi vào cache. Ghi chú `MediaResult` nêu
   operation và SynthID. Giá đi qua sổ chi phí (ADR-0016) bằng `price_per_second` do chủ xưởng khai.
   `ComfyUIVideo` và `VeoVideo` dùng chung lớp `_HttpJob` (submit → checkpoint → poll → download); thông điệp lỗi
   giữ nguyên tên dịch vụ.
4. **Provider `scene_video: omni`** (`video_gen.OmniVideo`, Gemini Omni 1.1 Flash): `POST /interactions` với
   `response_format {type: video, aspect_ratio, resolution 360p/720p/1080p/4k, delivery: uri}`, `store: false`; ảnh
   tham chiếu thành phần `image` của `input` và `video_config.task: reference_to_video`. Gọi đồng bộ (tài liệu không
   có luồng poll cho interaction), nên ngay khi có phản hồi code checkpoint `interaction` + `uri`, rồi poll
   `GET files/{id}` tới `ACTIVE` và tải — timeout lúc chờ file không sinh lại clip đã trả tiền. Omni không nhận
   thời lượng, seed hay negative prompt: thời lượng đo bằng ffprobe, seed chỉ nằm trong khoá cache. Video inline
   (`data` base64) cũng nhận, có trần byte. Veo và Omni dùng chung `_GeminiJob` (HTTPS, `x-goog-api-key`, chỉ tải
   từ `*.googleapis.com`, ảnh tham chiếu ≤ 3, khoá cache bỏ giá và khóa).
5. Checklist gate `publish` sửa ghi chú quota theo rổ.
6. **`MetadataPackage.paid_product_placement: bool | None`** (Data API 7/2026,
   `paidProductPlacementDetails.hasPaidProductPlacement`). Xưởng không có khái niệm tài trợ trong brief, nên nguồn
   duy nhất là chính sách kênh `production.paid_product_placement`; orchestrator ghi vào metadata sau bản cuối (audit
   `paid_product_placement.decided`). Adapter chỉ thêm part `paidProductPlacementDetails` khi khác `None`. Gate
   `publish` luôn có dòng `paid_product_placement:` để người duyệt chặn video có tài trợ mà chưa khai.

## Hệ quả

- Người vận hành khai `production.synthetic_media` một lần cho kênh phim AI; kênh explainer ảnh sinh để người duyệt
  quyết từng video. Không có số liệu bịa: cờ chỉ `True` khi có asset `generated` thật trong manifest.
- Veo là provider thuê đầu tiên của `scene_video`: thêm `license: generated` vào cấu hình, `cinema.visual_mode:
  generated_video` sẽ kéo cờ `synthetic_media` lên `True` tự động.
- Chưa làm: `personGeneration` (mặc định API),
  mở rộng clip 8 s → dài hơn (`extend`), Veo trên Vertex AI (khác xác thực).

## Phương án bị loại

- **Luôn gửi `containsSyntheticMedia=true` cho mọi video có ảnh sinh**: gắn nhãn thừa lên explainer minh họa, giảm
  CTR mà không đúng định nghĩa của YouTube. Để `None` → người duyệt.
- **Dùng SDK `google-genai`**: trái quy ước `urllib` thuần của `media.py`, kéo thêm dependency vào `.venv` chung.
