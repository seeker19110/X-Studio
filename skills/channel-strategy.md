---
name: channel-strategy
version: 1
standards: [Content pillars, ICE prioritisation, YouTube Creator Academy, Jobs-to-be-done, Editorial calendar]
---
# Skill: channel-strategy

## Tiêu chuẩn tham chiếu
- Content pillars: 3–5 trụ cột nội dung, mỗi video thuộc đúng một trụ; trụ nào không ra video trong 8 tuần thì xét bỏ
- ICE (impact × confidence × ease) để xếp ưu tiên; ghi điểm từng thành phần, không chỉ tổng
- Jobs-to-be-done: mỗi video giải quyết một "việc" của khán giả (học, chọn, giải trí, cập nhật)
- Lịch biên tập theo nhịp (cadence) đã cam kết; nhịp đều thắng đợt bùng
- YouTube Creator Academy: chuỗi (series) + video nền tảng (evergreen) + video bắt trend theo tỷ lệ 50/30/20

## Quy trình (làm đúng thứ nhất)
Đọc `channel_brief` (mục tiêu, khán giả, pillar, nhịp, ranh giới) → đọc `trend-reports` và `insights` trên blackboard →
liệt kê ứng viên theo pillar → lọc bằng ranh giới → chấm ICE → chọn đủ nhịp (không hơn) → viết brief với angle khác
đối thủ → ước lượng token theo `calibration` → ghi lý do vào `strategy`.

## Quy tắc — chọn chủ đề
- Mỗi brief nêu rõ "việc" khán giả cần xong và vì sao video này khác 2 video đối thủ gần nhất (`angle`).
- Tỷ lệ evergreen/series/trend theo pillar; không quá 1/3 số brief là bắt trend.
- Brief bắt trend có hạn dùng: ghi vào `boundaries` "đăng trước <ngày>"; quá hạn thì huỷ, không ép sản xuất.
- Chủ đề YMYL (sức khoẻ, tài chính, pháp lý) chỉ khi kênh có pillar và ranh giới tương ứng; gắn `risk_tags`.
- Short bổ trợ long (cắt từ video dài đã duyệt) chứ không thay thế pillar.

## Quy tắc — ước lượng và ưu tiên
- `estimate_tokens` theo mẫu gần nhất cùng format (calibration median × ước lượng thô); không có mẫu thì PERT (O+4M+P)/6.
- `priority` 1 cho video có deadline hoặc trend; 2–3 cho evergreen chính; 4–5 cho thử nghiệm.
- Không lập kế hoạch vượt nhịp đã cam kết: 2 video/tuần nghĩa là tối đa 4 brief cho 2 tuần.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Mỗi brief thuộc đúng một pillar và có angle so với đối thủ
- [ ] Ranh giới của chủ kênh không bị vi phạm ở brief nào
- [ ] estimate_tokens có cơ sở (calibration/PERT); budget ≥ estimate × 1.5
- [ ] priority theo ICE, ghi được lý do
- [ ] risk_tags đúng cho chủ đề nhạy cảm
- [ ] Số brief không vượt nhịp
- [ ] Lý do chọn ghi vào `strategy`

## Ví dụ tốt
Brief "So sánh 3 công cụ AI dựng video cho người mới (2026)": pillar so-sánh, angle "đo thời gian thật trên cùng kịch bản"
(đối thủ chỉ liệt kê tính năng), estimate 60k token (calibration long 1.1 × 55k), priority 2, boundaries "không hứa thu nhập".

## Ví dụ xấu
8 brief cho một tuần nhịp 2 video; brief "Cách kiếm 100 triệu/tháng từ YouTube" vi phạm ranh giới; không estimate.
