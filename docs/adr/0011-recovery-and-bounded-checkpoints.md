# ADR-0011 — Khôi phục nhánh và checkpoint render tiết kiệm ổ đĩa

Ngày: 2026-10-11. Trạng thái: chấp nhận. Bổ sung ADR-0004, 0009, 0010.

## Vấn đề được tái hiện

Một nhánh model thành công nhưng nhánh kế tiếp lỗi tạm thời làm toàn bộ event chạy lại;
nhánh tạo manifest có thể sinh thêm công việc render. Render hết retry bị pause nhưng event
lại được đánh dấu đã xử lý nên resume không dựng lại. SEO correction ghi dấu hoàn tất
trước lời gọi có thể lỗi tạm thời, khiến lần sau bỏ qua bước sửa.

Bounded renderer lưu cả cache PCM và bản sao dùng để concat. QC bỏ qua exit code của
FFmpeg, có thể trình bày kết quả quét chưa đầy đủ như kết quả hợp lệ.

Audit bổ sung tái hiện: graph renderer ghi đè file cuối trước khi encoder thành công;
finishing lỗi có thể thay cả video/phụ đề/nhạc đã publish nhưng không ghi asset mới lên bus.
Thứ tự cảnh thiếu/trùng làm mất hoặc lặp narration; ID cảnh trùng trả tiền rồi ghi đè asset.
Manifest đánh dấu locked có thể tái dùng đường dẫn chưa đăng ký hoặc của video/cảnh khác.
Cut-list sai version hoặc đổi narration chỉ với regenerate_image làm manifest lệch file tiếng.
FFprobe trả JSON sai cấu trúc hoặc NaN/Infinity làm QC lỗi hoặc bỏ qua kiểm tra thời lượng.

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
- Graph encode vào file tạm rồi atomic replace sau thành công. Finalize dựng video,
  phụ đề và nhạc tại vùng staging, finishing xong mới thay file và publish asset.
  Video staging giữ cùng parent với output để bounded vẫn dùng cache segment cũ.
  Lỗi encode/finishing giữ nguyên gói đã publish và dọn file tạm.
- Manifest phải có cảnh với ID duy nhất; order không rỗng phải là hoán vị đầy đủ.
  Locked chỉ tái dùng asset có checksum/provenance đã đăng ký đúng video, cảnh và kind;
  replacement mới được code xác minh có thể tham gia kiểm tra trước khi publish.
  Repair buộc đúng video/version; thay lời đọc/prompt cần regenerate đúng thành phần.
- QC coi JSON/metric hỏng, giá trị không hữu hạn hoặc âm là không đo được.
  Bản cuối thiếu hình hoặc thời lượng dương có finding `block` dù có luồng tiếng.
- HTTP mở media rồi lấy kích thước từ cùng descriptor để giữ response nhất quán khi
  renderer thay file nguyên tử; hỗ trợ suffix range và trả kích thước trong lỗi 416.

## Giới hạn

Checkpoint nhánh không nằm trong cùng transaction với publish: hard kill giữa publish
và checkpoint vẫn có thể phát lại. Không tuyên bố exactly-once cho upload hoặc replies.
Không tự xóa cache PCM cũ hoặc cache không còn dùng. FLAC tiết kiệm bao nhiêu phụ thuộc
nội dung âm thanh; ổ đĩa vẫn tăng theo tổng thời lượng và phiên bản cảnh.

Commit video dùng atomic replace; nhiều file sidecar và publish bus chưa nằm trong một
transaction chung. Lỗi I/O hoặc hard kill giữa các lần replace/publish vẫn cần khôi phục;
staging không được diễn giải thành bảo đảm giao dịch cho toàn bộ gói xuất bản.

Benchmark offline tại `scripts/benchmark-bounded.py` đo fixture tổng hợp, cold/warm run,
RSS, dung lượng segment, timing và QC. Kết quả trong VALIDATION.md không chứng minh
render 12 giờ, Windows, chất lượng provider hay upload thật.
