# Xác minh bản nâng cấp factory

## PR #9 tách tên module admission để tránh collision với PR #20 (2026-10-25)

Base vẫn là main `93a290a1705ee7c62669804b48cfb9e0f2690a9d`. Đã đọc riêng
PR #20 (`2a6f033`) và thấy `storage.py`/`test_storage.py` của nhánh đó dùng cho
bảng ứng dụng và IdeaStore. PR #9 đổi tên module thành `storage_admission.py`
và test thành `test_storage_admission.py`; cập nhật import/monkeypatch, giữ nguyên
API `StorageMonitor`/`StorageAdmissionError`, cấu hình và hành vi. Không đưa code,
migration hoặc tính năng PR #20 vào nhánh này.

Test import tên mới đã chạy đỏ trước khi chuyển source; sau đổi tên **27 test
admission đạt 100%**. `UV_CACHE_DIR=/tmp/xstudio-pr9-uv-cache make verify` độc lập
đạt **1.205 Studio + 273 Gateway + 486 core = 1.964 test**, không skip, coverage
**100%** mỗi package (core cả nhánh). Studio vẫn **7.055** dòng thực thi/**70**
file test; README không đổi số đếm. Ruff/mypy, **32/32** eval replay, Node syntax
và diff-check đạt. Gateway giữ warning runpy cũ. Log: `/tmp/pr9-oct25-verify.log`.

Khi hòa giải riêng với PR #20, phải giữ admission trong dispatch `create`, ưu tiên
controls và bắt `StorageAdmissionError` trước `ValueError` trong worker mới.
Inventory resources của PR #20 có phạm vi/semantics khác, không thay thế snapshot
admission có entry limit và trạng thái incomplete. Đổi tên này chỉ xử lý collision
module, không xác nhận mọi luồng mới của PR #20 đã có admission. Website jobs/export
ngoài roots vẫn không được bao phủ; chi phí/provider live chưa được kiểm chứng.
Không merge PR, không chạy provider/upload hoặc deploy.

## PR #9 hòa giải với main PR #19 và reviewers multimodal (2026-10-16)

Đã tích hợp main `93a290a1705ee7c62669804b48cfb9e0f2690a9d` trong worktree
`/tmp/xstudio-pr9-reconcile`, `.venv` và cache uv riêng; đã đọc `AGENTS.md` mới
và `CLAUDE.md`, xác nhận đường dẫn import `studio` thuộc worktree. Giữ nguyên
attachments/reviewers multimodal, hosted video, ASR narration QC, synthetic-media
metadata, graphics, ledger và các workflow của main. Không sửa prompt hoặc
provider của main. ADR mới nhất **0020**; checklist rà soát lịch sử đã đánh dấu
LICENSE/AGENTS/SECURITY/Dependabot là có sẵn, không đề xuất tạo lại.

`UV_CACHE_DIR=/tmp/xstudio-pr9-uv-cache make verify` đạt **1.205 Studio + 273
Gateway + 486 core = 1.964 test**, không skip; coverage **100%** từng package
(Studio **7.055** dòng thực thi, **70** file Python trong tests gồm conftest;
core cả dòng/nhánh 100%). Ruff/mypy cả ba package, **32/32** strict eval replay,
Node syntax và diff-check đạt. Gateway giữ warning runpy cũ. Log:
`/tmp/pr9-oct16-verify.log`.

Chỉ xác minh offline, không gọi provider/model CLI/website/upload live, không
merge PR hoặc deploy. Chưa đối soát chi phí provider thực hoặc chứng minh chất
lượng provider/multimodal/ASR live. Website jobs/export ngoài output/upload roots
vẫn ngoài storage admission của `create`, xem [STORAGE.md](STORAGE.md). Scan không
phải quota hoặc reservation và jobs đã có vẫn có thể tăng dung lượng. PR #7
upload recovery vẫn là nhánh riêng.

## PR #9 hòa giải với scene graphics và rà soát docs trên main (2026-10-13)

Đã tích hợp main `5f5afae4362d3ff82bd90c8745b7c105bda5637f` (PR #13 và #18)
trong worktree/venv/cache riêng. Giữ nguyên model-data graphics, kiểm tra số claim,
layout, fallback/cache, ledger/budget pause, contact sheet/freeze QC và các
workflow đã có; không nhận các tính năng main là triển khai mới của PR #9.
ADR mới nhất **0017**. Ghi chú rà soát ngày 2026-10-13 đã đánh dấu
`SECURITY.md` và Dependabot là có sẵn, không đề xuất tạo lại.

`UV_CACHE_DIR=/tmp/xstudio-pr9-uv-cache make verify` đạt **1.127 Studio + 273
Gateway + 478 core = 1.878 test**, không skip; coverage **100%** từng package
(Studio **6.410** dòng thực thi, **69** file Python trong tests gồm conftest;
core cả dòng/nhánh 100%). Ruff/mypy cả ba package, **29/29** strict eval replay,
Node syntax và diff-check đạt. Gateway giữ warning runpy cũ. Log kiểm chứng:
`/tmp/pr9-graphics-verify.log`.

Chỉ chạy offline, không website/model CLI/provider/upload live, không merge PR
hoặc deploy. Chi phí trả tiền thực chưa được đối soát. Kiểm tra số trong graphic
chỉ xác nhận số có trong claim nguồn; không chứng minh nhãn–giá trị–nguồn đúng
ngữ nghĩa. Website jobs/export ngoài roots vẫn ngoài storage admission như
[STORAGE.md](STORAGE.md); PR #7 upload recovery vẫn riêng.

## PR #9 hòa giải với ledger/budget/QC của PR #12 (2026-10-13)

Đã tích hợp main `d7cb00d507a4e5c3f1208c906ad1195a2e67b8c8` trong worktree
`/tmp/xstudio-pr9-reconcile`, dùng `.venv` và cache uv riêng; đường dẫn import
`studio` được xác nhận thuộc worktree. Chỉ README có xung đột; giữ ledger chi
phí bền, budget pause, contact sheet, freeze QC, Website → Video và cleanup của
main. Các tính năng từ PR #12 không phải triển khai mới của PR #9. ADR mới nhất
trên bản tích hợp là **0016**, không thêm số ADR trùng.

`UV_CACHE_DIR=/tmp/xstudio-pr9-uv-cache make verify` đạt **1.083 Studio + 273
Gateway + 478 core = 1.834 test**, không skip; coverage **100%** từng package
(Studio **6.234** dòng thực thi, **68** file Python trong tests gồm conftest;
core cả dòng/nhánh 100%). Ruff/mypy cả ba package, **28/28** eval replay strict,
`node --check src/studio/web/app.js` và diff-check đạt. Gateway vẫn có warning
runpy đã tồn tại. Log: `/tmp/pr9-oct13-verify.log`.

Lần hòa giải này chỉ xác minh offline: **chưa kiểm chứng chi phí trả tiền thực
hoặc đối soát hóa đơn provider**; không gọi website/model CLI/TTS/provider/upload
live, không deploy hoặc merge PR. Website jobs (`website`) và thư mục xuất riêng
ngoài output/upload roots vẫn ngoài admission storage của `create`, như ghi tại
[STORAGE.md](STORAGE.md). Không mở rộng phạm vi hoặc thay đổi job đã có. PR #7
upload recovery vẫn là nhánh riêng.

## PR #9 hòa giải với Website → Video và cleanup trên main (2026-10-11)

Đã tích hợp main `13ab96abe9709dcd52876cae2b95a59c677f0625` vào nhánh storage
trong worktree riêng `/tmp/xstudio-pr9-reconcile`, với `.venv` riêng và nguồn
`studio` xác nhận nằm trong worktree. Giữ nguyên Website → Video, cleanup bằng
`ExitStack`, staging thumbnail và giới hạn FFmpeg của main; các tính năng này
không phải phần triển khai mới của PR #9. Giữ cả bộ test cleanup và storage,
không thay đổi provider/prompt/schema. ADR 0001–0015 không trùng số.

`UV_CACHE_DIR=/tmp/xstudio-uv-cache make verify` đạt **1.062 Studio + 273 Gateway
+ 478 core = 1.813 test**, không skip, coverage **100%** từng package (Studio
6.106 dòng thực thi, 66 file Python trong tests gồm conftest; core có coverage
nhánh 100%). Ruff/mypy cả ba package, strict eval replay **28/28**, Node syntax
và diff-check đạt. Gateway giữ RuntimeWarning runpy đã có trước. Log lần này:
`/tmp/pr9-reconcile-verify.log`. Không gọi website/model CLI/TTS/provider/upload
live; lịch sử kiểm tra website thật bên dưới được giữ từ main, không phải một
lượt chạy lại trong lần hòa giải này.

Phạm vi storage được ghi rõ trong README và [STORAGE.md](STORAGE.md): admission
chỉ áp dụng command `create` của **Tạo video mới**. Command `website` mới và thư
mục xuất website ngoài output/upload roots chưa được bao phủ; giữ nguyên
workflow này, không âm thầm mở rộng chính sách admission. Không xóa file, dừng
job đã có, merge PR hay deploy. PR #7 upload recovery vẫn là nhánh riêng.

## PR #9 hòa giải với main sau PR #10 (2026-10-11)

Main đổi thành `d9272224709af08e2fe5bdcba90de290fc6fa650` trong khi CI của
path fix chạy. Đã giữ các thay đổi CLI/publishing preview được merge bên ngoài,
không nhận chúng là công việc của PR #9. Hòa giải README, giữ cả lịch sử xác minh;
code storage và regression bypass giữ nguyên.

Bản kết hợp `make verify` đạt **983 Studio + 273 Gateway + 478 core = 1.734 test**,
không skip, coverage **100%** mỗi package (Studio 5.530 dòng thực thi, 65 file test).
Ruff/mypy cả ba package và **28/28 eval replay** đạt; Gateway giữ warning runpy cũ.
Review độc lập + 104 test factory/storage/CLI/media đạt. Chromium console smoke:
3 dự án, 0 lỗi JS, không overflow; trang publishing và 5 bộ lọc đạt ở desktop và
mobile 390px. Node syntax và diff-check đạt. Không gọi CLI/provider/upload thật.
PR #7 và #9 vẫn riêng biệt, chưa merge; tài liệu của hai PR cần hòa giải theo thứ
tự merge được phê duyệt.

## Sửa bypass admission qua symlink trước `..` trong PR #9 (2026-10-11)

Review độc lập phát hiện `abspath` rút gọn `link/../output` trước khi filesystem
resolve symlink, khiến monitor đo nhầm thư mục. Tái hiện đỏ ở cả output/cache,
external uploads và SQLite/WAL: file 200 byte vẫn được nhận dưới hard limit 100.
Bản sửa giữ nguyên đường dẫn, kiểm tra từng prefix và báo incomplete nếu gặp
symlink trước `..`; không âm thầm chuyển sang thư mục khác. Luôn kiểm tra upload
root riêng, kể cả lexical child chứa `..`; inode dedup tránh cộng trùng.

Năm regression mới xác nhận ba root trên bị chặn, đường dẫn `..` thông thường
vẫn đo đủ cache/media/DB-sidecar và lỗi quyền ở parent được báo an toàn. Storage:
**27 test, coverage 100%**. `make verify`: **938 Studio + 273 Gateway + 478 core =
1.689 test**, không skip, coverage **100%** từng package (Studio 5.398 dòng thực
thi). Ruff/mypy và **28/28 eval replay** đạt. Review code độc lập không thấy blocker.

Không thay đổi UI hoặc code upload. PR #7/#9 vẫn tách riêng; tài liệu có phần
chồng nhau và cần hòa giải khi một PR được merge sau phê duyệt. Scan vẫn không
nguyên tử trước filesystem mutation đồng thời và không phải quota OS.

## Thống kê dung lượng và admission riêng cho Factory (2026-10-11)

Base main `4350abfc1742d756494fe718a344101cc66ff2e1` sau PR #8. Nhánh này
độc lập với PR #7 đang chờ duyệt; không đưa code upload recovery vào bản này.
Giữ tính năng phim/hoạt hình của main, không nhận các tính năng đó là công việc mới.

`make verify` đạt **933 Studio + 273 Gateway + 478 core = 1.684 test**, không skip;
coverage **100%** từng package (Studio 5.387 dòng thực thi, 64 file test). Ruff/mypy
cả ba package và **28/28 eval replay** đạt. Gateway giữ RuntimeWarning runpy cũ.
Thêm 27 regression cases: 22 accounting/admission và 5 tích hợp Factory.

Kiểm thử gồm hardlink/root lồng nhau, symlink, scan thiếu/quá giới hạn entry,
phân nhóm cache/temp/media/DB-sidecar, config không hợp lệ, cache và admission
fresh scan. Tích hợp thực dùng SQLite và file áp lực 2 MiB: dự án còn chờ, controls
vẫn chạy, 5 vòng bị chặn không quét lại; bỏ riêng fixture áp lực và hết cache thì
dự án tiếp tục, asset đã duyệt giữ nguyên. Crash sau brief trước command-complete
không xét admission lại hoặc tạo brief trùng. Không có lệnh xóa dữ liệu trong tính năng.

Chromium smoke qua server demo/SQLite/output riêng: 3 dự án, không lỗi JavaScript,
không tràn ngang; kiểm tra UI storage absent/normal/warning/blocked/incomplete và
mobile 390px. Media/provider đều fake. `node --check` và `git diff --check` đạt.

Benchmark filesystem offline tạo 10.003 file tạm, tổng **11.294.720 byte** (cache
10.240.000, output 1.048.576, DB+WAL 6.144), số liệu khớp chính xác. Lần scan đầu
**0,410082 giây**; 1.000 snapshot có cache **0,005460 giây**; 1.000 lần kiểm tra
queued admission đang blocked **0,005924 giây**. Không xóa file trong scan; fixture
tạm được dọn sau benchmark. Đo trên filesystem local với metadata vừa tạo, không
đại diện ổ mạng, hàng triệu file hoặc giới hạn thời gian I/O cứng.

Mặc định soft/hard limit = 0, chỉ quan sát. Hard limit chỉ chặn tạo dự án mới của
Factory; job đã có vẫn có thể tăng dung lượng. Không reservation hoặc quota OS,
không scan nguyên tử, không tính file/provider ngoài roots và file unlink đang mở.
Chi tiết phạm vi/giới hạn và cách cấu hình: [STORAGE.md](STORAGE.md).


## Website → Video và hai video Đồng Hành 4K (2026-10-11)

Luồng mới của [ADR-0015](adr/0015-website-to-local-video.md) đã chạy qua form thật,
HTTP command, SQLite, worker, model CLI đã cấu hình, Chromium, Edge TTS và FFmpeg.
Tác vụ `13774411f6754cc5849079b27fb772bc` dùng chế độ cả hai, 60 giây/video,
4K, giọng `vi-VN-HoaiMyNeural`; ảnh nguồn là giao diện khách thật của trang chủ
và Góc học tập tại donghanhcungban.org. Không đăng nhập website hay thực hiện
các thao tác tài khoản. Video hướng dẫn giới thiệu cách bắt đầu và chọn môn học.

- Cả `intro.mp4` và `tutorial.mp4` đã lưu tại
  `C:\Videos\X-Studio\donghanhcungban.org-13774411f6754cc5849079b27fb772bc`.
  Mỗi video đúng **60,000 giây**, **3840×2160**, **30 fps / 1.800 frame**,
  H.264 High/yuv420p, color space BT.709, AAC stereo 48 kHz.
  Pipeline giải mã toàn bộ trước khi công bố file; ffprobe độc lập xác nhận
  metadata và SHA-256 khớp báo cáo. Có SRT, poster, kịch bản/nguồn và báo cáo QC.
- Chromium tại console thật: phát cả hai video, tua đến giây 32, giữ nguyên
  video/điểm tua qua polling; tám liên kết MP4/SRT/kịch bản/báo cáo trả HTTP 200.
  Preset thời lượng và nhập số tùy chọn hoạt động. 1440×1080 và 390×844:
  không lỗi JavaScript, không tràn ngang. Đây là lượt media thật, không phải demo.
- Regression được viết đỏ trước khi sửa: URL/thư mục C, dữ liệu model,
  demo offline, request/DNS/redirect, ảnh trắng/chưa tải nội dung, giới hạn
  request của SPA, phụ đề, thời lượng giọng đọc, TTS lỗi tạm thời,
  cache/staging, hủy và tiếp tục tác vụ. File website có **55 ca**.
- `PATH=/home/liend/.local/bin:/tmp/xstudio-stability-tools/bin:$PATH
  UV_PROJECT_ENVIRONMENT=/tmp/xstudio-website-test-venv make verify` đạt trên
  bản sao nguồn đối chiếu byte với workspace: **1.030 Studio + 273 Gateway +
  478 core = 1.781 test Python**, không skip. Coverage dòng **100%** cả ba
  package, core có coverage nhánh **100%**. Ruff/mypy cả ba package và replay
  strict **28/28** đạt; `node --check` và `git diff --check` đạt.

Nhật ký cục bộ trong `output/website-verify.log`, metadata video trong
`output/website-4k-verification.json`, đối chiếu nguồn trong
`output/website-source-comparison.json` và ảnh/kết quả browser trong
`output/website-ui/`. Môi trường Python 3.13.15, Linux/WSL, Chromium 1248,
FFmpeg n8.1. Không chạy YouTube/upload, model API có khóa hay eval model live.
Lượt này xác minh pipeline Website → Video và lưu trên ổ C qua WSL;
không chứng minh quy trình sau đăng nhập hoặc runtime Windows thuần.

## Phim và hoạt hình (2026-10-11)

Thêm `CinemaSpec`, storyboard trong phòng dựng, `scene_video` provider độc lập và
adapter ComfyUI/command; xem [ADR-0013](adr/0013-film-animation-scene-generation.md)
và [nguồn nghiên cứu](RESEARCH-FILM-ANIMATION.md). Xác minh trên nhánh phim/hoạt hình
riêng từ main `07b242f`; các thay đổi khác trong workspace không thuộc bản này.

- Studio: **906 test đạt**, không skip, **100%** dòng (5.239 statements) qua
  `COVERAGE_FILE=/tmp/xstudio-film-pr.coverage PATH=/tmp/xstudio-audit-tools/bin:$PATH make verify`.
- Gateway: **273 test đạt**, coverage 100%; core: **478 test đạt**, coverage dòng/nhánh 100%.
  Tổng **1.657 test Python**. Gateway có một RuntimeWarning `runpy` đã có trước.
- `make verify` đạt: Ruff/mypy cả ba package, eval ghi sẵn **28/28**;
  không chạy eval model live. `node --check` và `git diff --check` đạt.
- Provider có 107 test offline: API-format, prompt/seed bindings, outputs video trong
  `images`/`gifs`/`videos`, giới hạn tham số/bytes, không redirect, ffprobe, job lỗi,
  timeout và tiếp tục job đã nhận; cache invalidation theo graph/`num_frames`.
- FFmpeg thật: command nhận JSON stdin sinh clip chuyển động ngắn, renderer lặp clip
  phủ narration, ghép MP4 H.264 có audio ở **320×180 và 180×320**; warm render không gọi
  lại generator. Fixture là `testsrc2` tổng hợp, không đại diện chất lượng phim AI.
- `scripts/film-smoke.cjs` đạt với Chromium: payload phim/hoạt hình, aspect độc lập,
  mặc định giải trí, giữ input khi polling, provider chưa cấu hình/bị gỡ, escape dữ liệu,
  xem/tải clip, nhãn fake, chuyển ý tưởng và mobile 390px không tràn ngang. Ảnh thay thế
  mới nhất được hiển thị thay clip/đường tải/license cũ (regression đỏ trước khi sửa).
- Browser + console HTTP/worker/SQLite riêng khởi động trên port 8766: tạo phim 16:9
  (seed 101) và hoạt hình 9:16 (seed 202), mỗi dự án hai cảnh, qua brief/manifest,
  `scene_video`, final và hai gate publish; hồ sơ nhân vật/logline/style/seed đúng,
  không lỗi JS; bảy màn hình mobile 390px không tràn ngang.
  Toàn bộ media của lượt này là **fake**, không đăng YouTube; server/DB thử được dọn sau chạy.

Môi trường: Python 3.12.14 (cùng minor với CI), FFmpeg n8.1.3-17-gc389840894-20261010, Linux/WSL.
Chưa cài/chạy ComfyUI hoặc model GPU thật; chưa đánh giá chất lượng continuity,
reference conditioning, lip-sync, tốc độ/VRAM/chi phí diffusion hoặc Windows end-to-end.
UI chỉ xác nhận đã cấu hình provider; không xác nhận endpoint/model sẵn sàng.

## Xác minh bản tích hợp lên main cho PR #6 (2026-10-11)

Đồng bộ nhánh audit với main `80a76e6` sau PR #5; giữ nhật ký đầu ra render,
manifest version tăng đơn điệu, chặn cut-list lỗi thời và disk budget của ADR-0012.
Kết hợp các ràng buộc aspect/ngôn ngữ và regression audit với các sửa đó.

`COVERAGE_FILE=/tmp/xstudio-pr-merged.coverage PATH=/tmp/xstudio-audit-tools/bin:$PATH make verify`
đạt trên bản tích hợp: **778 Studio + 273 Gateway + 478 xagents-core = 1.529 test**,
không skip; coverage **100%** mỗi package, gồm line/branch của core. Studio có 4.868 dòng
thực thi và 61 file test. Ruff/mypy cả ba package và eval replay strict **28/28** đạt.
Môi trường Python 3.13.15/FFmpeg n8.1.3-17 giống lượt audit bên dưới.

Tái chạy cả `console-smoke.cjs` và `dashboard-smoke.cjs` trên server demo/SQLite riêng
bằng mã đã tích hợp: long/short, audience/hook/message, metadata English, gate generation,
settings drafts và retry kế hoạch thiếu budget đạt; không lỗi JavaScript/tràn ngang.
Kiểm tra responsive sau cập nhật CSS đạt **49 tổ hợp** bảy màn hình × bảy chiều rộng
1920/1440/1280/1024/768/390/360px, đúng một nav active/aria-current và bốn cột ở 1920px.
Không gọi provider tính phí, YouTube thật hoặc chạy lại benchmark dài của PR #5.

## Audit và hoàn thiện toàn workspace (2026-10-11)

Đã lấy main `0dc1d81606c38b32cdf3bd7b29e76a0e9c4047a5` từ GitHub vào workspace,
kiểm tra lại remote ở cuối lượt và hoàn thiện trên nhánh local `codex/audit-hardening`.
Phạm vi gồm Studio, Gateway, xagents-core và toàn bộ thay đổi dashboard/playbook đang có.
Không đổi prompt agent hoặc schema topic; không gọi provider trả phí hay đăng YouTube thật.

Các lỗi được tái hiện bằng regression trước khi sửa:

| Phạm vi | Kết quả sửa |
|---|---|
| Gate và policy | Quyết định mới nhất chi phối approval; gate đang mở không dùng approval cũ. Policy `production` chỉ duyệt plan; `publish` duyệt plan/publish; replies/escalation vẫn cần người. Signature Unicode/JSON hỏng bị từ chối. |
| Gateway và console | Chặn domain giả loopback như `127.attacker.example` và authority có hậu tố sai. Bearer/cookie/login Unicode không gây 500. Token Gateway/YouTube ghi qua file tạm ngẫu nhiên 0600 và replace nguyên tử; lỗi refresh 5xx không đánh dấu credential hỏng. |
| Workflow | Đóng connection SQLite sau mỗi transaction. Kiểm tra gate command trước enqueue và báo lỗi khi gate đã hết hiệu lực. Checkpoint từng reply đã thành công giúp retry/restart bỏ qua các reply đó. Fact review theo chương đọc budget từ brief bền vững. |
| Render và QC | Staging giữ bản đã publish khi encode/finishing thất bại. Chặn scene ID trùng, order thiếu/trùng, locked asset khác video/cảnh/checksum và cut-list sai phiên bản/action. JSON probe hỏng, duration không hữu hạn/không dương hoặc final thiếu hình bị QC chặn. |
| YouTube upload | Gửi từng chunk tối đa 8 MiB, dùng offset server xác nhận và status probe sau timeout/5xx. Mở file trước POST, `fstat` và đọc cùng descriptor. Xét tiến triển của Range trước giới hạn retry. HTTP giả kiểm cả thay file nguyên tử và probe thứ ba có tiến triển. |
| Dashboard và brief | Giữ audience và hook/message/tone qua boundaries, hợp nhất giới hạn an toàn; manifest thực tế theo 16:9/9:16 và ngôn ngữ yêu cầu. Metadata và nhãn ngôn ngữ caption theo cùng yêu cầu, cả SEO retry. Kế hoạch bị từ chối trả `planning_error`, ý tưởng có thể thử lại. Polling giữ draft cấu hình và lý do duyệt; checklist thế hệ mới xóa draft cũ. |

Nâng dependency khóa `multidict` từ 6.8.0 lên 6.9.1 để xử lý
[CVE-2026-104874 / GHSA-54p9-h82j-f925](https://github.com/aio-libs/multidict/security/advisories/GHSA-54p9-h82j-f925).
`pip-audit` trên export **toàn workspace và all extras** sau nâng cấp không phát hiện
vulnerability đã biết. Đây là kiểm tra advisory hiện có, không chứng minh không còn lỗi bảo mật.

Môi trường: Linux/WSL, Python **3.13.15**, uv **0.12.9**, FFmpeg
**n8.1.3-17-gc389840894-20261010**, font DejaVu. FFmpeg và Chromium là công cụ
kiểm chứng cục bộ ngoài repo, không trở thành dependency runtime mới.

Lệnh `COVERAGE_FILE=/tmp/xstudio-audit-complete.coverage PATH=/tmp/xstudio-audit-tools/bin:$PATH make verify`
đạt trên mã cuối. Coverage dùng file riêng để tránh xung đột với các lượt pytest đồng thời.

| Kiểm tra | Kết quả |
|---|---|
| Studio | **752 passed**, 100% dòng, 4.743 dòng thực thi; 61 file test |
| Gateway | **273 passed**, 100% dòng, 1.617 dòng thực thi |
| xagents-core | **478 passed**, 100% dòng và nhánh; 2.285 dòng, 638 nhánh |
| Ruff và mypy | Cả ba package đạt |
| Eval replay strict | **28/28** ca ghi sẵn đạt, 14 vai; không gọi model live |
| JavaScript | `node --check` đạt cho app và hai script smoke |
| Dependency | `uv sync --all-packages --locked --check` không cần thay đổi; `pip-audit` all extras không có advisory đã biết |
| Chromium console | 1440×1000 và 390×844: tạo ba dự án, xem review cập nhật trong dialog đang mở, đổi cấu hình, draft duyệt và đổi thế hệ checklist; 0 lỗi JavaScript, không tràn ngang |
| Chromium dashboard | 1440×1080 và 390×844: lưu/reload/chuyển đổi/undo ý tưởng, long+short, audience/hook/message và `metadata.language=en`, Kanban/search, script/scenes/review, kế hoạch thiếu budget có thể thử lại, draft cấu hình trước/sau khi gửi; sáu màn hình mobile không tràn ngang, 0 lỗi JavaScript |

Tổng **1.503 test Python**, không có test bị skip. Gateway còn một RuntimeWarning
`runpy` đã có trước; không có ResourceWarning từ CommandStore.
Script tái chạy: [console-smoke.cjs](../scripts/console-smoke.cjs) và
[dashboard-smoke.cjs](../scripts/dashboard-smoke.cjs), chỉ dùng server `--demo` và DB riêng.
Ảnh console ở [screenshots](screenshots/); ảnh dashboard hiện tại cũng được lưu tại đó.
Smoke test cố ý tạo kế hoạch thiếu budget để kiểm tra thông báo lỗi và khả năng thử lại.

ADR-0008/0011 và tài liệu Gateway đã cập nhật theo hành vi cuối. Thông tin quota YouTube
trong ADR-0008 được đối chiếu lại với tài liệu chính thức ngày 2026-10-11.

Giới hạn còn lại: chưa kiểm chứng end-to-end trên Windows, video 12 giờ, provider live,
chất lượng nội dung/giọng/ảnh thật hoặc upload/lịch phát YouTube thật. Cache chưa tự giới hạn
tổng dung lượng. Nhiều sidecar và publish bus chưa cùng một transaction; hard kill giữa
hành động bên ngoài và checkpoint vẫn có thể lặp upload/reply. Upload resumable được
kiểm bằng HTTP giả, chưa có phiên upload được lưu để tiếp tục qua restart toàn tiến trình.
100% coverage và demo không thay thế các phạm vi xác minh đó.

## Bổ sung sau PR #4: đầu ra bền vững và disk budget

Base: `0dc1d81606c38b32cdf3bd7b29e76a0e9c4047a5` (PR #4 được merge bởi tác nhân
khác trong lúc đang làm tiếp). Môi trường chạy bị tạo lại; checkout cũ chưa commit
không còn, nên các sửa tiếp theo được tái dựng từ main bằng TDD. Không thay đổi hai
repo X-Agents/projects-template, không gọi provider trả phí/upload/deploy.

Regression tái hiện **hai draft thay vì một** khi render xong nhưng thumbnail-designer
lỗi transient. Bản sửa kiểm tra cả restart SQLite và số lần editor được gọi. Chèn lỗi
trước/sau publish của draft, repair, final, thumbnail xác minh dùng lại event ID, không
gọi lại provider sau khi batch đã chuẩn bị bền vững. Có test từ chối output đã mất/hỏng,
repair không nhảy version ở giới hạn vòng sửa, lỗi xóa cache không mất checkpoint active.
Bốn ca repair/approve lỗi thời, có/không restart, xác minh không gọi renderer,
không ghi đè v2 và journal trước đó vẫn khôi phục được. Luồng request_changes từ
kịch bản tăng phiên bản manifest, kể cả model bỏ trường version hợp lệ theo schema.
QC được khôi phục cho reviewer sau restart; audit sai actor/malformed không ghi đè
báo cáo hợp lệ. Kiểm chứng này dùng file/SQLite/provider giả và FFmpeg offline.

`make verify` trên bản cuối đạt **711 Studio + 251 Gateway + 477 core = 1.439 test**,
coverage 100% từng package (Studio 4.706 dòng thực thi); Ruff/mypy đạt và **28/28**
eval replay đạt. Không skip; Gateway còn RuntimeWarning `runpy` đã có trước. Thêm
26 regression cases so với PR #4, không thêm file test. Script benchmark cũng qua Ruff.

Stress thật, Linux/Python 3.12.14/FFmpeg 7.1.5, lệnh:

```bash
uv run python scripts/benchmark-bounded.py --scenes 60 --scene-seconds 30 \
  --resolution 640x360 --wall-budget 600
```

| Đo | Kết quả |
|---|---:|
| MP4 thực tế, 15 fps | 1.824 s (30 phút 24 giây) |
| Cold / warm render | 61,071 s / 24,248 s |
| Scene encode cold / warm | 60 / 0 |
| QC toàn file | 5,815 s, scan_complete=true, không block |
| Peak RSS từng tiến trình con / Python | 223.512 / 32.128 KiB |
| CPU cộng dồn tiến trình con | 140,675 s |
| Segment + metadata | 116.576.747 byte |
| Workspace tạm gồm input và hai MP4 | 293.701.146 byte |
| Ngân sách segment cấu hình | 536.870.912 byte (512 MiB) |

1 thread cấu hình; nguồn là PPM một màu và sóng sine riêng từng cảnh, không phải footage
hoặc giọng đọc live. Kiểm duration ffprobe và mtime cache không đổi. Script tự dọn thư
mục tạm, giới hạn đầu vào tối đa 1 giờ, 2 độ phân giải, thời gian từng subprocess theo
phần ngân sách còn lại. Đây không phải thử tải 12 giờ/1080p/Windows hoặc chi phí thật.

Disk budget chỉ quản lý MKV checkpoint hợp lệ từng video. Cảnh đang encode có thể vượt
ngân sách trước khi bị phát hiện; free-space estimate không thay filesystem quota.
Audit outbox, paid cache và output người dùng chưa có chính sách dọn tự động. Xem
[ADR-0012](adr/0012-durable-render-outbox-and-disk-budgets.md) về các cửa sổ lỗi còn lại.

## Bổ sung: khôi phục và giới hạn tài nguyên (2026-10-11)

Nền tảng lần này: Linux, Python 3.12.14, FFmpeg 7.1.5; main đầu vào
`9ebc3880b4be23a9df9db0040d6e71b175e29622`. Chỉ thay đổi X-Studio, không đổi hai repo
X-Agents/projects-template. Không gọi provider trả phí hoặc YouTube thật.

29 ca regression được thêm vào các file test hiện hữu: checkpoint route qua restart,
resume render hết retry, SEO transient, QC lỗi/thiếu/truncated, cache FLAC, concat lỗi,
checkpoint hỏng, Range suffix và thay file trong lúc HTTP mở media. Các ca tái hiện
lỗi chính đã được chạy đỏ trước sửa. ADR mới: [0011](adr/0011-recovery-and-bounded-checkpoints.md).

`UV_CACHE_DIR=/tmp/xstudio-uv-cache make verify` đạt trên mã cuối: **685 Studio +
251 Gateway + 477 xagents-core = 1.413 test**, coverage 100% ở từng package; Ruff,
mypy cả ba package đạt; eval replay **28/28** đạt. Gateway còn một RuntimeWarning
`runpy` đã có trước. Không có test bị skip. `node --check src/studio/web/app.js` đạt;
nhãn duyệt escalation/replies được sửa đúng hành động. Lần này không chạy lại smoke
Chromium; bằng chứng giao diện PR #3 bên dưới là lịch sử, không phải ảnh mới.

Benchmark tái chạy bằng `uv run python scripts/benchmark-bounded.py --scenes N`:

| Đo | 12 cảnh | 120 cảnh |
|---|---:|---:|
| Thời lượng MP4 thật | 28,8 s | 288 s |
| Cold render | 4,887 s | 58,261 s |
| Warm render (bao gồm concat AAC) | 1,995 s | 19,049 s |
| Đỉnh RSS tiến trình con (KiB, Linux) | 125.344 | 124.820 |
| Đỉnh RSS Python (KiB, Linux) | 34.872 | 34.960 |
| Cache segment + metadata (byte) | 1.585.076 | 15.844.557 |
| QC quét hết file | 0,454 s | 3,215 s |

Cả hai: 320×180, 15 fps, 1 thread cấu hình; ảnh PPM một màu và sóng sine riêng từng
cảnh (2 giây/cảnh). Kiểm tra ffprobe duration, cache mtime không đổi ở warm run và QC
`scan_complete=true`, không có finding block. Không dùng thời gian này để suy ra hiệu
năng trên máy khác: máy chạy test khác đồng thời, không phải benchmark máy độc lập.
RSS là cực đại từng tiến trình con theo `getrusage`, không phải tổng RAM toàn cây tiến
trình. Fixture không đại diện độ phức tạp footage, giọng nói hoặc chất lượng sáng tạo.

Segment mới không lưu thêm bản sao MKV để concat; FLAC nén lossless phụ thuộc nội dung.
Cache PCM cũ vẫn còn cho đến khi người vận hành dọn khi xưởng đã dừng. Chưa tự động
giới hạn tổng dung lượng cache. Hard kill giữa publish và checkpoint route vẫn có thể
lặp đầu ra; không cam kết exactly-once. Không kiểm chứng thêm Windows hoặc 12 giờ.

## Lần xác minh trước (PR #3)

Môi trường: Linux, Python 3.12.14, uv 0.12.23, FFmpeg 6.1.1. Ngày: 2026-10-11. Không gọi model/TTS/ảnh tính phí và không đăng lên YouTube thật.

| Phạm vi | Kết quả |
|---|---|
| Studio `uv run pytest -q --cov` | 656 ca đạt, 100% dòng (4.547 dòng thực thi) |
| Gateway `uv run --no-sync pytest -q --cov` | 251 ca đạt, 100% dòng |
| xagents-core `uv run pytest -q --cov` | 477 ca đạt, 100% dòng và nhánh |
| `make lint-all` | Ruff và mypy đạt cho cả ba package |
| `make eval-replay` | 28/28 ca ghi sẵn đạt, 14 vai; không phải eval mới với model live |
| HTTP thực | Login/cookie/bearer, Host/Origin, CSRF, JSON validation, byte-range, chặn file ngoài output và tải file không an toàn dạng attachment |
| FFmpeg thực | Segment cache, frame timing, phụ đề Việt, title/quote/bars, footage lặp, nhạc ducking kết thúc đúng độ dài |
| Chromium desktop 1440×1000 + mobile 390×844 | Tạo ba dự án demo, chờ review, đổi cấu hình, mở phòng dựng, giữ gate; 0 lỗi JavaScript, không tràn ngang |

Tổng cộng **1.384 ca Python** ở ba package. Coverage là bằng chứng đường code được chạy, không chứng minh sản phẩm sáng tạo hoàn hảo hoặc nội dung model luôn đúng.

Gateway cần cài thêm `socksio` trong môi trường kiểm tra này vì môi trường có SOCKS proxy; không thay đổi adapter hoặc tắt proxy để vượt kiểm soát mạng. Có một RuntimeWarning có sẵn ở test `runpy` của gateway. Hai fixture HTTP của studio đã được sửa `close()` để tránh warning dọn file của mock.

Ảnh chụp giao diện nằm trong [screenshots](screenshots/), hiển thị **MÔ PHỎNG OFFLINE** và trạng thái thật của demo. Script tái chạy: [scripts/console-smoke.cjs](../scripts/console-smoke.cjs); cần Playwright trong môi trường phát triển, không phải dependency runtime của ứng dụng.

Ví dụ chạy smoke với một console `--demo` đã mở ở cổng riêng:

```bash
STUDIO_TEST_URL=http://127.0.0.1:8765 \
STUDIO_TEST_TOKEN="$STUDIO_CONSOLE_TOKEN" \
STUDIO_SCREENSHOTS_DIR=/tmp/xstudio-shots \
node scripts/console-smoke.cjs
```

Script từ chối nếu server không ở chế độ demo. Có thể đặt `CHROMIUM_EXECUTABLE_PATH` khi dùng Chromium đã cài sẵn. Không dùng database sản xuất cho lượt smoke này.

Kiểm tra màn hình Xuất bản (2026-10-11): `scripts/publishing-smoke.cjs` đạt với console demo đang chạy. Kiểm tra 8 màn hình ở 7 độ rộng (360–1920 px), không tràn ngang và không có lỗi JavaScript. Snapshot giả trong trình duyệt kiểm tra chờ duyệt, lịch đăng, đã đăng, lỗi, hoàn tác, bộ lọc, giữ lý do duyệt khi cập nhật, tải lại đường dẫn `#publishing`, escape nội dung và ẩn liên kết `fake.video`. Không có lệnh xuất bản nào được gửi và không sửa database. Script cần Playwright, `STUDIO_TEST_URL` và `STUDIO_TEST_TOKEN`; `STUDIO_SCREENSHOTS_DIR` tùy chọn để lưu ảnh.

Bản xem trước thật (2026-10-11): kịch bản biên tập về việc học, 6 cảnh motion do code tạo, giọng Việt qua `command`/edge-tts, FFmpeg bounded và phụ đề nhúng. MP4 H.264/AAC dài 62,47 giây, 720×1280, 24 fps, 48 kHz stereo, −13,8 LUFS và đỉnh −1 dBTP; QC đo đủ toàn bộ video, không có finding, đoạn đen hoặc khoảng lặng dài. Bản dựng và SQLite nằm trong thư mục ignored `output/real-preview/`, không nằm trong repo. Đây là lượt render thật, chưa phải chạy tự động toàn bộ các agent; không giả review pass hoặc đăng lên nền tảng. Smoke trên console này cũng kiểm tra nút Xem video nạp được hình và MP4 thực sự tiến thời gian khi phát trong Chromium.

Chưa xác minh: video 12 giờ, chi phí tiền thực, quyền/quota OAuth của tài khoản, upload và lịch phát YouTube thật, render đa máy, Windows end-to-end. Không dùng kết quả demo để suy ra các phạm vi đó đã hoạt động.

## Kết nối CLI và MP4 thật (2026-10-11)

Codex CLI và Claude Code CLI đã được kiểm tra bằng lời gọi tạo JSON thật sau khi đăng nhập, không suy ra khả năng gọi model từ việc có file credential. Codex trả lời với `gpt-5.6-sol`; Claude trả lời với `claude-sonnet-5-5`. File cấu hình cục bộ dùng hai backend CLI, không có backend `fake` dự phòng; console chạy không có `--demo` hoặc biến `STUDIO_LLM_PROVIDER=fake`.

Yêu cầu Shorts “Vì sao phải học khi mọi thứ đều có trên mạng?” đã đi qua nghiên cứu Claude, kế hoạch và kịch bản Codex, kiểm tra dữ kiện Claude, scene planning, metadata và biên tập bằng model thật. Sáu cảnh có Edge TTS tiếng Việt, PNG vector được CLI thiết kế và FFmpeg dựng. `draft_v2.mp4` tại `output/Pdec57ced433b4535a79a290a-V1/` dài 56,669 giây, H.264/AAC, 720×1280, 24 fps, âm thanh 48 kHz stereo. Chromium đã nạp và phát MP4 thực trong chi tiết dự án, thời gian tiến 0,947 giây trong lượt kiểm tra; không có lỗi JavaScript. Đầu ra và SQLite bị gitignore.

Lượt chạy thực phát hiện SVG có `stroke-dasharray` hợp lệ bị validator từ chối; đã thêm hỗ trợ với kiểm tra giá trị, cùng thông báo lỗi gọn. Hai cảnh đầu được dùng lại từ cache khi khôi phục. Lượt biên tập tiếp tục sửa từng ảnh có cảnh báo tương phản. Có lịch sử lỗi và thao tác phục hồi trong audit, không giả review hoặc xóa lịch sử để báo thành công. `extra.tool_budget_tokens` cho phép cấu hình trần tác vụ web phù hợp với số token CLI báo, vẫn tính cache và giữ kiểm tra trần tổng video.

Studio hiện tại: 951 ca đạt, 100% của 5.371 dòng thực thi; Ruff và mypy đạt (42 file nguồn). Các test YouTube giả lập dùng hồ sơ riêng, không phụ thuộc `media.yaml` thật của người vận hành. Snapshot chỉ trả trạng thái có cấu hình OAuth, không trả token; trường hợp thiếu hoặc hỏng credential vẫn cho phép dựng MP4 và giao diện ghi “Bản dựng cục bộ · chưa kết nối YouTube”. Trạng thái có credential không xác nhận quyền hoặc quota trên Google. Giọng và hình live đã được kiểm tra với mẫu này; chưa suy rộng thành đánh giá chất lượng cho mọi chủ đề. Theo lựa chọn của người dùng, chỉ tạo MP4, chưa kết nối OAuth hoặc đăng YouTube.

Bản cuối của lượt CLI: `output/Pdec57ced433b4535a79a290a-V1/final_v5.mp4`, 4.670.889 byte, dài 52,349 giây, H.264/AAC, 720×1280, 24 fps, giọng Việt 48 kHz stereo, phụ đề nhúng và SRT riêng. Bản mới được editor thật duyệt cho manifest v5; các bản trước và lịch sử mở lại vẫn giữ trong audit. QC quét đủ file: −14 LUFS, đỉnh −1,4 dBTP, không finding, không đoạn đen hoặc khoảng lặng dài. Đã xem khung hình thực để xác nhận phụ đề Việt và bố cục. `scripts/publishing-smoke.cjs` đạt trên console live: MP4 thực tiến thời gian khi phát, nút Xem video hoạt động, 56 kiểm tra bố cục/navigation, không lỗi JavaScript và không gửi lệnh xuất bản. Đây là bằng chứng render và phát file; review quyền tư liệu/nội dung cuối vẫn là trạng thái riêng trong dashboard, không suy ra đã đăng YouTube.


## Bổ sung: rà soát độ ổn định sau bộ video Đồng Hành (11/10/2026)

Base: `b92bc6a` (`feat: run real CLI video production and expose publishing previews`).
Đợt này ưu tiên lỗi và độ ổn định của X-Studio. Bộ chín video Đồng Hành đã xuất riêng
ở `C:\Videos\DongHanh-ChatLuongCao`; mã dựng trong output và nguồn ZIP đi kèm.
Sau khi video hoàn tất, thực hiện TDD cho các lỗi tại [ADR-0014](adr/0014-factory-cleanup-and-thumbnail-staging.md).

- Mốc trước sửa: 951 Studio tests đạt, 100% dòng (5.371 dòng thực thi); lint/typecheck cả ba package đạt.
- Thêm 24 regression cases: 23 ca thất bại đúng lỗi trước sửa, 1 ca xác nhận nhánh lỗi client đã được bảo vệ. Sau sửa, 24/24 đạt.
- Ảnh bìa: giữ JPEG đã duyệt khi lỗi ở lần nén 1/2/3; dựng đủ A/B trước commit; B lỗi giữ cả bộ cũ và retry dùng lại ảnh nền trong cache.
- FFmpeg: kiểm RunSpec thực qua sandbox cho graph ảnh/clip, JPEG và hoàn thiện captions/music. Decoder từng input, filter đơn/complex và encoder áp dụng render.threads; các test media thật vẫn chạy.
- Factory: chèn RuntimeError và KeyboardInterrupt tại bus, command store, client, autopilot, saved settings, snapshot và đóng bus. Kiểm khóa được xóa, SQLiteBus đã đóng và khởi động lại được.
- Ba fixture thumbnail cũ được sửa để runner ghi vào output thực trong argv thay vì ghi cứng vào tệp đích. Giữ nguyên kiểm tra chữ phủ, nén, cảnh báo và dọn staging.

`make verify` trên bản cuối đạt:

| Kiểm tra | Kết quả |
|---|---|
| Studio | **975 passed**, 100% dòng, 5.381 dòng thực thi; 64 tệp Python trong tests gồm conftest |
| Gateway | **273 passed**, 100% dòng, 1.617 dòng thực thi |
| xagents-core | **478 passed**, 100% dòng và nhánh; 2.285 dòng, 638 nhánh |
| Ruff và mypy | Cả ba package đạt bằng lệnh quy định trong Makefile |
| Eval replay strict | **28/28** ca ghi sẵn, 14 vai; không gọi model live |
| JavaScript | node --check đạt cho app.js |
| Chromium dashboard | 1440×1080 và 390×844: ý tưởng, chuyển thành dự án, undo, kế hoạch lỗi có thể thử lại, brief/ngôn ngữ, long/short, Kanban/search, script/scenes/QC, gate generation, draft settings, playbook và sáu view mobile; 0 lỗi JavaScript |

Tổng **1.726 test Python**, không skip. Gateway giữ một RuntimeWarning runpy có sẵn
ở test `gateway.manage`; không có lỗi kiểm thử. README và ADR được cập nhật theo
975 ca/64 file và ADR 0001–0014; test đối chiếu README đạt.

Chạy trên Linux/WSL, Python 3.13.15, FFmpeg/ffprobe BtbN n8.1 có libx264/libass/drawtext,
font Unicode. Để giảm I/O từ ổ NTFS, kiểm chứng dùng bản sao tại
`/tmp/xstudio-stability-audit` và venv riêng; 280 tệp source/test/config/prompt/schema/
recording được so SHA-256 với checkout trên C, không thiếu hoặc khác nội dung.
Lệnh: `PATH=/home/liend/.local/bin:/tmp/xstudio-stability-tools/bin:$PATH UV_PROJECT_ENVIRONMENT=/tmp/xstudio-stability-venv make verify`.
Log ở `output/stability-verify.log`; bản đỏ/xanh và so source ở output cùng tên;
ảnh UI ở `output/stability-browser`. Server demo/DB riêng được đóng sau kiểm tra.

Bộ video gồm 18 MP4 (9 bản 4K và 9 Full HD), 30 fps, tổng nội dung 742,1 giây.
Tất cả giải mã toàn bộ đạt; đo âm thanh, màu BT.709, số khung, chương và caption/layout
đạt. Thư viện ngoại tuyến phát được, đổi 1080p, seek chương, tìm không dấu và mobile
không tràn ngang; 0 lỗi JavaScript. ZIP nguồn kiểm integrity đạt: 44 ảnh giao diện,
65 đoạn lời đọc gốc và 9 timeline. Số liệu chi tiết lưu trong Kiem-tra và
Thong-tin-video.json ở thư mục video; không suy ra chất lượng nguồn giọng tăng lên
khi resample WAV lên 48 kHz.

Giới hạn: chưa kiểm suite cuối trên Windows native, provider/model live, ComfyUI GPU
hoặc YouTube upload thật. Giới hạn thread không phải quota RAM; graph vẫn tăng theo
số cảnh. Staging bảo vệ lỗi dựng trước commit, không tạo transaction chung cho nhiều
filesystem replace và SQLite publish. Không thay prompt, quyền đăng công khai hay
các giới hạn đã ghi ở ADR-0011/0012.
