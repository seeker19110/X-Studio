---
name: cost-estimation
version: 1
standards: [Reference-class forecasting, PERT, Budget = estimate × 1.5, Token + media cost, Calibration loop]
---
# Skill: cost-estimation

## Tiêu chuẩn tham chiếu
- Dự báo theo lớp tham chiếu: video cùng format/pillar gần nhất là cơ sở, không phải cảm tính
- PERT (O + 4M + P)/6 khi chưa có mẫu
- Ngân sách = ước lượng × 1.5; code từ chối brief không đạt
- Chi phí = token model text + media (TTS theo ký tự, ảnh theo số cảnh, ghép video theo phút)
- Vòng hiệu chỉnh: supervisor ghi actual/estimate theo format; lần sau nhân median

## Quy trình (làm đúng thứ tự)
Chọn lớp tham chiếu (format, số cảnh dự kiến) → ước lượng token text (research + script + review + production ≈ 4–6 lượt agent) →
ước lượng media (số cảnh × ảnh + ký tự narration) → nhân `calibration.ratio_median` → đặt budget ≥ ×1.5 → ghi cơ sở.

## Quy tắc
- Không có mẫu → PERT với O/M/P nêu rõ.
- Video retry tốn thêm ≈ 40% một vòng; brief có risk_tags tốn thêm review.
- Ước lượng ghi trong brief (estimate_tokens) và trong `strategy` (cơ sở).

## Checklist (supervisor và human gate dùng để chấm)
- [ ] estimate_tokens có cơ sở (mẫu hoặc PERT)
- [ ] budget ≥ estimate × 1.5
- [ ] Media cost ước lượng theo số cảnh
- [ ] Hiệu chỉnh theo calibration khi có

## Ví dụ tốt
Long 8 phút, 14 cảnh: text 55k (tham chiếu V3, V5) × 1.1 (calibration) = 60k; media 14 ảnh + 1200 ký tự TTS; budget 100k.

## Ví dụ xấu
estimate 10k cho video 20 phút "cho rẻ"; không ghi cơ sở.
