---
name: growth-experiments
version: 1
standards: [Controlled A/B (title/thumbnail), Two-proportion z-test, 95% confidence, Retention guard, One-variable-at-a-time]
---
# Skill: growth-experiments

## Tiêu chuẩn tham chiếu
- Thí nghiệm có kiểm soát: một biến (tiêu đề HOẶC thumbnail), cùng khoảng thời gian, cùng nguồn traffic
- Kiểm định hai tỷ lệ (z-test) trên CTR; code `analytics.judge_experiment` tính, agent chỉ đọc
- Ngưỡng kết luận: độ tin cậy ≥ 0.95
- Guard giữ chân: biến thể thắng CTR nhưng AVD giảm → không thắng (clickbait)
- Mỗi lần một biến; đổi hai thứ thì không học được gì

## Quy trình (làm đúng thứ tự)
Đặt giả thuyết (biến thể khác gì, kỳ vọng gì) → chạy đủ mẫu (≥ 1000 impressions mỗi nhánh) → code kiểm định →
đọc winner/confidence/guard → ghi kết quả vào `insights` → chuyển giả thuyết thắng thành quy tắc `brand`/`seo`.

## Quy tắc
- Không dừng sớm khi "nhìn thấy khác biệt"; đủ mẫu mới kết luận.
- winner null = chưa kết luận, không phải hoà.
- Kết quả một video không thành quy tắc; lặp ≥ 3 video mới ghi `brand`/`seo`.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Một biến mỗi thí nghiệm
- [ ] Mẫu ≥ 1000 impressions/nhánh
- [ ] confidence ≥ 0.95 và retention_guard_ok mới có winner
- [ ] Giả thuyết và kết quả ghi `insights`

## Ví dụ tốt
EXP-V1-B: thumb B CTR 7.1% vs A 5.8%, confidence 0.97, AVD B 7.2s ≥ A 7.0s → winner B; ghi "giả thuyết tò mò thắng lợi ích (1/3 mẫu)".

## Ví dụ xấu
Đổi tiêu đề và thumbnail cùng lúc; kết luận sau 300 impressions.
