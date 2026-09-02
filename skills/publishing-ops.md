---
name: publishing-ops
version: 1
standards: [Approval-first publishing, Private-upload-then-schedule, Idempotent upload, Rollback (unlist), Audit trail]
---
# Skill: publishing-ops

## Tiêu chuẩn tham chiếu
- Approval-first: không upload công khai, không lên lịch khi chưa có quyết định gate ghi trong audit
- Upload private → kiểm (thumbnail, metadata, chapter hiện đúng) → chuyển scheduled; công khai chỉ theo lịch
- Idempotent: cùng video_id không upload hai lần (dùng platform_ref đã có)
- Rollback: unlist/private ngay, ghi `rolled_back` với lý do; không xoá
- Mọi hành động đăng có evidence (id nền tảng, thời điểm, người duyệt)

## Quy trình (làm đúng thứ tự)
Kiểm phê duyệt (`approved_by`) → kiểm gói đủ (final_video, thumbnail chosen, metadata, 0 preflight block) → upload private →
đặt metadata/thumbnail/chapter/playlist → đặt lịch theo `strategy` → phát `publish-events` scheduled → khi nền tảng công khai: `published`.

## Quy tắc
- Thiếu bất kỳ điều kiện nào → `failed` + evidence, không "đăng tạm".
- Lịch: khung giờ khán giả trong `strategy`; không có → 48h sau phê duyệt, giờ tròn.
- Reply bình luận: chỉ draft đã duyệt, bỏ `requires_human`; mỗi reply một event kind=reply.
- Lỗi quota/API → failed, không retry vô hạn (supervisor thấy qua audit).

## Checklist (supervisor và human gate dùng để chấm)
- [ ] approved_by có trong đầu vào
- [ ] Gói đủ, 0 preflight block
- [ ] Upload private trước, scheduled sau
- [ ] platform_ref và scheduled_at ghi trong event
- [ ] Không upload trùng

## Ví dụ tốt
`{"status":"scheduled","scheduled_at":"2026-09-05T12:00:00Z","platform_ref":"yt:abc123","evidence":"private upload ok; thumb A; 4 chapters; approved_by human:editor"}`

## Ví dụ xấu
Đăng public ngay vì "gate chắc sẽ duyệt"; sửa tiêu đề lúc upload.
