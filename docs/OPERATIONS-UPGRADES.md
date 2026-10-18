# Vận hành sau bốn đợt nâng cấp

## Kiểm tra môi trường

```bash
uv run studio doctor --media media.yaml --flow render --json
uv run studio doctor --media media.yaml --flow website --json
uv run studio doctor --media media.yaml --flow film --json
uv run studio doctor --demo --smoke /tmp/kiem-tra-moi.mp4 --json
```

Doctor không gọi API/CLI model hoặc GPU. Dòng “đã cấu hình” chưa chứng minh credential/quota hay backend đang
phục vụ. Smoke tạo file mới, 12 giây hình tổng hợp, âm thử và chữ Việt; không ghi đè file có sẵn.
Trong **Cấu hình**, chọn luồng rồi bấm kiểm tra trước khi sản xuất. Website tự preflight trước capture/model.

## Ý tưởng và dữ liệu

Kho ý tưởng lưu trong cùng SQLite của xưởng, tối đa 200 mục đang hoạt động. Đổi trình duyệt kết nối cùng xưởng
vẫn đọc được dữ liệu. Lần mở đầu tự import `xstudio.ideas.v1` theo ID, giữ bản trình duyệt và cho thử lại khi lỗi.
Hai tab sửa cùng revision sẽ báo xung đột; nội dung đang nhập vẫn được giữ để người dùng đối chiếu.
Chuyển ý tưởng thành dự án ghi liên kết command cùng transaction, tránh tạo hai dự án do gửi lặp.

## Sao lưu và khôi phục

Đang chạy xưởng: dùng form **Sao lưu** trong Cấu hình và chờ command hoàn tất. Worker chờ checkpoint, dừng
writer website trong lúc chụp DB/media rồi tiếp tục. Chọn thư mục mới nằm ngoài output/upload/website.

Khi xưởng đã tắt, có thể dùng CLI:

```bash
uv run studio backup create --db studio.sqlite --media media.yaml --to /data/backup-moi
uv run studio backup restore --from /data/backup-moi --to /data/xuong-phuc-hoi
uv run python -m studio.console --db /data/xuong-phuc-hoi/studio.sqlite --media /data/xuong-phuc-hoi/media.restore.yaml
```

Gói là thư mục gồm `manifest.json`, snapshot DB và media có checksum. Giữ nguyên cấu trúc gói. Restore chỉ tạo
workspace mới; lỗi checksum/DB không tạo workspace dở. Đường dẫn asset/job và sidecar JSON được rebase.
Xưởng phục hồi dừng, automation manual; job/command đang dở không tự chạy. Ý tưởng và lịch sử dự án còn nguyên.
Token OAuth/API, policy key, cấu hình executable và session URI không được gom tự động. Cấu hình lại provider
trước khi tiếp tục. Upload chưa hoàn tất từ bản backup cần đối chiếu YouTube trước khi cho phép operation mới.

## Upload và cache

Upload YouTube từ orchestrator có checkpoint riêng. Sau restart, adapter xác minh file, metadata và phiên duyệt,
hỏi Google offset rồi gửi phần còn lại. Nếu phản hồi chunk cuối mất nhưng session báo đã hoàn tất, dùng ID đã
nhận thay vì tự upload lại. Session hết hạn 404 chỉ mở phiên mới khi checkpoint xác nhận chưa thử gửi chunk cuối. Đã thử chunk cuối mà
session không còn thì giữ operation để đối chiếu YouTube, tránh tự tạo bản đăng trùng. Thay file/metadata/gate hoặc restore
thiếu session URI sẽ giữ thao tác để đối chiếu; không xóa checkpoint thủ công khi chưa kiểm YouTube Studio.

Speech cache website kiểm SHA-256, duration và word timing; segment cache kiểm chữ ký đầu vào và checksum file.
Hỏng thành phần nào tạo lại thành phần đó; staging giữ final tốt khi bước thay thế lỗi.

## Phòng dựng và thư viện

Trong dự án đang review, vào **Cảnh & tư liệu → Sửa cảnh**. Có thể sửa narration/prompt, tạo lại audio/hình/cả hai,
chọn tư liệu thư viện, khóa/mở khóa. Lệnh cũ bị từ chối nếu manifest đổi. Mở khóa trước khi sửa cảnh đã khóa.
Mỗi lượt tạo bản mới, vô hiệu duyệt cũ và yêu cầu review/duyệt lại; tối đa ba vòng. Video đã upload cần đối chiếu,
không sửa âm thầm. Cảnh khác giữ asset còn hợp lệ qua cache và cut-list hiện có.

Trong **Thư viện**, upload ảnh/video/nhạc (tối đa 256 MiB/file), khai nguồn/quyền/attribution rồi lưu catalog.
File vừa upload là `unknown`. CC-BY phải có nguồn và credit. Preview chỉ phục vụ file quản lý còn đúng checksum.
Catalog YAML cũ vẫn dùng được; chọn file ngoài upload_dir bị chặn.

Provider `tts.provider: edge` nhận word timing từ dịch vụ; cần mạng, không cần API key. Provider khác vẫn dùng
được; thiếu timing thì phụ đề được ước lượng. Sidecar `.words.json` gắn checksum audio để cache không trộn lời/giọng.

## Review, chi phí và dung lượng

Khi tạo dự án qua command API, có thể gửi `rights_dossier` cùng `title`/`budget_tokens`.
Dossier là khai báo của người vận hành; giữ nguồn yêu cầu, đối tượng, quyền sử dụng và giới hạn cụ thể.
Nó được lưu với yêu cầu dự án và gắn lại vào đúng video khi review, kể cả sau restart và với bản Shorts dẫn xuất.
Rights-checker nhận toàn văn script, prompt cảnh và thumbnail để đối chiếu; code không tự đổi khai báo thành
license hoặc verdict pass. Mẫu giới hạn cho bài thử cục bộ: [local-validation.json](rights/local-validation.json).

Token budget tính usage backend báo, gồm token cache đọc lại; không suy ra ngân sách từ độ dài MP4.
Đặt `budget_tokens` trước khi chạy dựa trên số đo và số vòng sửa dự kiến. Hai review lỗi cùng bản dựng
được gom vào một vòng sửa, giữ đủ lý do và checkpoint khi restart. Budget media USD vẫn là hạn mức riêng.

Tab **Kiểm tra & đăng** có contact sheet lấy tối đa 24 cảnh từ media thật, report buộc với phiên bản và checksum.
Đây là mẫu để kiểm tra, cần xem bản cuối/nghe lời đọc trước khi duyệt. Adapter hình/tiếng tùy chọn khai bằng
`production.review_command`; nhận JSON stdin gồm manifest, contact sheet, đường dẫn audio/video và checksum;
trả `{"verdict":"pass|warn|block","findings":[{"level":"warn|block","text":"...","location":"S1@2.4s"}]}`.
Adapter là executable do người vận hành tin cậy, chạy với env đã lọc và cờ `read_only` của sandbox;
backend subprocess không cưỡng chế quyền ghi filesystem ở cấp OS. Timeout 1–300 giây; đầu ra sai hoặc adapter lỗi tạo block. `review_required: true` thiếu adapter cũng block.
Cấu hình lại adapter sau restore. Reviewer hiện có đọc JSON không tự trở thành model đa phương thức.

Trong **Cấu hình**, nhập giá TTS/1.000 ký tự, ảnh/request và clip/giây theo provider đang dùng. Giá trống là chưa
biết, giá 0 phải do người vận hành xác nhận. Hạn mức USD media/dự án chỉ tính ước lượng generation; thiếu giá
sẽ chặn trước gọi provider. Text token do backend báo cáo được hiển thị riêng; không quy đổi subscription CLI
thành tiền hay coi đây là hóa đơn thực. Lượt lỗi có thể phát sinh phí ngoài hệ thống, hiển thị chi phí chưa biết.

Giá từ form `production.pricing` cũng được dùng cho ledger provider `media.cost`; giá theo đơn vị
từ form ưu tiên giá tương ứng của provider. `tts.price_per_second` vẫn được cộng nếu đã khai.
Lớp tài nguyên ghi cùng lượt vào `media.usage` để giữ số lần sinh/lỗi/chi phí chưa biết; hai đường
ghi nhận không gọi provider hai lần và không cộng hai bản ghi thành hai lượt mua. Đổi bảng giá
không làm mất cache media.

Nút tài nguyên cho tổng dung lượng, từng dự án/nhóm media và ledger. Xem trước danh sách dọn segment, tạm dừng
xưởng rồi áp dụng. Danh sách đổi thì phải xem lại. Chỉ MKV checkpoint renderer có metadata phù hợp được xóa;
không xóa TTS/ảnh/clip trả phí, tư liệu người dùng hoặc final.

## Tạo Shorts

Quy trình mặc định hiện dùng `video.layout: editorial`, Full HD 1920×1080 cho ngang và 1080×1920 cho dọc.
Khung chữ/hình lấy nội dung của dự án hiện tại; tiếng giữ tốc độ, cân âm -14 LUFS và chỉ rút phần đuôi sau
từ cuối khi có timing. Phụ đề giữ dấu câu, ngắt câu/cân dòng, có chiều rộng riêng 38/26 ký tự cho ngang/dọc.
`layout: full_bleed` giữ kiểu trước đây; các giới hạn hoặc provider khai rõ trong cấu hình vẫn có hiệu lực.

Mở bản cuối của dự án, chọn **Tạo Shorts từ video này**, nhập tiêu đề và đoạn 0,5–180 giây. Mặc định
**Dàn lại khung dọc, chữ rõ** dùng ảnh/tiếng gốc đúng checksum và dàn theo tỷ lệ dọc; không gọi lại generation.
Nguồn cũ chỉ có MP4 hoặc nguồn dùng footage cần chọn rõ một trong hai cách giữ/cắt khung bên dưới.
Mốc được căn ra cue
SRT đầy đủ; nếu căn vượt 180 giây cần chọn lại. `contain` giữ khung nguồn với viền, `cover` cắt ngang theo tâm
đã nhập. Video mới là 9:16, có MP4/tiếng/SRT/poster và hồ sơ nguồn; demo ghi rõ mô phỏng.

Lệnh đợi khi xưởng đang tạm dừng. Bản trích có brief, manifest, ba review và gate publish riêng; không dùng lại
quyết định duyệt nguồn và không tự đăng khi vừa tạo. Người duyệt kiểm tra crop, chữ đã burn ở nguồn và ngữ cảnh
đoạn trích trước khi xuất bản. Nhánh analytics scheduler và workflow GPU mới chưa được chọn trong đợt này.
Hồ sơ nguồn ghi version và SHA-256 của video/phụ đề, khoảng cắt, công cụ biến đổi và checksum clip dùng để
tạo poster. Rights-checker nhận chuỗi nguồn này cùng tư liệu gốc. Nếu review không đạt, bản trích giữ nguyên
lời đọc đã chọn để xử lý finding, không tự chuyển thành một kịch bản viết lại.


## Giọng Piper của bản thử trước và hồ sơ quyền phát hành

Bản thử trước dùng command Piper `vi_VN-vais1000-medium` qua `scripts/piper-speech.py`. Runtime riêng cần `piper-tts[alignment]==1.8.0`; model và JSON cấu hình phải khớp SHA trong `docs/rights/piper-vais1000-evidence.json`. Lệnh nhận narration UTF-8 từ stdin và tạo WAV cùng `.timing.json`. Bật `tts.timing_sidecar: true` để kiểm hash, đầy đủ lời và mốc hợp lệ; dữ liệu sai làm lượt TTS thất bại. Mốc từ lấy từ mẫu âm của phoneme; trường hợp không ghép đủ token ghi rõ `piper-sentence-boundary`.

Đặt `tts.license`, `tts.source_url`, `tts.attribution` theo bằng chứng thật. Renderer giữ chúng trong take gốc và audio cắt đuôi; metadata video chính và Shorts giữ ghi công theo nguồn đã xác minh. Model card VAIS1000 ghi CC BY cho dataset; hồ sơ 2026-10-17 chưa xác lập quyền đầu ra của model do lineage Lessac. Cấu hình Piper trong lượt trước dùng license unknown và truyền rights_assessment cho SEO/reviewer để không lặp nhận định cấp phép quá rộng. Không đổi license file khác chỉ dựa vào tên model. Phạm vi thao tác của lượt này là xem cục bộ theo yêu cầu người vận hành; hồ sơ không cấp thêm quyền cho model hoặc đầu ra. Review quyền giữ block cho phát hành.

Shorts reflow giữ từng cảnh/claim của đoạn chọn, lấy mẫu QC theo khoảng thực và dùng lại thumbnail A/B/C đầy đủ từ nguồn. Muốn sửa nội dung bản trích: sửa bản nguồn rồi tạo Shorts mới.

Cấu hình phát âm đang thử dùng noise_scale=0, noise_w_scale=0 và khoảng nghỉ câu 0,18 giây. Mọi mốc từ phía sau được dời theo đúng số mẫu khoảng nghỉ. Viết lời thuần Việt, dùng “bản đã xuất”, “tên người”, “cảnh cần sửa” để tránh các cụm giọng này đọc không rõ. Quyền phát hành của từng model phải được đánh giá riêng theo hồ sơ nguồn; sửa phát âm không thay đổi giấy phép.


## Giọng Việt có quyền thương mại đang dùng

Giọng mặc định hiện tại là preset **Ngọc Huyền** của **VieNeu-TTS v3 Turbo**, runtime riêng
`vieneu==3.8.3`, chạy ONNX/CPU qua `scripts/vieneu-speech.py`. Không cần khóa API.
[Hồ sơ quyền](rights/vieneu-commercial-voice-evidence.json) dẫn model card đã ghim revision:
tác giả xác nhận Apache-2.0 bao gồm model/preset, đầu ra được dùng thương mại và người cung cấp giọng
đã đồng ý. Chỉ dùng preset đi kèm, không dùng reference audio để nhân bản giọng khác.

`tts.license: licensed`, nguồn và ghi công đi cùng take gốc/trim, còn `rights_assessment` giúp SEO/reviewer
phân biệt giọng mới với lịch sử Piper. Giữ notice Apache khi phân phối lại model/preset. Quyền giọng không
thay việc duyệt nội dung và phê duyệt đăng của chủ xưởng; lượt này chỉ xuất để xem, chưa đăng YouTube.

Manifest runtime chứa SHA của model, codec và preset. Wrapper kiểm các SHA, chạy offline, rồi nhận dạng
lại chính WAV đã lưu bằng Whisper small; nếu không khớp thì thử large-v3-turbo (CPU int8, không gợi transcript).
Giữ kết quả từng lần, kể cả khi hai model khác nhau về dấu. Chỉ trả timing sidecar
khi toàn bộ token khớp và mốc hợp lệ. Không sửa chữ nhận sai để ép đạt; lỗi phải được kiểm lại hoặc sửa lời.
Nếu một mẫu sinh không đạt, wrapper mặc định thử tối đa ba seed liên tiếp (23–25), vẫn cùng giọng/model.
Dừng ngay ở WAV đầu tiên qua kiểm đầy đủ. Recognition ghi seed/SHA từng lần; timing/source ghi seed được
chọn và số candidate. Ledger media đếm lệnh provider, còn candidate nội bộ được ghi riêng trong source;
không coi đó là một lần sinh duy nhất khi đánh giá thời gian/CPU. `--synthesis-attempts 1` tắt lần thử thêm.
Hết ba candidate vẫn dừng; cập nhật wrapper không tự mở lại bài thử đang ở gate escalation.
Cần cache ASR sẵn trên máy; lệnh không tự tải model khi đang xuất. Khi restore backup, cấu hình lại đường
runtime/model như các command provider khác vì provider executable nằm ngoài profile backup an toàn.

Shorts chỉ kế thừa quyền/ghi công của các tư liệu hiện tại; take Piper cũ giữ trong lịch sử sẽ không kéo
nhãn unknown hoặc credit cũ vào bản dùng giọng mới. Tư liệu unknown thật sự còn dùng vẫn chặn như trước.

Runtime đang dùng lưu manifest ổn định tại `output/runtime/vieneu/runtime-artifacts.json`, không phụ thuộc
thư mục một lần xuất. Model/codec/preset vẫn được kiểm SHA trước từng lần sinh; bản khóa dependency
được giữ tại `docs/rights/vieneu-runtime-lock.txt`. Phonemizer sea-g2p 0.10.0 có Apache-2.0; notice và SHA
được bổ sung vào hồ sơ thành phần. Không coi việc backup media là backup cả runtime bên ngoài repo.

Cache nhận dạng đang dùng nằm tại `/home/liend/.cache/xstudio-asr` trên filesystem Linux; manifest
`output/runtime/vieneu/asr-artifacts.json` giữ revision, kích thước và SHA của small/large/tiny.
Đã đối chiếu hash khi chuyển bản cache có sẵn từ ổ C; không tải lại hoặc thay model để chạy bài thử.
Nếu ASR báo đọc thiếu weights, giữ log lỗi, kiểm hash rồi thử lại bản sao đã xác minh trên filesystem
runtime. Không coi một lần nhận thiếu câu là kết quả đạt, và không chép narration vào kết quả nhận dạng.

Bài thử mặc định dùng `docs/rights/local-validation-vieneu.json`, chỉ chạy cục bộ. Dùng
`uv run python scripts/live-defaults-smoke.py` khi đã cài runtime/cache và cấu hình command provider.
Tùy chọn `--narration` nhận JSON các section đã rà để thử lại lời đọc qua model thật; không thay review
bằng fixture. Bài thử tự dừng worker sau hai bản xuất và giữ gate publish chờ người duyệt.

[ADR-0024](adr/0024-current-export-traceability.md) giữ heading/claim_ids khi sửa lời và chỉ nối
script_version khi narration khớp đủ. Draft/final kế thừa nhãn quyền của thành phần thực dùng; renderer
không cấp lại quyền chỉ vì file ghép được sinh bằng code. Metadata giữ ghi công `licensed` và `cc-by`
trước review; các bản ghi lịch sử được đánh dấu riêng trong gói bằng chứng. Timing sidecar có audit SHA
và producer. Cảnh báo thiếu chapter được bỏ cho bản đo dưới 30 giây, kể cả sau restart.
Rights review nhận metadata của đúng video đang xét, cùng phân loại reference hiện tại/lịch sử;
SRT dẫn xuất giữ quyền captions nguồn, và các vai trò cảnh dùng chung file clip vẫn được kiểm đầy đủ.
Khi credit ghép nhiều notice, metadata chỉ bổ sung notice còn thiếu để không lặp nguyên đoạn ghi công giọng.

Xưởng không tính bản đã có đủ tư liệu và ba review đạt là sản xuất bị treo chỉ vì chủ xưởng chưa duyệt
đăng sau sáu giờ. Gate publish vẫn chờ, cùng cơ chế nhắc/quá hạn riêng. Khi mở DB cũ, chỉ thông báo
idle lỗi thời trên gói hiện vẫn đạt mới không làm pause trở lại; budget và review lỗi vẫn giữ nguyên.
