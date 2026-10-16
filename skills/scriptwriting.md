---
name: scriptwriting
version: 2
standards: [Hook–Promise–Payoff, Plain language, Read-aloud test, 150 wpm pacing, Claims register, YPP inauthentic content]
---
# Skill: scriptwriting

## Tiêu chuẩn tham chiếu
- Cấu trúc Hook → Lời hứa → Nội dung theo mục → Payoff → CTA
- Ngôn ngữ nói: câu ≤ 20 từ, một ý mỗi câu, từ cụ thể thay tính từ
- Kiểm đọc to: câu nào đọc vấp thì viết lại
- 150 từ/phút cho giọng đọc; short ≤ 150 từ
- Sổ claim: mọi câu có số/thực thể/so sánh là một claim có nguồn

## Quy trình (làm đúng thứ tự)
Đọc brief + dossier → viết hook (3 phương án, chọn 1) → dàn ý mục theo key_points → viết narration từng mục →
đánh dấu claim và gắn nguồn từ dossier → viết CTA → đếm từ, tính phút → đọc to sửa vấp → ghi giọng vào `voice`.

## Quy tắc
- Hook nêu lợi ích hoặc câu hỏi cụ thể trong ≤ 15 từ; không "hôm nay mình sẽ".
- Mỗi mục kết bằng một câu chuyển (mở vòng tò mò) để giữ chân.
- `visual_notes` mỗi mục: hình gì minh hoạ ý này (production-manager dùng), không mô tả kỹ thuật ảnh.
- Claim không có nguồn trong dossier: bỏ, hoặc chuyển thành ý kiến có đánh dấu, hoặc giữ với `source: null` để fact-checker chặn.
- Retry theo `hint`: đổi đúng chỗ, giữ phần đã pass, tăng `version`.
- Kịch bản phải có phân tích, so sánh hoặc tổng hợp của kênh, không chỉ đọc lại một nguồn: YouTube không cho kiếm tiền
  nội dung "không chân thực" (làm hàng loạt theo khuôn, lặp lại, ít giá trị riêng — YPP, 7/2025).

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Hook ≤ 15 từ, có lợi ích/câu hỏi
- [ ] 3–8 mục, mỗi mục có narration + visual_notes
- [ ] Mọi câu có số/thực thể có claim_id và nguồn
- [ ] word_count/150 ≈ estimated_minutes, khớp target ± 20%
- [ ] CTA một hành động
- [ ] Không vi phạm boundaries
- [ ] Có giá trị riêng, không phải đọc lại một nguồn

## Ví dụ tốt
Hook: "Một video 8 phút mất bạn 6 giờ? Hôm nay còn 30 phút." Mục 1 narration 60 từ, claim C1 "42% người mới bỏ sau 3 video" → nguồn report trang 14.

## Ví dụ xấu
"Xin chào các bạn, hôm nay mình sẽ nói về..."; mục 900 từ không claim; số liệu "nghiên cứu cho thấy" không nguồn.
