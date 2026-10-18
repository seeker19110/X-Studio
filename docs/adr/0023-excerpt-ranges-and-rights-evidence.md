# ADR-0023: Mốc cảnh thật và hồ sơ quyền của bản trích

Ngày: 2026-10-16. Trạng thái: chấp nhận. Bổ sung ADR-0022.

Shorts reflow giữ từng mục script, heading và claim_ids của các cảnh đã chọn. Mỗi Scene có `clip_start_s`
tùy chọn, là vị trí đo trong file clip dẫn xuất; `duration_s` là phần cảnh nằm trong clip. Timeline không
cộng đệm/chuyển cảnh lần nữa cho các cảnh này. Inspection lấy mẫu giữa từng khoảng và giữ toàn khung dọc;
QC chỉ đo phần âm thanh/hình của khoảng đó. Clip có mốc cố định là bản trích: sửa nguồn rồi tạo Shorts mới,
không biến thao tác sửa cảnh thành lời đọc mới nằm trên một clip cũ. Cảnh gốc vẫn sửa theo ADR-0004.
Editor nhận asset đang được manifest tham chiếu, kể cả asset đã đăng ký từ version trước; không làm mất
hồ sơ của cảnh được tái sử dụng khi chỉ sửa vài cảnh. Phụ đề bỏ token chỉ dùng để ngắt nghỉ không được đọc,
nhưng vẫn kiểm toàn bộ token có chữ/số trước khi khôi phục dấu câu từ narration.

Bản trích giữ bộ thumbnail ngang A/B/C đã được model thiết kế từ nguồn, kiểm checksum và ghi producer tái
sử dụng thật. Không thu nhỏ cả khung dọc vào poster ngang. Gói review có spec nguồn và nhãn reused_from,
để đánh giá lời hứa/hypothesis của thumbnail với phần trích; phần trích không phù hợp vẫn phải bị giữ lại.
Metadata khởi tạo chỉ gồm tiêu đề người vận hành và lời thực sự có trong clip; SEO thật được cung cấp
thời lượng đo và quy tắc không dùng độ dài video làm lời hứa tốc độ thao tác.

Font được nối bằng SHA-256 tới thông báo giấy phép được lưu trong repo, có URL upstream; không suy quyền
cho font khác chỉ vì trùng tên. Reviewer nhận notice đầy đủ một lần, khung giữ reference/hash trong cache
và provenance. Thumbnail ghi font thật trong attribution. Voice giữ alias yêu cầu và resolved voice/provider;
clip bất biến ghi đúng voice_id thực tế.

Hồ sơ kiểm thử ghi riêng điều khoản Edge consumer và quyền phát hành chưa được xác minh, không lấy giấy
phép Azure thay cho Edge. Scope private_local_validation luôn bị policy tự đăng giữ lại ngay cả khi ai đổi
automation sang publish. Điều này ghi đúng giới hạn của lượt thử, không tạo quyền cho nhà cung cấp hoặc
phê duyệt người dùng. Bản chạy lại bỏ tên dự án khỏi lời/hình để tránh một yêu cầu quyền tên không cần thiết.

Schema Scene thêm clip_start_s nullable, tương thích dữ liệu cũ. Không đổi prompt/skill agent hoặc topic ACL.

## Licensed local speech

The initial local speech trial used Piper `vi_VN-vais1000-medium` through the existing command adapter; the replacement is documented below. Its optional timing sidecar must match the produced audio SHA and full narration; missing or invalid timing fails the command instead of silently using stale boundaries. The Piper wrapper exposes model phoneme duration tensors in memory and records measured word boundaries when token counts match; otherwise it labels measured sentence boundaries explicitly. Original model files remain unchanged.

TTS license, source and attribution travel with raw and trimmed audio. Derived Shorts retain CC BY credit in their metadata. Resolving a voice license does not authorize publication. Official model card, training lineage and exact model/config SHA are retained in `docs/rights/piper-vais1000-evidence.json`; the isolated GPL engine and model binaries are local runtime dependencies, not vendored into the application.


## Bổ sung ngày 2026-10-17: độ rõ lời và phạm vi giấy phép

Wrapper Piper cho phép điều chỉnh noise/noise_w và khoảng nghỉ giữa câu. Khoảng nghỉ được tính theo số mẫu thật, đi vào offset của mọi word boundary phía sau, không thêm sau câu cuối. Các từ tiếng Anh và cụm khó đọc trong bản thử được biên tập lại thành câu Việt giữ nguyên ý; caption lấy đúng narration mới, không thay bằng kết quả ASR.

Đã đối chiếu lời tác giả Piper về licensing: nhãn MIT của kho không bổ sung quyền cho từng checkpoint. Card VAIS1000 dẫn CC BY ở mục Dataset; Lessac upstream dùng research license. Hồ sơ 2026-10-17 kết luận NOT_CLEARED cho phát hành công khai/thương mại của giọng này, thay cho việc chỉ để một câu hỏi mở. Đây là quyết định dùng trong vận hành dựa trên bằng chứng tìm được, không phải kết luận pháp lý rằng mọi đầu ra đều vi phạm. Giữ bản thử cục bộ và không duyệt publish.

## Giọng thay thế có quyền thương mại (2026-10-17)

Theo yêu cầu thay Piper, dùng preset đi kèm VieNeu-TTS v3 Turbo qua adapter `command` hiện có.
Model card của tác giả cấp Apache-2.0 cho model/preset, cho phép rõ đầu ra thương mại và xác nhận
đồng ý của người cung cấp giọng. Lưu revision, card, notice và SHA của model/codec/preset; không
nhân bản giọng từ mẫu ngoài. Cấu hình ghi `licensed` và gửi hồ sơ quyền mới cho SEO/reviewer.
Đây là quyền của giọng được chọn, không thay phê duyệt đăng của chủ xưởng.

Wrapper chạy model offline, nhận dạng lại WAV vừa lưu bằng Whisper small, thử thêm large-v3-turbo khi
bộ nhỏ không khớp; cả hai đều không gợi transcript. Giữ kết quả từng lần và tên model cấp timing.
Chỉ tạo sidecar mốc từ khi toàn bộ token khớp narration và mọi mốc đều hợp lệ; không thay chữ nhận
sai bằng narration để làm đẹp kết quả. SHA liên kết sidecar với đúng audio. ASR là kiểm bổ sung,
không tạo chứng nhận nghe của người. Sửa lời và thay giọng chỉ sinh lại audio bốn cảnh;
giữ hình gốc, dựng lại phụ đề và Shorts theo nguồn mới.

Quyền/ghi công của Shorts lấy từ final/captions nguồn, reference của manifest hiện tại, tư liệu
reflow thực dùng và music của phiên bản nguồn. Chọn bản ghi mới nhất theo đường dẫn. Các take
Piper cũ vẫn ở lịch sử nhưng không làm nhiễm quyền/ghi công của bản đã thay toàn bộ tiếng.
Tư liệu hiện tại còn `unknown` vẫn khiến bản trích giữ `unknown`; không nâng quyền bằng cách lọc lịch sử.
