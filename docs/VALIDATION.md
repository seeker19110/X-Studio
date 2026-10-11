# Xác minh bản nâng cấp factory

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

Chưa xác minh: video 12 giờ, chất lượng giọng/ảnh live, chi phí tiền thực, quyền/quota OAuth của tài khoản, upload và lịch phát YouTube thật, render đa máy, Windows end-to-end. Không dùng kết quả demo để suy ra các phạm vi đó đã hoạt động.
