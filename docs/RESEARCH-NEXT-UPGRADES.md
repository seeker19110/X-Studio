# Hướng nâng cấp tiếp theo cho X-Studio

Ngày đối chiếu: **2026-10-16**. Source tại commit `13ab96a`. Phạm vi: Studio, giao diện, Website → Video, Gateway, lớp dùng chung và CI của workspace.

Ưu tiên tiếp theo là hoàn thiện vận hành và khả năng sửa sản phẩm: kiểm tra môi trường trước khi chạy, bảo vệ dữ liệu, tiếp tục upload qua restart và chỉnh sửa từng cảnh từ giao diện. Giữ Python, FFmpeg, SQLite và các adapter hiện có; mở rộng chất lượng phụ đề và review sau khi các bước vận hành có tiêu chí nghiệm thu rõ ràng.

Cập nhật triển khai 2026-10-16: bốn đợt đã được đưa vào source; đợt 4 chọn nhánh Shorts.
Xem [hướng dẫn vận hành](OPERATIONS-UPGRADES.md), [ADR-0016](adr/0016-durable-operations-and-editing.md)
và [bằng chứng kiểm tra](VALIDATION.md). Phần dưới giữ hiện trạng và lộ trình tại thời điểm nghiên cứu;
những nhánh mở rộng như analytics scheduler, alignment và workflow GPU mới chưa được chọn trong bốn đợt này.

## Hiện trạng từ source

| Phần | Đã có | Khoảng trống cụ thể |
|---|---|---|
| Điều phối | Command queue SQLite, lease, checkpoint, retry, gate và policy | Chưa có quy trình backup/restore của toàn workspace; bảng command/job chưa có cơ chế migration riêng |
| Kho ý tưởng | Tạo, sửa, xóa, chuyển thành dự án | Chỉ lưu `localStorage`, đọc tối đa 200 mục; dữ liệu phụ thuộc trình duyệt và origin |
| Render chính | Cache có checksum, commit nguyên tử, sửa cảnh, bounded segments, giới hạn cache segment | Chưa có tổng quan dung lượng và chính sách giữ dữ liệu cho toàn bộ TTS, ảnh, clip, website và bản xuất |
| Website → Video | Capture công khai, kịch bản, Edge TTS có mốc từng từ, checkpoint, QC và lưu ổ C | Cache speech chủ yếu kiểm sự tồn tại của file; cache segment kiểm chữ ký đầu vào, chưa kiểm checksum đầu ra khi tái sử dụng |
| Xuất bản | Upload theo chunk, hỏi offset sau lỗi mạng, tái dùng upload đã có audit | Session URI và offset nằm trong lượt gọi; restart giữa upload chưa tiếp tục được phiên cũ |
| Phòng dựng | Xem storyboard, clip, audio, nguồn và quyền sử dụng | Giao diện chưa gửi command sửa narration, đổi asset, khóa hoặc sinh lại cảnh; thư viện local vẫn khai YAML |
| Phụ đề | Pipeline chính chia trang; website dùng timing từ dịch vụ giọng đọc | Pipeline chính chia thời gian theo tỷ lệ lời đọc; hai luồng chưa dùng chung hợp đồng timing |
| Review | Ba vai review, QC bằng FFmpeg/ffprobe, checklist và preview cho người | Đầu vào reviewer hiện là JSON; đánh giá hình có khớp nội dung hay nhân vật có nhất quán cần thêm bằng chứng hình/tiếng |
| CI | Ruff, mypy, coverage ba package, eval replay trên Ubuntu | Các script smoke UI chưa chạy trong workflow; chưa có job Windows thực tế |
| Gateway và lõi | Gateway có health/status, failover và cooldown bền vững; lõi có event log WAL, lock và context theo project | Health Gateway chỉ xác nhận tiến trình; Studio chưa truyền project scope vào blackboard |

Căn cứ: [factory](../src/studio/factory.py), [kho ý tưởng và phòng dựng](../src/studio/web/app.js), [cache chính](../src/studio/render_cache.py), [website renderer](../src/studio/website_render.py), [YouTube adapter](../src/studio/platform.py), [QC](../src/studio/qc.py), [thư viện local](../src/studio/production.py), [CI](../.github/workflows/verify.yml).

[Đánh giá ngày 2026-09-04](DANH-GIA-NANG-CAP-XUONG-VIDEO.md) mô tả nhiều thiếu hụt đã được xử lý sau đó, như cache, preview, nhạc và footage. Dùng source cùng [README hiện tại](../README.md) để xác định phần cần làm tiếp; giữ báo cáo cũ như lịch sử quyết định.

## Các nâng cấp ưu tiên

### P0 Kiểm tra môi trường và chạy thử ngắn

Thêm `studio doctor` với kết quả theo từng luồng: render thường, website và phim. Kiểm tra executable, encoder/filter FFmpeg, ffprobe, font Unicode, Chromium, đường dẫn ghi, dung lượng trống và cấu hình provider. Tách trạng thái **đã cấu hình**, **có công cụ** và **đã chạy thử thành công**; trạng thái `scene_video_ready` hiện dựa trên adapter đã khởi tạo, chưa xác nhận backend/GPU.

Kiểm tra mặc định chỉ đọc và cục bộ. Kiểm tra kết nối hoặc chạy provider thật là chế độ riêng, có timeout và thông báo rõ tác vụ sẽ thực hiện. Với luồng website, phát hiện thiếu FFmpeg/font/Chromium trước capture và viết kịch bản giúp tránh làm các bước dài rồi mới báo thiếu công cụ.

- **Vị trí:** thêm `src/studio/doctor.py`; nối console, factory và launcher hiện có.
- **Nghiệm thu:** thiếu từng công cụ trả thông báo có cách xử lý; demo vẫn chạy offline; báo cáo không chứa token/cookie; smoke fixture 10–20 giây xác nhận file có hình, tiếng và phụ đề.

### P0 Sao lưu dữ liệu và lưu ý tưởng trên máy chủ

Đưa ý tưởng vào SQLite qua API có xác thực, kèm ID ổn định và revision để phát hiện hai tab sửa cùng lúc. Import kho `xstudio.ideas.v1` theo ID; chỉ đánh dấu import hoàn tất sau khi server xác nhận. Giữ dữ liệu trình duyệt khi import lỗi để có thể thử lại.

Thêm migration cho các bảng ứng dụng mới. `schema_version` của event hiện có phục vụ hợp đồng event; cần quản lý phiên bản bảng SQLite riêng. Backup DB dùng Online Backup API qua `sqlite3.Connection.backup()`. SQLite hỗ trợ snapshot DB đang hoạt động; WAL có trạng thái ngoài file DB chính, nên việc copy riêng `.sqlite` đang mở không phải quy trình backup phù hợp. [SQLite Backup API](https://sqlite.org/backup.html), [Python sqlite3](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup), [SQLite WAL](https://sqlite.org/wal.html).

Backup media cần manifest gồm đường dẫn tương đối, kích thước và SHA-256. Bản v1 nên chờ bước hiện tại kết thúc và tạm dừng mọi writer khi chụp gói DB + media, gồm cả worker website: backup DB riêng không làm các file media đồng bộ theo cùng thời điểm. Restore vào thư mục mới, kiểm DB và checksum trước khi dùng lại.

- **Vị trí:** `factory.py`, `website.py`, `console.py`, `web/app.js`; thêm module migration và backup.
- **Nghiệm thu:** đổi trình duyệt vẫn thấy ý tưởng; import hai lần không nhân đôi; migration lỗi giữ được DB cũ; khôi phục sang workspace mới mở được dự án và phát đúng media; gói backup không tự gom credential.

### P0 Tiếp tục upload YouTube sau restart

Lưu upload operation trước khi gửi media, gồm video ID nội bộ, checksum file, kích thước, dấu vân tay metadata, phiên gate, session URI và trạng thái. Khi mở lại, xác minh file và quyền gate còn hiệu lực rồi hỏi offset từ server. Tái sử dụng session URI và xử lý `308`/`Range` theo giao thức; session hết hạn có thể trả `404` và cần tạo phiên mới. [YouTube resumable upload](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol).

Code hiện đã tái dùng `platform_ref` sau khi có audit upload thành công. Phần cần bổ sung là khoảng trước audit: đang gửi file, hoặc server đã hoàn tất nhưng tiến trình chưa kịp ghi nhận. Lưu cả kết quả hoàn tất để retry không tự mở một upload mới khi có thể đối chiếu phiên cũ. Session URI cần được che khỏi log và API giao diện.

- **Vị trí:** `platform.py`, `_publish_video` trong `orchestrator.py`, bảng upload operation mới.
- **Nghiệm thu:** tắt tiến trình giữa chunk rồi mở lại tiếp tục cùng phiên; mất phản hồi chunk cuối có thể đối chiếu kết quả; đổi file hoặc gate hết hiệu lực bị giữ lại; phiên hết hạn có đường xử lý rõ. Trường hợp không xác định được kết quả phải chờ đối chiếu, tránh cam kết xử lý đúng một lần cho mọi sự cố.

### P1 Sửa cảnh và chọn tư liệu ngay trong phòng dựng

Cho sửa lời đọc/prompt, nghe thử audio, khóa/mở khóa, sinh lại hình hoặc clip và chọn asset có quyền sử dụng. Dùng command bền vững với `manifest_version` dự kiến và ID thao tác để chặn lệnh lỗi thời hoặc gửi lặp. Tận dụng `apply_cutlist` và cache hiện có; sửa narration cần cập nhật audio, timeline và các đầu ra phụ thuộc.

Mở thư viện media từ catalog YAML thành giao diện quản lý có thumbnail, loại file, nguồn, license, attribution và danh sách nơi sử dụng. Có thể import catalog hiện tại để giữ đường chuyển đổi đơn giản.

- **Vị trí:** `factory.py`, `renderer.py`, `production.py`, model cut-list, `web/app.js`.
- **Nghiệm thu:** sửa cảnh B giữ nguyên asset đã khóa ở A/C; command cũ bị từ chối; asset thay thế không có hồ sơ quyền được giữ lại để review; file nằm ngoài vùng upload bị chặn.

### P1 Dùng chung timing giọng đọc và tăng khả năng kiểm tra chất lượng

Chuẩn hóa kết quả TTS để nhận thêm word timings và nguồn timing. Tận dụng cách nhận `WordBoundary` đang có ở website cho provider hỗ trợ; pipeline chính tiếp nhận cùng định dạng. Khi provider thiếu timing, giữ chế độ ước lượng được gắn nhãn; khảo sát adapter alignment cục bộ sau đó.

[faster-whisper](https://github.com/SYSTRAN/faster-whisper) có word timestamps và chế độ CPU int8. [WhisperX](https://github.com/m-bain/whisperX) bổ sung forced alignment và cần model theo ngôn ngữ. Khả năng có trong thư viện chưa chứng minh chất lượng tiếng Việt của X-Studio; cần bộ audio có số, tên riêng và câu dài để so sánh trước khi chọn dependency.

Review hình/tiếng nên bắt đầu bằng contact sheet theo cảnh, đoạn audio và timestamp finding cho người duyệt. Bước tiếp theo mới thêm adapter review đa phương thức, gắn kết quả với checksum media và phiên bản manifest; giữ QC định lượng để đo codec, thời lượng, loudness và decode.

- **Vị trí:** `MediaResult`, renderer, `timeline.py`, `website_render.py`, `qc.py`, phần tạo package review trong orchestrator.
- **Nghiệm thu:** SRT không chồng cue, không vượt audio, giữ dấu tiếng Việt; cache timing đổi theo audio/voice; review cũ hết hiệu lực khi media đổi. Nếu đổi prompt/skill agent, cập nhật golden và chạy eval model thật theo `CLAUDE.md`.

### P1 Quản lý dung lượng và chi phí media

Hiển thị dung lượng theo dự án và nhóm TTS/ảnh/clip/segment/website/final. Bổ sung danh sách tham chiếu từ asset tới dự án trước khi cho dọn dữ liệu. Dọn segment tái tạo được có thể giữ cơ chế hiện tại; media trả phí và tư liệu người dùng cần chính sách giữ riêng, có bản xem trước các file sẽ xóa.

Đồng nhất kiểm tra checksum cache giữa hai pipeline. Với website, thử file audio/segment bị thay, bị cắt và sidecar JSON lỗi để xác định lúc nào cần tạo lại đúng thành phần. Giữ bản final tốt khi phục hồi cache thất bại.

Thêm ledger ghi số request, cache hit, token text, lượng TTS, số ảnh/giây clip và chi phí ước tính theo bảng giá do người vận hành cấu hình. Chi phí API thực tế và mức dùng subscription CLI cần hiển thị riêng; token budget hiện tại chưa phải trần tiền media.

- **Vị trí:** `render_cache.py`, `website_render.py`, `media.py`, `video_gen.py`, audit và màn hình cấu hình.
- **Nghiệm thu:** dọn cache giữ mọi asset đang được tham chiếu; báo dung lượng dự kiến thu hồi; sửa một cảnh chỉ ghi chi phí phần phát sinh; cache website hỏng không bị coi là hợp lệ chỉ vì file còn tồn tại.

### P2 Hiệu năng theo quy mô và sản phẩm mở rộng

`Factory.refresh()` gom toàn bộ media/audit để tạo snapshot; `JobStore.rows()` đọc toàn bộ job rồi `snapshot()` mới lấy 100 mục. Đây là điểm cần đo khi dữ liệu tăng. Tạo fixture 10/100/1.000 dự án, đo thời gian refresh, kích thước JSON, RSS và độ trễ giao diện trước khi chọn mức tối ưu. Sau đó thêm truy vấn có limit/index, API chi tiết dự án và projection có thể dựng lại từ event log. FTS5 là lựa chọn tìm kiếm text khi catalog đủ lớn. [SQLite FTS5](https://sqlite.org/fts5.html).

Các nhánh sản phẩm sau nền vận hành: chuyển một video dài thành các bản Shorts có brief/manifest và gate riêng; đồng bộ analytics theo lịch bền vững trong ứng dụng; phim có reference asset và benchmark workflow ComfyUI thật. Cấu hình lịch là tính năng đề xuất, chưa tạo lịch vận hành trong đợt nghiên cứu này.

Phim cần đo chất lượng nhất quán nhân vật, thời gian, VRAM và license của đúng model/workflow được chọn. Text bible và seed hiện có là đầu vào quản lý, chưa bảo đảm danh tính giữa các shot; xem [ADR-0013](adr/0013-film-animation-scene-generation.md).

## Ranh giới của Gateway và lớp dùng chung

[Gateway](../gateway/src/gateway/server.py) đã có `/health` và `/auth/status`. Doctor nên phân biệt tiến trình đang phục vụ với tài khoản còn khả dụng và model đã được probe; health HTTP 200 chưa xác nhận một yêu cầu sinh nội dung sẽ thành công. Giữ cooldown/failover ở tầng hiện có, đưa trạng thái cần thiết lên báo cáo vận hành và che thông tin credential.

[Blackboard của core](../xagents-core/src/xagents_core/blackboard.py) đã hỗ trợ project scope; [lớp Studio](../src/studio/blackboard.py) còn dùng context không phân vùng. Trước khi mở rộng nhiều kênh hoặc nhiều worker, lập bản đồ namespace dùng chung và theo dự án, rồi truyền scope xuyên runner/context. Nghiệm thu bằng hai dự án có nội dung và version độc lập, đồng thời đọc được event cũ. Giữ SQLite trên một máy theo mô hình hiện tại; quyết định tăng worker hoặc đổi database cần dựa trên số đo và thiết kế lease/claim công việc tương ứng.

## Thứ tự triển khai đề xuất

| Đợt | Phạm vi PR | Điều kiện hoàn tất |
|---|---|---|
| 1 | Doctor; migration và backup/restore; ý tưởng SQLite | Chạy được kiểm tra môi trường, phục hồi được gói thử, đổi trình duyệt vẫn giữ ý tưởng |
| 2 | Upload operation bền vững; checksum và phục hồi cache website | Test restart giữa chunk và cache hỏng đạt; giữ nguyên gate xuất bản |
| 3 | Sửa cảnh; thư viện media; timing TTS dùng chung | Sửa một cảnh từ UI đến MP4/SRT, asset khác được giữ và quyền sử dụng được kiểm |
| 4 | Review hình/tiếng; dung lượng/chi phí; tối ưu theo số đo | Có bộ mẫu chất lượng và baseline tài nguyên; chọn nhánh Shorts, analytics hoặc phim theo nhu cầu sử dụng |

Mỗi thay đổi runtime áp dụng TDD theo repo và chạy `make verify`. Đổi pipeline/manifest cần ADR; đổi prompt/skill cần golden và eval model thật. Đưa smoke console/dashboard/publishing vào CI bằng server demo và DB riêng; thêm Windows job cho đường dẫn, Unicode, atomic replace và một bản dựng ngắn có font thật.

## Xác minh cho lần rà soát này

Trên source `13ab96a`, lệnh sau đạt **170 test**, không skip:

```bash
uv run --offline pytest -q tests/test_factory.py tests/test_console.py \
  tests/test_website_video.py tests/test_render_cache.py \
  tests/test_licensed_media.py tests/test_platform.py
```

`uv run --offline ruff check src tests` đạt. Đây là kiểm tra chọn lọc phục vụ rà soát source, không phải chạy lại `make verify` hoặc chứng nhận provider thật. Môi trường hiện tại có Python 3.13.15; FFmpeg/ffprobe không nằm trên PATH, nên chưa đo lại render thật, GPU hoặc upload YouTube trong lượt này. Bằng chứng media thật từ lần trước nằm trong [VALIDATION.md](VALIDATION.md).

Đầu ra của lượt này là lộ trình nghiên cứu; các module và API mới ở trên là phạm vi triển khai đề xuất.
