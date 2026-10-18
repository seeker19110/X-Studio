# Quy trình sản xuất video X-Studio

Playbook này đi cùng **Quy trình chuẩn** trên bảng điều khiển: tám bước từ ý tưởng đến đo lường.
Mục tiêu là biết đầu ra cần có, ai kiểm tra và việc tiếp theo của từng video.
Một người có thể đảm nhận nhiều vai; người chịu trách nhiệm cuối cùng là chủ xưởng/biên tập.

## Bắt đầu và theo dõi công việc

1. Vào **Kho ý tưởng**, ghi chủ đề, khán giả, hook, định dạng và ghi chú.
2. Chọn **Phát triển thành video**, hoàn thiện brief rồi gửi yêu cầu tạo dự án.
3. Chờ dự án xuất hiện trong **Dự án video**; mở dự án để đọc kịch bản, xem cảnh và kết quả kiểm tra.
4. Dùng **Tổng quan** để thấy quyết định đang chờ; vào **Duyệt & xuất bản** khi có yêu cầu duyệt.
5. Nếu dự án cần xử lý, đọc finding và **Hoạt động** trước khi sửa hoặc thử lại.

Kho ý tưởng lưu trong SQLite của xưởng, dùng chung giữa các trình duyệt kết nối cùng xưởng, tối đa 200 ý tưởng.
Dữ liệu `localStorage` cũ được import theo ID sau khi server xác nhận; bản cũ vẫn giữ để phục hồi khi cần.
Hai tab sửa cùng lúc sẽ báo xung đột revision và giữ nội dung đang nhập. Xem [hướng dẫn vận hành](OPERATIONS-UPGRADES.md).
Sau khi yêu cầu tạo dự án thành công, hồ sơ dự án và tiến độ được lưu trong SQLite của xưởng.
Chỉ có mã yêu cầu/nhãn đang xử lý chưa đồng nghĩa dự án đã được tạo thành công.

Tám bước là cách tổ chức SOP; thanh tiến độ suy ra từ trạng thái và tài nguyên đã lưu.
Không coi việc chuyển bước trên giao diện là bằng chứng một checklist đã được con người duyệt.
Demo dùng provider và dữ liệu mô phỏng; tệp media và số liệu demo không xác minh chất lượng sản phẩm thật.

## 01 · Ý tưởng & brief

**Vai:** bạn/chủ kênh đề xuất; biên tập xác định phạm vi.
**Đầu ra:** brief gồm khán giả, nhu cầu, góc kể, thông điệp, định dạng, thời lượng, ngôn ngữ và giới hạn nội dung.

- [ ] Một video trả lời một câu hỏi hoặc giải quyết một nhu cầu cụ thể.
- [ ] Viết rõ điều người xem nhận được sau khi xem.
- [ ] Chọn `long` 16:9 hoặc `short` 9:16; thời lượng là mục tiêu để đối chiếu khi dựng.
- [ ] Ghi yêu cầu thương hiệu, điều cần tránh và ngân sách token cho text model.

**Video dài:** một câu hỏi trung tâm, các chương hỗ trợ, ví dụ và lời hứa rõ với người xem.
**Video ngắn:** một thông điệp, một kết quả đáng xem; xác định hình ảnh mở đầu trong khung dọc.
**Thao tác:** tạo dự án từ ý tưởng hoặc tạo trực tiếp; kiểm tra brief tại tab **Tổng quan**.
**Điểm kiểm soát:** kế hoạch sản xuất đi qua gate `plan`; policy có thể duyệt trong chế độ đã cấu hình.

## 02 · Nghiên cứu & nguồn

**Vai:** nghiên cứu tìm bằng chứng; kiểm chứng đọc và đánh giá các luận điểm.
**Đầu ra:** hồ sơ nguồn và danh sách claim, gồm URL, tên nguồn, ngày truy cập và điều cần kiểm chứng.

- [ ] Luận điểm quan trọng có nguồn có thể truy cập và đối chiếu.
- [ ] Ưu tiên tài liệu gốc; kiểm tra ngày dữ liệu khi thông tin có thể thay đổi.
- [ ] Phân biệt dữ kiện, suy luận, quan điểm và phần chưa chắc chắn.
- [ ] Ghi mâu thuẫn giữa các nguồn; sửa hoặc bỏ claim chưa đủ bằng chứng.

**Video dài:** tổ chức nguồn theo chương; đối chiếu nhiều nguồn cho nhận định quan trọng.
**Video ngắn:** kiểm tra dữ kiện chính và ngữ cảnh; sự ngắn gọn không thay thế tính chính xác.
**Thao tác:** đọc **Nguồn nghiên cứu & kiểm chứng** trong tab **Kịch bản**.
**Điểm kiểm soát:** review nội dung phải đạt trước xuất bản; nguồn đã khai báo vẫn cần được đọc.

## 03 · Kịch bản & hook

**Vai:** biên kịch soạn lời; biên tập kiểm tra câu chuyện và cách diễn đạt.
**Đầu ra:** hook → nội dung → kết quả/lời đáp → CTA, kèm ghi chú hình ảnh và thời lượng ước lượng.

- [ ] Hook nêu lý do ở lại và đúng với nội dung video thực sự cung cấp.
- [ ] Đọc thành tiếng: sửa câu khó nói, từ mơ hồ và đoạn lặp.
- [ ] Các luận điểm nối mạch lạc, có ví dụ phù hợp đối tượng.
- [ ] CTA phục vụ mục tiêu brief; kiểm tra claim và tên riêng trước khi tạo lời đọc.

**Video dài:** chia chương, có chuyển ý và nhịp nghỉ; kết luận đáp lại câu hỏi mở đầu.
**Video ngắn:** vào thẳng vấn đề trong những giây đầu; một ý chính và kết thúc rõ.
**Thao tác:** đọc hook, các phần, CTA, nguồn và phiên bản trong tab **Kịch bản**.
**Kiểm tra biên tập 1:** xem lại brief + kịch bản; hiện không có gate/nút duyệt kịch bản riêng.

## 04 · Storyboard & tư liệu

**Vai:** sản xuất điều phối; hình ảnh và âm thanh chuẩn bị tài nguyên.
**Đầu ra:** danh sách cảnh có lời đọc, hình/footage, chữ, thời lượng, voice, motion và hồ sơ quyền sử dụng.

- [ ] Mỗi cảnh có mục đích rõ và hình ảnh hỗ trợ đúng lời đọc.
- [ ] Giọng, phong cách, font và tỷ lệ khung hình nhất quán.
- [ ] Lưu nguồn, giấy phép/quyền sử dụng và credit của ảnh, footage, nhạc, font.
- [ ] Với đồ họa dữ liệu, ghi nguồn số liệu và kiểm tra nhãn/giá trị.
- [ ] Nghe lời đọc; sửa phát âm sai, tên riêng và khoảng nghỉ trước khi khóa cảnh.

**Video dài:** thiết kế theo chương; tận dụng cache và khóa cảnh đã đạt để giảm công làm lại.
**Video ngắn:** bố cục 9:16; chữ đủ rõ trên điện thoại, tránh vùng giao diện che nội dung.
**Thao tác:** xem/nghe từng cảnh và đọc nguồn tại **Cảnh & tư liệu**; cập nhật lựa chọn tư liệu theo cấu hình hỗ trợ.
**Điểm kiểm soát:** quyền sử dụng chưa rõ phải được xử lý; không suy ra quyền từ việc tải được tài nguyên.

## 05 · Dựng & phụ đề

**Vai:** dựng phim tạo bản nháp; hậu kỳ xử lý tiếng, hình và caption.
**Đầu ra:** bản nháp, bản cuối MP4 và SRT; danh sách chỉnh sửa chỉ rõ cảnh/vị trí cần sửa.

- [ ] Lời đọc khớp hình; chuyển cảnh và khoảng nghỉ giúp theo dõi câu chuyện.
- [ ] Nhạc không lấn lời; nghe cả bằng loa điện thoại và tai nghe khi có thể.
- [ ] Phụ đề đúng nội dung, dấu tiếng Việt, không tràn và có thời gian đọc hợp lý.
- [ ] Xem trọn bản dựng; ghi timestamp, lỗi và hướng sửa cụ thể.

**Video dài:** kiểm tra tính liên tục, chuyển chương, nhịp kể và mốc chapter.
**Video ngắn:** bỏ đoạn dư; xem preview dọc ở kích thước điện thoại, chú ý nhịp mở đầu.
**Thao tác:** xem/tải MP4 và SRT tại **Tổng quan**; sửa theo cảnh để tận dụng tài nguyên đã có.
**Kiểm tra biên tập 2:** người phụ trách xem bản dựng và xác nhận các sửa đổi đã được xử lý.
Phụ đề hiện chia thời gian theo lời đọc trong cảnh; chưa có forced alignment từng từ/Whisper trong pipeline.

## 06 · Kiểm tra chất lượng

**Vai:** kiểm chứng nội dung, kiểm tra bản quyền và QC kỹ thuật.
**Đầu ra:** review `fact`, `rights`, `quality` và kết quả đo file thật.

- [ ] Cả ba review đạt; đọc finding thay vì chỉ nhìn tổng số review.
- [ ] Có bản cuối hợp lệ; kiểm tra file, codec, thời lượng, hình và tiếng theo kết quả QC.
- [ ] Đối chiếu thời lượng thực với mục tiêu; xem lỗi hình/tiếng tại vị trí được ghi nhận.
- [ ] Không còn tài nguyên thiếu hồ sơ quyền; credit cần thiết có trong mô tả.
- [ ] Tiêu đề/thumbnail không hứa điều video không cung cấp.

**Video dài:** kiểm tra toàn bộ file, đoạn nối chương và credits của các tài nguyên đã sử dụng.
**Video ngắn:** kiểm tra tỷ lệ 9:16, caption, cắt khung và độ rõ của chữ trên điện thoại.
**Thao tác:** đọc từng review và **Kiểm tra file thực tế** ở tab **Kiểm tra & đăng**.
**Điểm kiểm soát:** khi review hoặc QC chưa đạt, xử lý finding rồi kiểm tra lại trước gate `publish`.

## 07 · Đóng gói & xuất bản

**Vai:** SEO chuẩn bị gói đăng; chủ xưởng quyết định; adapter nền tảng thực hiện.
**Đầu ra:** tiêu đề, mô tả, thumbnail, tags, phụ đề, credits và trạng thái/URL do adapter trả về.

- [ ] Đúng kênh, định dạng, phiên bản bản cuối và nội dung metadata.
- [ ] Thumbnail rõ khi thu nhỏ; title/mô tả phản ánh đúng video.
- [ ] Credits đầy đủ; chapters hợp lệ nếu có; kiểm tra thời điểm đăng và múi giờ.
- [ ] Xem bản cuối và checklist trước quyết định đăng/lên lịch.
- [ ] Sau đăng, đối chiếu URL và trạng thái nền tảng; xử lý lỗi upload thay vì tạo bài trùng.

**Video dài:** mô tả có cấu trúc; chapters giúp tìm đoạn; thumbnail có điểm tập trung rõ.
**Video ngắn:** kiểm tra tên, khung dọc và nội dung hiển thị; mỗi nền tảng cần gói đăng phù hợp.
**Thao tác:** dùng **Duyệt & xuất bản** hoặc nút đi đến cửa duyệt trong **Kiểm tra & đăng**.
**Kiểm tra biên tập 3:** rà bản cuối + gói đăng; hành động công khai phải qua gate `publish`/policy được cấu hình.
Adapter thật hiện có là YouTube; TikTok/Reels cần adapter riêng, chưa được nối vào xưởng.

## 08 · Đo lường & cải tiến

**Vai:** phân tích đọc dữ liệu; biên tập chọn thay đổi cho video tiếp theo.
**Đầu ra:** snapshot nền tảng, nhận xét có bằng chứng và một thử nghiệm cụ thể cho vòng sau.

- [ ] Kiểm tra nguồn dữ liệu, khoảng đo và thời điểm đồng bộ trước khi so sánh.
- [ ] So với baseline của kênh và video cùng định dạng/độ dài; tránh kết luận từ một mẫu nhỏ.
- [ ] Video dài: xem CTR, thời lượng xem trung bình và retention theo đoạn/chương khi dữ liệu có sẵn.
- [ ] Video ngắn: xem đoạn mở đầu, thời lượng xem và tỷ lệ xem hết khi nền tảng cung cấp.
- [ ] Ghi một thay đổi có thể kiểm tra: hook, cấu trúc, nhịp dựng, thumbnail hoặc cách chọn chủ đề.

**Thao tác:** đồng bộ dữ liệu YouTube theo CLI hiện có; đọc tab **Số liệu** và đối chiếu YouTube Studio khi cần.
**Nhịp xem khuyến nghị:** sau 24 giờ, 7 ngày và 28 ngày; đây là lịch xem thủ công, chưa có automation theo SOP này.
Chưa có snapshot phải hiểu là **chưa đồng bộ**; demo phải hiểu là dữ liệu mô phỏng.
Một số trường có thể bằng 0 khi API chưa cung cấp/thiếu quyền; số 0 không đủ để kết luận hiệu suất thật bằng 0.
Không có retention/tỷ lệ xem hết thì ghi chưa có dữ liệu; không tự suy ra số đẹp hoặc cam kết mức tăng trưởng.

## Điểm duyệt, định dạng và giới hạn vận hành

Ba kiểm tra biên tập nêu trên là SOP khuyến nghị, không phải ba gate mới trong phần mềm.
Hai gate sản xuất/xuất bản hiện có là `plan` và `publish`; `replies`/`escalation` xử lý việc ngoài chuỗi này.
`manual` chờ người duyệt kế hoạch và đăng; `production` có thể tự duyệt kế hoạch hợp lệ, chờ duyệt đăng.
`publish` có thể duyệt qua policy đã cấu hình khi đủ điều kiện; đọc chế độ xưởng trước khi bắt đầu.
Nếu cần quyết định biên tập của con người, dùng chế độ phù hợp và theo dõi các yêu cầu đang chờ.

Video dài 16:9 và video ngắn 9:16 là các dự án sản xuất độc lập trong workflow hiện tại.
Chưa có tự động cắt video dài thành Shorts; muốn tái sử dụng, chọn đoạn/viết lại brief và tạo dự án ngắn riêng.
Token trong brief là ngân sách text model, không phải trần tiền tổng cho TTS/ảnh/render.
Cache giúp tái sử dụng phần hợp lệ; hiệu quả thời gian/chi phí cần đo trên cấu hình và video thực tế.
Chưa có cam kết chất lượng, phần trăm tiết kiệm hoặc benchmark video nhiều giờ từ playbook này.

## Nguồn thiết kế và quyết định sử dụng

Đã đối chiếu README/tài liệu chính chủ ngày 11/10/2026; kiểm tra license của phiên bản cụ thể trước khi tích hợp.
X-Studio dùng Python + FFmpeg + SQLite. Các repo còn lại dưới đây là tham khảo, chưa được cài/kết nối vào pipeline.

| Nguồn | Điều học hỏi/ứng dụng | Điều kiện thực tế |
|---|---|---|
| [FFmpeg](https://github.com/FFmpeg/FFmpeg) | Dựng, xử lý tiếng/phụ đề; ffprobe đo file. [Filters](https://ffmpeg.org/ffmpeg-filters.html) có loudnorm và phát hiện đen/im lặng. | Engine hiện có; chủ yếu LGPL, thành phần tùy chọn có GPL. |
| [Remotion](https://github.com/remotion-dev/remotion) | Template nhất quán, preview và đồ họa theo dữ liệu. | [License riêng](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md), có điều kiện thương mại; chưa thêm React runtime. |
| [Whisper](https://github.com/openai/whisper) | Nhận dạng lời nói/phụ đề từ audio thực; hướng nâng cấp căn lời. | Code và weights MIT; là ASR, không phải TTS; cần đánh giá tiếng Việt trên máy thực. |
| [MoviePy](https://github.com/Zulko/moviepy) | Compositing/hiệu ứng Python khi có nhu cầu cụ thể. | MIT; v2 có breaking changes; README nêu chi phí import/export khiến chậm hơn FFmpeg trực tiếp. |
| [n8n](https://github.com/n8n-io/n8n) | Workflow, retry, quan sát và human approval; adapter webhook khi cần. | [Sustainable Use License](https://github.com/n8n-io/n8n/blob/master/LICENSE.md), giới hạn mục đích sử dụng; chưa cài thêm workflow engine. |
| [Postiz](https://github.com/gitroomhq/postiz-app) | Calendar, gói đăng, cộng tác và analytics đa kênh. | AGPL-3.0; [API](https://docs.postiz.com/public-api/introduction) và self-host cần credentials/quyền nền tảng; chưa kết nối. |

Tham khảo nội bộ: [README](../README.md), [đối chiếu repo](RESEARCH-VIDEO-FACTORIES.md), [quyết định kiến trúc xưởng](adr/0010-production-factory.md).
