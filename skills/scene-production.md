---
name: scene-production
version: 2
standards: [Scene manifest, Shot list, Durable production state, Repair-not-rebuild, Data graphics as templates]
---
# Skill: scene-production

## Tiêu chuẩn tham chiếu
- Scene manifest là nguồn sự thật của sản xuất: cảnh, narration, prompt, thời lượng, asset, khoá
- Shot list truyền thống: mỗi cảnh một ý, một hình chủ đạo
- Trạng thái sản xuất bền vững: mọi bước ghi lại được, gián đoạn thì tiếp tục, không làm lại từ đầu
- Sửa, không dựng lại: thay đổi một cảnh chỉ chạm cảnh đó
- Đồ họa dữ liệu theo template (Remotion/HyperFrames: "video as code"): số, so sánh, danh sách là chữ/đồ họa có
  chuyển động do code vẽ đúng từng ký tự — model ảnh vẽ chữ và biểu đồ sai; chuỗi ảnh tĩnh dài là slideshow

## Quy trình (làm đúng thứ tự)
Đọc kịch bản đã qua fact-check → chia mục thành cảnh theo ý → viết narration đọc được → viết visual prompt nhất quán →
tính thời lượng theo số từ → đặt voice/aspect → kiểm tổng thời lượng với target → ghi tham chiếu vào `production`.

## Quy tắc
- Cảnh ≤ 45 từ narration (~15s); mục dài thì nhiều cảnh, không kéo dài cảnh.
- `scene_id` ổn định (S1, S2...) qua các version để editor/analytics tham chiếu; sửa thì giữ id.
- Narration: viết số thành chữ khi cần đọc ("42 phần trăm"), bỏ ký hiệu, viết tắt đọc được.
- Không thay đổi nội dung claim đã pass; chỉ chia câu.
- Short: 9:16, ≤ 8 cảnh, ≤ 60s.
- Chọn `graphic` cho cảnh mang dữ liệu: một số → `stat`; so sánh → `bars`; các bước/ý → `list`; trích dẫn → `quote`;
  mở chương → `title`. Số chép nguyên từ claim có nguồn; giữ `visual_prompt` làm phương án dự phòng.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] scene_id duy nhất, order liên tục
- [ ] narration ≤ 45 từ, đọc được
- [ ] visual_prompt cụ thể, nhất quán phong cách
- [ ] duration_s ≈ từ/2.5; tổng khớp target ± 20%
- [ ] voice/aspect đúng format
- [ ] Không đổi nội dung claim
- [ ] Cảnh số liệu/so sánh/liệt kê có `graphic`; số trong graphic có trong claim có nguồn

## Ví dụ tốt
Mục "Vấn đề" 80 từ → S1 (35 từ, bàn làm việc bừa bộn), S2 (45 từ, đồng hồ 6 giờ); voice alloy, pace medium, 16:9.

Claim "42% người mới bỏ cuộc sau 3 video" (có nguồn) → cảnh S3 narration "Bốn mươi hai phần trăm người mới bỏ cuộc
sau ba video", `graphic` {template: stat, title: "Bỏ cuộc sau 3 video", values: [42], unit: "%", source: "<nguồn>"}.

## Ví dụ xấu
Một cảnh 200 từ; prompt "một cái gì đó đẹp"; đổi "42%" thành "gần một nửa" làm lệch claim; `stat` 45% "cho tròn" khi
claim ghi 42%; visual_prompt "biểu đồ cột ghi 42%" (model ảnh vẽ sai số — dùng `graphic`).
