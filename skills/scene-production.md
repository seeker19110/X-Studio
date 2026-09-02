---
name: scene-production
version: 1
standards: [Scene manifest, Shot list, Durable production state, Repair-not-rebuild]
---
# Skill: scene-production

## Tiêu chuẩn tham chiếu
- Scene manifest là nguồn sự thật của sản xuất: cảnh, narration, prompt, thời lượng, asset, khoá
- Shot list truyền thống: mỗi cảnh một ý, một hình chủ đạo
- Trạng thái sản xuất bền vững: mọi bước ghi lại được, gián đoạn thì tiếp tục, không làm lại từ đầu
- Sửa, không dựng lại: thay đổi một cảnh chỉ chạm cảnh đó

## Quy trình (làm đúng thứ tự)
Đọc kịch bản đã qua fact-check → chia mục thành cảnh theo ý → viết narration đọc được → viết visual prompt nhất quán →
tính thời lượng theo số từ → đặt voice/aspect → kiểm tổng thời lượng với target → ghi tham chiếu vào `production`.

## Quy tắc
- Cảnh ≤ 45 từ narration (~15s); mục dài thì nhiều cảnh, không kéo dài cảnh.
- `scene_id` ổn định (S1, S2...) qua các version để editor/analytics tham chiếu; sửa thì giữ id.
- Narration: viết số thành chữ khi cần đọc ("42 phần trăm"), bỏ ký hiệu, viết tắt đọc được.
- Không thay đổi nội dung claim đã pass; chỉ chia câu.
- Short: 9:16, ≤ 8 cảnh, ≤ 60s.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] scene_id duy nhất, order liên tục
- [ ] narration ≤ 45 từ, đọc được
- [ ] visual_prompt cụ thể, nhất quán phong cách
- [ ] duration_s ≈ từ/2.5; tổng khớp target ± 20%
- [ ] voice/aspect đúng format
- [ ] Không đổi nội dung claim

## Ví dụ tốt
Mục "Vấn đề" 80 từ → S1 (35 từ, bàn làm việc bừa bộn), S2 (45 từ, đồng hồ 6 giờ); voice alloy, pace medium, 16:9.

## Ví dụ xấu
Một cảnh 200 từ; prompt "một cái gì đó đẹp"; đổi "42%" thành "gần một nửa" làm lệch claim.
