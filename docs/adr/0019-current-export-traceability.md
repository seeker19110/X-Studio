# ADR-0019: Truy vết kịch bản và quyền của bản xuất hiện tại

Ngày: 2026-10-18. Trạng thái: chấp nhận. Bổ sung ADR-0018.

Sửa lời từ phòng dựng giữ heading và claim_ids của từng section. Sau cut-list, renderer chỉ nâng
script_version khi toàn bộ narration theo thứ tự cảnh khớp nguyên văn kịch bản mới nhất; không gắn
một kịch bản chưa được đọc vào bản dựng. Giữ giới hạn ba vòng sửa và lịch sử event bất biến.

Quyền của draft/final và Shorts tổng hợp từ bản ghi mới nhất của những đường dẫn thực sự được
manifest tham chiếu, cùng nhạc của phiên bản xuất. Thứ tự giữ hạn chế là unknown, cc-by, licensed;
generated của bước ghép không xóa quyền thành phần. Đây là nhãn vận hành, không tự cấp một giấy
phép chung cho mọi thành phần. Ghi công được giữ nguyên văn, bao gồm licensed có attribution,
trước khi reviewer nhận metadata. Khi credit ghép nhiều notice, chỉ thêm những notice còn thiếu;
không lặp giọng đã có chỉ vì còn thiếu credit font. SRT trích lại giữ provenance của captions nguồn,
không nhận giấy phép giọng từ file video. Take cũ không được tái sử dụng chỉ vì bản hiện tại đã đạt review.

Gói rights review đánh dấu current/historical theo path, kind, scene_id và variant_id. Nhiều vai trò
cùng tham chiếu một file clip vẫn là current; bản ghi cũ của cùng vai trò mới là historical. Timing JSON có audit producer,
SHA-256 của sidecar và audio, phiên bản manifest; reviewer nhận bằng chứng của reference hiện tại.
Production và SEO nhận speech_profile đã cấu hình cùng speech_rights, không phải lệnh hoặc credential.
Rights-checker nhận metadata mới nhất của chính video_id đang kiểm. Blackboard dùng chung giữa các
video không được thay cho metadata của đối tượng trong gói bằng chứng.

Preflight bỏ tư vấn thiếu chapter khi bản đo dưới 30 giây: ba chapter tối thiểu mười giây không thể
đặt vừa. Khi chưa đo vẫn giữ tư vấn; chapter đã khai vẫn được kiểm. Preflight tính lại sau render và
khi replay. Ngưỡng này chỉ điều khiển tư vấn nội bộ, không thay định dạng hoặc phân loại nền tảng.

Bài thử mặc định dùng hồ sơ VieNeu mới có nguồn ủy quyền từ yêu cầu người vận hành, ghi rõ chỉ xem
cục bộ. Hồ sơ Piper cũ giữ nguyên. Runtime manifest được lưu ngoài thư mục một bài thử riêng lẻ;
giữ checksum và model revision. Không có phê duyệt xuất bản hoặc chứng nhận nghe của người được tạo.
Thumbnail ngang giữ chuẩn adapter; ảnh bìa dọc xem trước có thể lấy từ chính khung Shorts đã xuất.

Bản đã có đủ tư liệu và ba review đạt, đang chờ gate publish, không thuộc công việc sản xuất bị treo
sau sáu giờ. Replay chỉ bỏ tác dụng pause của thông báo cũ có đúng nguyên nhân idle và gói hiện tại
vẫn đủ điều kiện; các nguyên nhân budget, review lỗi hoặc thiếu tư liệu vẫn giữ. Không duyệt publish
hoặc escalation bằng chữ ký người giả. Nếu bản thử cũ có gate escalation chỉ do idle lỗi thời, giữ
event gốc và ghi invalidate có bằng chứng; đây là hủy thông báo sai, không phải phê duyệt phát hành.

Không đổi topic/schema/ACL hoặc prompt/skill agent. Golden và replay vẫn áp dụng cùng bộ hợp đồng.

VieNeu có thể đọc cùng một câu khác nhau theo mẫu sinh. Wrapper thử tối đa ba seed liên tiếp (mặc định
23–25), dừng ngay khi một bản WAV thật được ASR độc lập nhận đúng toàn bộ token và có mốc hợp lệ.
Không thay lời nhận dạng bằng kịch bản, không bỏ qua lỗi timing, không đổi giọng/model hoặc tốc độ.
Recognition giữ seed và SHA của từng lần kiểm; timing/source gắn seed được chọn và số candidate.
Đây là nhiều candidate nội bộ của một lệnh TTS cục bộ, cần phân biệt với số request media trong ledger.
Hết ba candidate vẫn dừng; không tự mở gate escalation hoặc phê duyệt đăng.

Scene planning chia chương phải giữ đúng toàn bộ lời đọc của riêng chương, theo thứ tự. Kiểm tra
cả checkpoint đã lưu và kết quả model mới trước khi ghép manifest; không nhận lại toàn bộ kịch bản
ở chương cuối gây lặp phần mở đầu. Checkpoint sai phạm vi được sinh lại, còn proposal mới sai bị
chặn sau khi đã ghi đầy đủ token. Model vẫn quyết định hình và cách chia cảnh; code không tự cắt
nội dung ngoài phạm vi để biến một kết quả sai thành đạt. Không thay đổi lời hoặc schema agent.

Nhóm phụ đề cũng phải vừa thực tế trong hai dòng, thay vì chỉ cộng độ dài ký tự. Nếu từ tiếp theo
tạo trang thứ ba thì mở nhóm mới ngay tại mốc từ đo được, tránh trang một từ lóe vài mili giây
trước nhóm cuối. Ghi công có một đoạn tiền tố nguyên văn của notice đầy đủ được hoàn tất tại chỗ;
nếu notice đầy đủ đã ở đoạn khác thì bỏ đoạn tiền tố trùng, giữ phần mô tả còn lại và tính idempotent.
