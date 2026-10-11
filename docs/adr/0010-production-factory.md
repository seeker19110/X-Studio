# ADR-0010 — Xưởng video bền vững, dựng theo cảnh, tự động hóa có chính sách

Ngày: 2026-10-11. Trạng thái: chấp nhận. Bổ sung ADR-0002, 0004, 0007, 0009.

## Vấn đề

Pipeline v2 đã có bus SQLite, 14 vai chuyên môn, ba review và YouTube adapter. Tuy nhiên một graph FFmpeg chứa toàn bộ cảnh tăng tài nguyên theo độ dài; render lỗi có thể gọi lại API đã trả tiền; kịch bản dài dễ vượt context; vận hành chỉ bằng CLI. Gate bắt buộc con người ở mỗi kế hoạch không phù hợp yêu cầu chủ repo về một xưởng tự động.

## Quyết định

- Giữ lõi event-driven, ACL, adapter và bus đã có kiểm thử. Thêm `Factory` là một worker có lease, hàng lệnh SQLite, snapshot chỉ đọc và console HTTP local. Không đưa Redis, Node hay dịch vụ DB thứ hai vào runtime.
- Mỗi thành phần TTS/ảnh/thumbnail có checkpoint theo nội dung + cấu hình. Blob có checksum; metadata commit cuối cùng bằng atomic replace. Retry sửa cảnh dùng fingerprint của cut-list; không sửa manifest gốc và không trả tiền lần hai cho thành phần đã thành công.
- `render.mode=bounded` mã hóa từng cảnh, PCM trung gian để tránh AAC priming tích lũy, làm tròn độ dài theo frame, ghép H.264 bằng stream copy. Giữ `graph` tương thích và cho crossfade chồng cảnh. Bounded dùng fade ngắn trong cảnh, không crossfade chồng tiếng.
- Chia tác vụ kịch bản dài thành chương tối đa 8 phút theo mặc định; chia scene planning và fact review theo nhóm section. Claim ID được đổi đồng bộ. Verdict ghép lấy mức nghiêm trọng nhất. Checkpoint theo prompt/cấu hình; token ghi nhận ngay từng chương, dừng gọi chương mới khi ngân sách đã hết.
- Tư liệu local có catalog được khai báo giấy phép, nguồn và attribution. `scene_video` và `music` đi qua cùng media-assets. Không tự tải stock không rõ quyền. Chữ, trích dẫn, biểu đồ chạy qua schema khai báo và FFmpeg; không thực thi code do model viết.
- Caption chia trang có thời gian tỷ lệ theo lời đọc; SRT luôn riêng, tùy chọn burn-in. Đây không phải forced alignment. Nhạc loop và duck dưới lời đọc, chuẩn hóa loudness.
- Ba chế độ: `manual`, `production` (tự duyệt kế hoạch), `publish` (tự duyệt cả xuất bản nếu kiểm tra đạt). Mọi hành động công khai vẫn qua gate. Policy dùng danh tính riêng `policy:studio`, chữ ký HMAC và thế hệ gate; không giả chữ ký con người. Replies/escalation vẫn cần người xử lý.
- Tự xuất bản cần ba review, provenance và checksum đúng, metadata preflight, QC file thật, thời lượng trong mục tiêu/giới hạn và ghi công CC-BY trong mô tả. Mặc định cả warning QC cũng giữ lại; fake media luôn bị giữ. Gate đang giữ chỉ đánh giá lại tối đa mỗi 30 giây để tránh quét video dài liên tục.
- Console chỉ bind loopback; mã truy cập, HttpOnly/SameSite cookie, Host/Origin check và header chống CSRF; chỉ stream file đã đăng ký dưới output. Model không có quyền đổi cấu hình xưởng hay ký policy.

## Hệ quả và giới hạn

RAM của render bounded phụ thuộc một cảnh và decoder, không phải toàn bộ graph. Thời gian, dung lượng ổ đĩa và tổng chi phí vẫn tăng theo độ dài. Checkpoint PCM tốn khoảng 0,69 GB âm thanh mỗi giờ mỗi bản sao, chưa kể video; hiện cache và bản dùng để concat là hai bản sao. Không tự xóa cache đang dùng.

Burn-in phụ đề hiện cần thêm một lượt encode video cuối. Cơ chế này đơn giản và đã đo được nhưng chưa tối ưu như burn-in tại từng segment. Có thể tắt burn-in và dùng SRT để tiết kiệm CPU. Một tiến trình phục vụ một workspace, không phải cụm phân tán.

720 phút là giới hạn yêu cầu đầu vào, chưa phải chứng nhận benchmark 12 giờ. Timeout render/QC phải phù hợp máy và thời lượng. Ngân sách token không bao gồm phí TTS/ảnh; một lời gọi model đang chạy có thể vượt phần ngân sách còn lại. UI đợi bước hiện tại kết thúc khi tạm dừng. Kiểm thử offline, FFmpeg và HTTP không chứng minh chất lượng sáng tạo của một model thật.
