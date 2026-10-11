# ADR-0011 — Khôi phục nhánh và checkpoint render tiết kiệm ổ đĩa

Ngày: 2026-10-11. Trạng thái: chấp nhận. Bổ sung ADR-0004, 0009, 0010.

## Vấn đề được tái hiện

Một nhánh model thành công nhưng nhánh kế tiếp lỗi tạm thời làm toàn bộ event chạy lại;
nhánh tạo manifest có thể sinh thêm công việc render. Render hết retry bị pause nhưng event
lại được đánh dấu đã xử lý nên resume không dựng lại. SEO correction ghi dấu hoàn tất
trước lời gọi có thể lỗi tạm thời, khiến lần sau bỏ qua bước sửa.

Bounded renderer lưu cả cache PCM và bản sao dùng để concat. QC bỏ qua exit code của
FFmpeg, có thể trình bày kết quả quét chưa đầy đủ như kết quả hợp lệ.

## Quyết định

- Ghi checkpoint bền vững theo event/agent/topic sau khi nhánh thành công; retry bỏ qua
  nhánh đó. Reassign review có chủ đích vẫn được chạy lại. Đánh dấu SEO retry sau khi
  lời gọi hết trạng thái transient. Render hết retry giữ event deferred dưới trạng thái
  pause; resume thử lại event đó.
- Segment mới dùng FLAC lossless trong MKV và concat đọc thẳng blob đã kiểm checksum.
  Không tạo bản sao `scene-*.mkv`. Cache mặc định vẫn copy tài nguyên đưa cho editor để
  sửa cảnh không làm hỏng blob dùng chung. Đổi phiên bản khóa segment để tách cache PCM cũ.
- Đặt số luồng decoder cho từng input, giữ encoder/filter có giới hạn. Chặn duration
  không hữu hạn hoặc không dương trước khi encode.
- QC yêu cầu FFmpeg thành công và đủ tổng kết loudness/peak; thiếu công cụ, lỗi,
  timeout hoặc log bị cắt tạo finding `block`. Giảm log từng frame, giữ log phát hiện
  hình đen/khoảng lặng. Đây là đo file, không thay ba review nội dung/bản quyền/chất lượng.
- HTTP mở media rồi lấy kích thước từ cùng descriptor để giữ response nhất quán khi
  renderer thay file nguyên tử; hỗ trợ suffix range và trả kích thước trong lỗi 416.

## Giới hạn

Checkpoint nhánh không nằm trong cùng transaction với publish: hard kill giữa publish
và checkpoint vẫn có thể phát lại. Không tuyên bố exactly-once hay tự retry upload.
Không tự xóa cache PCM cũ hoặc cache không còn dùng. FLAC tiết kiệm bao nhiêu phụ thuộc
nội dung âm thanh; ổ đĩa vẫn tăng theo tổng thời lượng và phiên bản cảnh.

Benchmark offline tại `scripts/benchmark-bounded.py` đo fixture tổng hợp, cold/warm run,
RSS, dung lượng segment, timing và QC. Kết quả trong VALIDATION.md không chứng minh
render 12 giờ, Windows, chất lượng provider hay upload thật.
