# ADR-0013: Phiên upload YouTube bền vững và đối soát khi kết quả không rõ

Trạng thái: Accepted · Ngày: 2026-10-11

## Bối cảnh

PR #6 (được merge bởi bên khác) đã chuyển upload sang chunk giới hạn 8 MiB,
nhưng Location và offset chỉ tồn tại trong RAM. Tiến trình chết sau khi YouTube
nhận video, trước audit `platform.upload`, có thể gây upload trùng khi chạy lại.
Ngoài ra upload private không có lịch bị gán trạng thái `published` từ model.

## Quyết định

- Giữ upload private và các gate duyệt hiện có. Không thêm thao tác chuyển public.
  Nếu adapter chưa xác nhận public/scheduled và không đặt lịch thành công, phát
  `failed` với bằng chứng trạng thái chưa xác nhận, giữ platform_ref để xử lý tiếp.
- Nhật ký upload riêng theo video nội bộ, nằm cạnh tokenstore, không đưa Location
  vào bus, evidence, artifact hay thông báo lỗi. Đây là trạng thái vận hành nhạy cảm,
  không phải tài liệu để chia sẻ hoặc đưa vào Git.
- Danh tính gồm SHA-256 nội dung file, request metadata/privacy đầy đủ, tài khoản
  OAuth và kênh trả về từ API. Thay đổi danh tính không được âm thầm tạo phiên mới.
- Khóa SQLite riêng theo journal/video, giữ qua toàn bộ lượt upload, ngăn hai
  factory dùng cùng tokenstore tạo phiên đồng thời. Khóa tự nhả khi tiến trình chết.
  Ghi file tạm 0600 rồi replace nguyên tử; fsync file và thư mục trên POSIX.
  Không dựa vào lease của bus vốn chỉ bao phủ một factory DB.
- Ghi intent trước POST khởi tạo, Location trước PUT media, kết quả hoàn tất trước
  trả về orchestrator. Khi restart, hỏi server bằng PUT rỗng `bytes */size`; dùng
  offset server xác nhận hoặc kết quả hoàn tất được trả lại.
- Phiên hết hạn, intent mất Location, danh tính lệch hoặc journal không đọc được
  phải dừng để đối soát. Không tự xóa journal rồi POST lại, kể cả đã chờ lâu.
- Audit upload cũ của orchestrator không thay thế việc xác minh danh tính qua
  adapter. Khi đã có receipt cũ nhưng thiếu journal tương ứng, không tự upload lại.

Giao thức đối chiếu: [YouTube resumable uploads](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol).

## Hệ quả và giới hạn

Phục hồi dựa vào journal còn nguyên và phản hồi session từ YouTube. API không
cung cấp khóa idempotency của ứng dụng cho `videos.insert`; session hết hạn không
cho phép khẳng định upload cuối đã tạo video hay chưa. Cần người kiểm tra YouTube
Studio và đối soát platform_ref khi trạng thái mơ hồ; không tuyên bố exactly-once
cho mọi lỗi phía ngoài.

Không sửa credential đang có, không đăng nhập/upload thật trong kiểm chứng. Test
transport giả kiểm tra protocol và trạng thái local, không xác minh hành vi dịch vụ
live, quota, quyền OAuth thực tế hoặc xử lý/transcode phía YouTube.

Lịch sử `published` sai từ phiên bản trước không được tự sửa: receipt cũ thiếu
trạng thái nền tảng nên không thể phân biệt chắc chắn với video đã public thật.
Người vận hành cần đối soát lịch sử này với YouTube Studio; bản mới ngăn tạo thêm
event sai, không suy diễn hoặc tự công khai video để khớp lịch sử.
