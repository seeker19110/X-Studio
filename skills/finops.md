---
name: finops
version: 1
standards: [FinOps Foundation (inform/optimize/operate), Unit economics per video, Budget alerts 80/100, Provider price awareness]
---
# Skill: finops

## Tiêu chuẩn tham chiếu
- FinOps: thấy chi phí (inform) → tối ưu (optimize) → vận hành có ngưỡng (operate)
- Đơn vị kinh tế: chi phí mỗi video đã đăng (token + media), chi phí mỗi 1000 view
- Cảnh báo 80% ngân sách, cắt 100% (code supervisor)
- Giá provider thay đổi: cấu hình, không hard-code; so model theo chất lượng/chi phí bằng eval

## Quy trình (làm đúng thứ tự)
Cộng token thật từ audit-log theo video → cộng media theo asset → so với estimate → báo cáo mỗi chu kỳ →
đề xuất tối ưu (tier standard cho bước rẻ, cache prompt, ít vòng sửa) → ghi `knowledge`.

## Quy tắc
- Không tối ưu bằng cách bỏ review; chỉ giảm vòng lặp và chọn tier.
- Video vượt ngân sách bị cắt: ghi rõ nguyên nhân (retry, repair, injection).
- Cache hit thấp (< 50%) là tín hiệu prompt động quá nhiều.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Chi phí mỗi video có trong báo cáo
- [ ] Cảnh báo/cắt có audit
- [ ] Đề xuất tối ưu có số
- [ ] Không bỏ gate/review để tiết kiệm

## Ví dụ tốt
"V1: 82k token (est 60k, ratio 1.37) vì 1 retry fact; media 16 ảnh; đề xuất: dossier ghi nguồn primary để giảm retry."

## Ví dụ xấu
Tắt fact-checker để "tiết kiệm 20%".
