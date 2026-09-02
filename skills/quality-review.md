---
name: quality-review
version: 1
standards: [Editorial QC checklist, Title–content honesty, Pacing limits, Audio/visual sync, Accessibility (captions-ready)]
---
# Skill: quality-review

## Tiêu chuẩn tham chiếu
- QC biên tập: hook, cấu trúc, nhịp, kết, CTA — mỗi mục có ngưỡng đo được
- Trung thực tiêu đề/thumbnail: nội dung phải chứng minh lời hứa
- Nhịp: hook ≤ 5s tới lời hứa; không cảnh > 20s một hình; tổng ± 20% target
- Đồng bộ: narration trong asset khớp manifest; ảnh khớp prompt; không cảnh trống
- Tiếp cận: narration rõ để phụ đề tự động đúng; không phụ thuộc chữ trong ảnh

## Quy trình (làm đúng thứ tự)
Đọc package → kiểm hook → kiểm từng cảnh (ảnh/narration/thời lượng) → kiểm tiêu đề/thumbnail so nội dung →
kiểm preflight còn block? → xếp finding theo mức → root_cause một câu → metrics.

## Quy tắc
- Block: khán giả bị lừa (tiêu đề/thumbnail không được chứng minh), hook > 8s, cảnh trống/ảnh sai hẳn, thời lượng lệch > 30%, preflight block.
- Warn: cảnh > 20s, chuyển gãy, CTA yếu, narration lặp.
- Mỗi finding có location; "video hơi chán" không phải finding.
- Không hạ chuẩn vì đã hết vòng sửa; ghi rõ để gate quyết.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Hook ≤ 5s tới lời hứa
- [ ] Không cảnh > 20s; không cảnh thiếu asset
- [ ] Tiêu đề/thumbnail được nội dung chứng minh
- [ ] Thời lượng ± 20% target
- [ ] CTA có, một hành động
- [ ] Preflight 0 block
- [ ] metrics đủ

## Ví dụ tốt
block S1 "hook 12s mới tới lời hứa"; warn S4 "22s một hình"; root_cause "kịch bản mở đầu dài" → hint giai đoạn script.

## Ví dụ xấu
Pass video có thumbnail "6 GIỜ → 30 PHÚT" nhưng video không đo thời gian nào.
