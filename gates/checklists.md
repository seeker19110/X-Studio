# Human gate — checklist (approval-first)

Nguyên tắc: không có gì lên lịch/đăng/trả lời công khai trước khi qua gate; four-eyes; timeout 24h (nhắc 12h);
quá hạn KHÔNG tự đi tiếp. Lệnh: `python -m studio.gate_cli list | approve | request_changes | reject | hold <id> --by <ai> --reason <lý do>`.

## Gate `plan` — Duyệt kế hoạch biên tập (PLAN-\<channel\>-\<n\>)
- [ ] Mỗi brief thuộc đúng pillar của kênh, có angle khác đối thủ, đúng khán giả
- [ ] Không brief nào vi phạm `boundaries` của chủ kênh
- [ ] `estimate_tokens` có cơ sở; `budget_tokens ≥ estimate × 1.5` (code đã kiểm)
- [ ] `risk_tags` đúng cho chủ đề nhạy cảm (health/finance/legal/minors/politics/music/footage/brand/person)
- [ ] Số brief không vượt nhịp đăng đã cam kết; brief bắt trend có hạn dùng
- [ ] priority hợp lý (deadline/trend = 1)
Kết quả: approve (desk dispatch) / request_changes / reject

## Gate `publish` — Duyệt gói nội dung trước khi lên lịch (PUB-\<video\>)
- [ ] `review:fact:pass` — mọi claim có nguồn, không block
- [ ] `review:rights:pass` — mọi asset có provenance/license hợp lệ, không người thật/nhân vật/nhạc chưa license
- [ ] `review:quality:pass` — hook, nhịp, tiêu đề/thumbnail được nội dung chứng minh, thời lượng đúng
- [ ] Preflight: 0 finding block; mỗi warn còn lại có lý do giữ (ghi trong `--reason`)
- [ ] Đã xem bản cuối `output/<video>/final_v<n>.mp4` và thumbnail `chosen`
- [ ] Không vi phạm `boundaries` và content-policy (YMYL có miễn trừ)
- [ ] Lịch đăng theo chiến lược (`strategy`) hoặc nêu trong reason
- [ ] Người duyệt ≠ người tạo (desk)
Kết quả: approve (publisher lên lịch) / request_changes(lý do → làm lại với hint) / hold / reject(đóng video)

## Gate `replies` — Duyệt lô trả lời bình luận (REP-\<video\>-\<n\>)
- [ ] Mỗi reply đúng giọng kênh, ≤ 60 từ, trả lời thẳng
- [ ] Reply `[cần người]` (giá, khiếu nại, pháp lý, sức khoẻ, dữ liệu cá nhân) đã được người soạn lại hoặc bỏ
- [ ] Không hứa hẹn, không tranh cãi, không lộ thông tin nội bộ
- [ ] Spam/độc hại không có trong lô (đã ghi `community`)
Kết quả: approve (publisher đăng các reply không `requires_human`) / reject

## Gate `escalation` — Video kẹt (ESC-\<video\>)
Mở khi video `blocked` (retry > 3) hoặc supervisor `escalate` (lỗi lặp, im lặng quá timeout, vượt ngân sách).
- [ ] root cause rõ (đọc `review-results` và `audit-log` của video)
- [ ] hint trong `--reason` đủ cụ thể để agent làm KHÁC lần trước
- [ ] ngân sách còn (supervisor `report`)
Kết quả: approve (mở lại với hint, retry về 0, resume) / reject (đóng)

## Chỉ người được quyết
- Đăng công khai, đổi lịch, gỡ video (rollback)
- Chấp nhận rủi ro bản quyền / license ngoại lệ
- Nội dung YMYL, nội dung nhắc tới người thật
- Trả lời bình luận chạm giá, hợp đồng, khiếu nại, pháp lý
