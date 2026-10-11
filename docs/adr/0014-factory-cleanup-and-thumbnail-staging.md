# ADR-0014 — Thu hồi tài nguyên xưởng và giữ ảnh bìa khi dựng lỗi

Ngày: 2026-10-11. Bổ sung ADR-0011/0012.

## Lỗi đã tái hiện

- `finish_thumbnail` ghi thẳng lên JPEG đã có. Lỗi ở lần nén thứ nhất, thứ hai hoặc thứ ba để lại tệp dở thay cho ảnh đã duyệt.
- Khi dựng A/B, A có thể ghi đè thành công rồi B lỗi. Bus vẫn giữ checksum của cả bộ trước, nhưng các tệp đã đổi.
- Graph giới hạn encoder/complex filter nhưng không đặt `-threads` cho từng decoder. Bước ảnh bìa và hoàn thiện phụ đề/nhạc cũng thiếu giới hạn đầu vào hoặc filter. Cấu hình một encoder không tự giới hạn các input tiếp theo.
- Factory chỉ dọn bus/lease trong một phần khởi tạo. Lỗi ở bus, command store, autopilot, settings đã lưu hoặc snapshot; Ctrl+C khi khởi tạo; và lỗi lúc đóng bus có thể để lại khóa của tiến trình còn sống.

24 regression cases được thêm trước code. 23 ca đỏ đúng các lỗi trên; ca lỗi client thông thường xác nhận hành vi đã được bảo vệ. Sau sửa, 24 ca đạt. Mốc Studio trước sửa: 951 ca, 100% độ phủ dòng; sau thêm: 975 ca, 64 tệp Python trong tests gồm conftest.

## Quyết định

1. FFmpeg hoàn thiện ảnh bìa trong thư mục tạm cùng filesystem. Chỉ replace đích sau khi encoder và các lần nén hoàn tất; dọn staging cả khi lỗi.
2. Renderer dựng đủ các biến thể vào staging trước khi replace bất kỳ JPEG đã phát nào. Sau commit, cập nhật đường dẫn asset, giữ checksum/provenance và cơ chế nhật ký `render.prepared` hiện có. Ảnh nền đã sinh vẫn ở cache; retry dùng lại kết quả đã trả phí.
3. Giới hạn filter đơn và complex tại `_run` chung theo `render.threads`. Đặt decoder threads riêng trước mỗi input trong graph, ảnh bìa và bước hoàn thiện phụ đề/nhạc; đặt output threads cho JPEG và encode cuối. Bounded tiếp tục dựng từng cảnh như trước.
4. `ExitStack` bảo vệ toàn bộ khởi tạo Factory. Đăng ký release lease sau acquire thành công, rồi đóng bus sau khi tạo thành công. Chỉ giữ tài nguyên khi snapshot đầu đã dựng xong. Cơ chế dọn áp dụng cả exception lẫn KeyboardInterrupt, không nuốt lỗi khởi tạo. Nếu bước dọn cũng lỗi, chuỗi exception giữ ngữ cảnh để đối soát. Khi đóng Factory, release lease nằm trong finally của bus.close.

Không đổi scene manifest, prompt, provider, gate hay quyền đăng công khai. Kiểm chứng dùng fixture, sandbox runner và FFmpeg/SQLite thật; không gọi provider trả phí hoặc YouTube live.

## Giới hạn

Staging cả bộ bảo vệ lỗi sinh/nén trước commit; nhiều lần filesystem replace và publish SQLite vẫn không thuộc một transaction chung. Hard kill hoặc lỗi filesystem giữa các lần replace cần đối soát; ADR này không cam kết rollback nguyên tử toàn bộ batch trong tình huống đó.

Giới hạn luồng xử lý không phải quota RAM/CPU. Graph vẫn mở đầu vào theo số cảnh; video dài nên dùng bounded. Không suy ra phần trăm tiết kiệm hay khả năng dựng 12 giờ từ các regression test.

Không tự xóa settings hỏng để khởi động bằng cấu hình đoán. Lỗi được báo và tài nguyên được thu hồi để người vận hành sửa đúng dữ liệu/cấu hình.

Kết quả kiểm chứng cuối ghi tại [VALIDATION.md](../VALIDATION.md).
