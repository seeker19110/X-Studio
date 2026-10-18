# ADR-0021 — Vận hành bền vững, sửa cảnh và bản trích dọc

Ngày: 2026-10-16. Trạng thái: chấp nhận. Bổ sung ADR-0002, 0004, 0009, 0012 và 0015.

## Quyết định

- Bảng ứng dụng dùng migration SQLite có transaction và phiên bản riêng trong `studio_meta`.
  Ý tưởng có revision, tombstone và import theo ID; chuyển thành dự án ghi command trong cùng transaction.
  Giữ dữ liệu `localStorage` cũ; chỉ ghi marker theo database sau khi server xác nhận import.
- Doctor mặc định chỉ đọc cấu hình/công cụ cục bộ. Smoke 12 giây là fixture tổng hợp do người vận hành yêu cầu,
  không gọi provider. “Đã cấu hình” không đồng nghĩa đã xác minh đăng nhập, mạng hay GPU.
- Backup tại checkpoint giữ lease và dừng writer website, dùng SQLite Online Backup API cùng media có SHA-256.
  Restore chỉ vào thư mục mới, kiểm checksum, rebase đường dẫn, dừng command/job cũ và ép manual/paused.
  Không mang token, lệnh provider hoặc session URI sang bản phục hồi. Upload chưa rõ kết quả cần đối chiếu.
- Upload operation lưu SHA-256, metadata, phiên gate, session và offset riêng trong SQLite trước khi gửi media.
  Restart hỏi offset Google; session 404 chỉ mở lại khi chưa thử chunk cuối; đã thử chunk cuối thì giữ để đối chiếu.
  Kết quả hoàn tất được tái dùng. Đổi file/metadata/gate
  giữ operation để người vận hành đối chiếu. Không hứa exactly-once khi session và kết quả ngoài hệ thống đều mất.
- Cache speech và segment website phải kiểm checksum; timing có duration hữu hạn và nằm trong audio.
- Sửa cảnh từ console là command có version dự kiến và ID thao tác ổn định. Code actor `console:editor`
  chỉ ghi script/cut-list được kiểm tra, không đóng giả agent. Mỗi lượt vô hiệu gate cũ, cập nhật script,
  tạo lại phần phụ thuộc và thu review mới; vẫn tối đa ba vòng sửa. Script do người sửa không bị planner ghi đè.
- Thư viện giữ catalog YAML tương thích, revision và ghi nguyên tử; upload là tên UUID trong vùng quản lý.
  File mới có quyền `unknown`; không tự suy ra quyền sở hữu. CC-BY cần nguồn và attribution. Thay tư liệu
  mang provenance catalog vào output; đường dẫn và checksum phải đúng.
- TTS trả word timing tùy chọn. Edge native và website dùng chung hợp đồng; timing sidecar buộc checksum audio.
  Thiếu timing dùng ước lượng, không giả là forced alignment. SRT không chồng cue hoặc vượt audio.
  Renderer khôi phục dấu câu từ narration khi toàn bộ token khớp word boundaries, không gán văn bản khác vào
  audio. Caption có giới hạn chiều rộng cấu hình, ngắt câu và khoảng đọc tối đa khoảng 4 giây; từ dài không bị
  bỏ khi phải chia nhiều trang. Bản ngang và dọc có thể dùng chiều rộng phụ đề riêng với cùng mốc giọng đọc.
- Contact sheet lấy tối đa 24 cảnh, report gắn manifest, checksum media và cấu hình reviewer.
  Adapter reviewer chỉ đọc nhận JSON và đường dẫn media; lỗi/đầu ra sai chặn nếu đã chọn adapter.
  `review_required` thiếu adapter cũng chặn. Mặc định yêu cầu người xem hình/nghe tiếng qua gate.
  Các reviewer agent hiện có không được quảng cáo là đã trực tiếp hiểu hình/tiếng.
- Gói review chỉ lấy thumbnail mới nhất của từng biến thể; lịch sử checksum cũ vẫn ở event log.
  Gói đính kèm thumbnail spec hiện hành và nêu rõ thumbnail độc lập với version của scene manifest
  (`manifest_version=0` là giá trị hiện có của thumbnail, không phải bản video cũ).
- Yêu cầu tạo dự án có thể mang `rights_dossier` của người vận hành. Dossier lưu trong `requested_video`
  của channel brief, được gói review gắn với đúng video/channel và nguồn event/actor; không lấy declaration
  của dự án khác qua blackboard dùng chung. Đây là dữ liệu để rights-checker quyết định, không tự cấp license.
  Gói quyền nhận cả script, manifest/prompt và thumbnail spec; gói chất lượng nhận brief/thời lượng mục tiêu.
- Các review lỗi của cùng script/final được gom thành một yêu cầu rework, giữ lý do của các nguồn review.
  Khóa bền theo event artifact ngăn review thứ hai tiêu thêm một retry sau restart; không tăng budget hoặc
  xóa số token đã dùng. Mỗi script/final mới vẫn được review và có khóa độc lập.
- Ledger ghi lượng công việc code thực hiện, cache hit và token backend báo cáo. Giá do người vận hành nhập;
  hạn mức media chỉ chặn theo ước tính đã biết trước generation, không phải trần hóa đơn hoặc subscription CLI.
  Chỉ dọn segment có dấu định danh của renderer, cần preview fingerprint còn đúng và xưởng đang dừng.
  TTS/ảnh trả phí, tư liệu người dùng và final không nằm trong lệnh dọn này.
- Snapshot gom event một lượt, overview bỏ chi tiết cảnh/script; API chi tiết và phân trang giữ khả năng truy xuất.
  Đo fixture 10/100/1.000 dự án trước/sau; chưa tăng worker hoặc chuyển database.
- Chọn nhánh Shorts trong đợt 4: người dùng chỉ định đoạn của final đã lưu, checksum/version nguồn phải khớp.
  Code FFmpeg căn mốc theo SRT, dựng 9:16 theo contain/cover, tạo brief/script/manifest mới cùng provenance.
  `console:shorts` là actor code được cấp quyền rõ ràng. Không sinh dữ kiện mới, không tự chọn nội dung thay model.
  Bản trích có ba review và gate publish mới; gate của nguồn độc lập. Outbox/receipt giữ retry không nhân đôi.
  Provenance ghi công cụ thật cho từng output (FFmpeg, SRT rebase, FFmpeg/Pillow poster), full SHA-256 của
  video/phụ đề nguồn, version nguồn, khoảng cắt và checksum output. Gói rights có chuỗi từ poster qua clip
  đến final/manifest/tư liệu nguồn. Audit cũ thiếu version được đối chiếu bằng checksum final nguồn đã lưu.
  Review lỗi giữ nguyên đoạn người dùng chọn, không tự viết lại narration hoặc dựng kịch bản khác.

## Hệ quả

SQLite tiếp tục là nguồn sự thật một máy, một worker. Backup cần dung lượng cho bản sao media.
Upload session là dữ liệu nhạy cảm nằm trong DB hoạt động, không xuất qua API/audit/backup.
Khôi phục giữ chính sách yêu cầu review nhưng phải cấu hình lại adapter/credential trên máy mới.

Chưa thêm forced alignment/model mới, analytics scheduler hay workflow GPU mới: đợt 4 chọn Shorts theo
nhánh sản phẩm trong nghiên cứu. Gateway failover và project scope blackboard tiếp tục theo hợp đồng hiện tại.
Không đổi prompt/skill agent; eval replay là kiểm tương thích, không phải đánh giá chất lượng model mới.
