# ADR-0020 — Reviewer đa phương thức có ranh giới: ảnh là đầu vào, không phải tool

Ngày: 2026-10-14. Trạng thái: accepted. Thực hiện DANH-GIA §5 C1 (lớp model); bổ sung ADR-0003, ADR-0007, ADR-0016.

## Bối cảnh

Editor, quality-reviewer và rights-checker chỉ nhận JSON. Editor phải "kiểm ảnh khớp prompt" mà không thấy ảnh;
rights-checker không bắt được logo, chữ hay khuôn mặt thật mà model ảnh vô tình vẽ ra khi prompt và provenance trông
vô hại; quality-reviewer không thấy chữ thật trên thumbnail hay bản cuối sau phụ đề. Lớp code (QC đo file, ADR-0009,
ADR-0016, ADR-0019) đã bắt được ảnh đen, im lặng, hình đứng yên, nhưng không hiểu nội dung hình.

Đo ngày 14/10/2026: CLI `claude -p --input-format stream-json` nhận ảnh base64 trong lượt user và đọc đúng cả chữ lẫn
màu tô trong một khung phụ đề karaoke, không cần mở tool đọc file nào cho model.

## Quyết định

1. **`ModelClient.complete(..., attachments=[Attachment])`** ở `xagents-core` (tham số tuỳ chọn, mặc định không có
   — mọi lời gọi cũ giữ nguyên). `Attachment(media_type="image/*", data, sha256, label)`; `sha256` là băm **file
   nguồn**. Anthropic: `image` block base64 trước chữ trong lượt user đầu; OpenAI-compatible: `image_url` data URL;
   `claude-code`: `--input-format stream-json --output-format stream-json`, `_parse` lấy dòng `result`; Codex: từ chối
   rõ ràng; routing bỏ qua backend `supports_images: false` (mặc định false cho codex). FakeClient ghi băm ảnh.
2. **Agent khai thứ được xem** trong front matter: `inputs: [scene_images | contact_sheet | thumbnails]`. Editor
   `scene_images`; rights-checker `scene_images`, `thumbnails`; quality-reviewer `contact_sheet`, `thumbnails`
   (gói của nó nay mang contact sheet của bản cuối).
3. **Code chọn và thu nhỏ ảnh** (`studio.attachments`): chỉ từ asset (dict có `kind` + `path`) mà orchestrator đã đưa
   vào payload/extra — không đọc đường dẫn nào khác. Ảnh cảnh ghép lưới 4×3 có nhãn scene_id (video trăm cảnh vẫn là
   vài ảnh); contact sheet ≤ 1568 px, thumbnail ≤ 768 px (tối đa 3); trần 6 ảnh mỗi lượt. File thiếu/hỏng/không phải
   ảnh/quá 50 MB bị bỏ kèm audit `attachments_skipped`; ảnh gửi đi ghi audit `attachments` (nhãn, băm, số byte); danh
   sách ảnh nối vào lượt user để model biết ảnh nào là gì. Client tự viết chưa có tham số `attachments` thì lượt chạy
   như cũ, audit `attachments_unsupported`.
4. **Eval**: `prompt_key(system, user, attachments)` thêm băm file nguồn khi có ảnh (không ảnh thì khoá y hệt trước).
   Fixture ảnh nằm ở `evals/fixtures/`; mỗi reviewer có thêm một ca **chỉ đúng khi nhìn ảnh**: logo thương hiệu trong
   ảnh cảnh với provenance sạch (rights), chữ hứa hẹn trên ảnh thumbnail khác `overlay_text` (quality), banner quảng cáo
   thay cho sơ đồ trong ảnh cảnh mà không có ghi chú nào (editor). Ghi bằng model thật: 9/9 ca đạt, câu trả lời nêu
   đúng thứ chỉ có trong ảnh.

## Hệ quả

- ADR-0003 không đổi: model vẫn chỉ trả JSON, ảnh là đầu vào như văn bản. ADR-0007 không đổi: tool duy nhất vẫn là web
  chỉ đọc; không mở tool đọc file cho model.
- Lượt reviewer tốn thêm token ảnh (một lưới 1536×648 cỡ vài nghìn token); trần 6 ảnh giữ chi phí có giới hạn.
- Chưa làm: nghe audio (whisper ở QC là lớp code, ADR-0019), ảnh cho fact-checker, khung từ `scene_video` (contact
  sheet bản cuối đã phủ).

## Phương án bị loại

- **Cho model tool đọc file ảnh**: trái ADR-0007, và model chọn file là model hành động.
- **Gửi mọi ảnh cảnh rời**: video dài vượt giới hạn ảnh mỗi request; lưới có nhãn giữ ngữ cảnh và chi phí.
- **Băm bytes đã thu nhỏ vào khoá eval**: Pillow khác phiên bản nén khác bytes → CI phát lại đỏ vô cớ.
