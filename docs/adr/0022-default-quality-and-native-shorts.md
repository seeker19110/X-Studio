# ADR-0022: Chất lượng mặc định và Shorts dàn lại từ tư liệu gốc

Ngày: 2026-10-16. Trạng thái: chấp nhận. Bổ sung ADR-0004, 0009, 0012 và 0016.

## Bối cảnh

Lượt xem thử cần xuất lại bằng một script riêng mới có hình Full HD, bố cục dọc rõ chữ và phụ đề dễ đọc.
Quy trình mặc định vẫn cắt từ MP4 ngang đã burn chữ, nên Shorts thu nhỏ cả hình lẫn phụ đề. Người vận hành
yêu cầu đưa các cải thiện vào quy trình thường ngày rồi thử dự án mới bằng model thật, chưa đăng YouTube.

## Quyết định

- Dùng `video.layout: editorial` mặc định cho cảnh ảnh. Code ghép khung native theo tỷ lệ, dùng working title,
  heading/narration của model và ảnh nguồn. Không gắn kịch bản, nhãn bước hay hình của bài thử cũ vào dự án mới.
  `full_bleed` là lựa chọn giữ kiểu trước đây. Clip chuyển động tiếp tục dùng footage; không thay bằng ảnh tĩnh.
- Font và khoảng an toàn được dùng chung cho bố cục và ASS. Không zoom/pan cả khung editorial vì sẽ cắt chữ.
  Phụ đề có dấu câu khi token khớp lời đọc, chiều rộng riêng cho ngang/dọc, tối đa hai dòng; cân dòng tránh
  từ đứng lẻ và tách một số cụm Việt thông dụng. Khi giới hạn thời gian tách riêng từ kết câu, chuyển hai từ
  đứng trước sang cue kế tiếp, giữ nguyên mốc từ. Trường hợp không có timing vẫn ghi theo ước lượng.
- Chỉ rút khoảng lặng cuối khi có word timing hợp lệ: giữ đến từ cuối + 0,28 giây và không vượt audio gốc.
  Audio gốc được giữ và đăng ký provenance; bản WAV dẫn xuất có checksum riêng. Không đổi tốc độ hoặc nội dung
  giọng đọc để làm vừa thời lượng. Hạn mức generation chỉ ghi lần gọi TTS thực tế, không ghi thêm một generation.
- Renderer giữ ảnh/audio gốc, cache khung dàn riêng theo tỷ lệ, font, nội dung và checksum nguồn. Audit ghi
  recipe, source/output SHA-256 và heading/title thật; inspection và gói review nhận dữ liệu khung này.
  Mỗi khung có media asset `scene_layout` riêng, kế thừa license/nguồn/ghi công của ảnh và ghi đúng producer
  `studio:editorial-layout-v1`; không tự nâng quyền của nguồn chưa rõ. ID ổn định chống ghi trùng khi resume.
- Profile backup/restore giữ bố cục, chiều rộng phụ đề ngang/dọc, tùy chọn rút đuôi, chuyển cảnh và hướng dẫn
  chất lượng. Việc phục hồi không làm mất các giá trị người vận hành đã chọn; command/credential vẫn bị loại.
  Hai lần thử Windows phát hiện khóa tạm lúc đổi tên thư mục. Backup/restore thử lại lỗi chia sẻ/quyền Windows
  5/32 tối đa sáu lần trong khoảng 1,55 giây; lỗi kéo dài vẫn dừng và không ghi đè đích.
- Shorts mặc định `fit: reflow`: kiểm checksum tư liệu ảnh/audio của đúng nguồn, dàn lại các cảnh liên quan
  theo 9:16 rồi cắt theo cue đã chọn. Tái dùng tiếng, không gọi lại TTS/ảnh/model sáng tạo để đổi lời hay tư liệu.
  Phụ đề dọc được tạo lại từ word timing gốc, chỉ lấy từ trong đoạn chọn; thiếu timing dùng cue ước lượng.
  Chỉ dựng các cảnh giao đoạn chọn, dùng assembler bounded khi xưởng đã chọn bounded.
- Nguồn cũ thiếu tư liệu hoặc nguồn là footage phải chọn rõ `contain`/`cover`; không lặng lẽ cho ra bản thu nhỏ.
  Nguồn đã đổi checksum bị từ chối ngay cả khi MP4 cuối còn nguyên. Receipt chống trùng có cả recipe/context nguồn.
  Hook/CTA của script dẫn xuất lấy đoạn thực sự có trong clip, không gán cả narration vào hook.
- Bản dẫn xuất tiếp tục có fact/quality/rights review và gate publish riêng. Gói quyền giữ chuỗi từ khung dàn,
  clip, poster đến nguồn; gói quality nhận cả derivation để phân biệt một container clip với số hình bên trong.
  Không tạo phê duyệt của người dùng hoặc ngoại lệ đăng thử.

## Hệ quả

Ảnh được xử lý ở độ phân giải xuất, không phải phóng to bản MP4 360p. Dàn lại Shorts tốn CPU/disk nhưng không
tiêu thêm generation ảnh/giọng đọc. Cần giữ tư liệu gốc để dàn lại; chỉ có MP4 thì vẫn còn hai lựa chọn cũ.
Schema media-assets thêm kind `scene_layout` tương thích bản cũ; không đổi prompt/skill agent. Hướng dẫn chất lượng vào channel brief, tôn trọng các giới hạn
người vận hành đã khai. Model quyết định nội dung; code chỉ dàn, đo và thực hiện. Reviewer JSON không được gọi
là đã trực tiếp nghe/xem media. Các bản cũ và lịch sử chi phí/review được giữ nguyên.
