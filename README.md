# X-Studio · Xưởng sản xuất video

Từ chủ đề đến kịch bản, lời đọc, hình ảnh/footage, dựng phim, phụ đề, kiểm tra và xuất bản YouTube. Một bảng điều khiển tiếng Việt, một worker, một database SQLite; tiếp tục công việc đã lưu sau khi khởi động lại.

![Bảng điều khiển X-Studio — dữ liệu demo](docs/screenshots/studio-desktop.png)

## Chạy xưởng

Cần Python 3.11+, [uv](https://docs.astral.sh/uv/) và FFmpeg/ffprobe có libx264, libass, drawtext cùng font Unicode (Linux: `ffmpeg fonts-dejavu-core`). Không cần Node, Redis hoặc GPU cho bảng điều khiển và bộ dựng mặc định.

```bash
uv sync --all-packages --locked
make studio-demo
```

Mở `http://127.0.0.1:8765`, nhập mã truy cập trong terminal, chọn **Tạo video mới**. Demo có DB riêng `demo.sqlite`, model/media/nền tảng giả; tệp MP4 demo không phải video thật. Các test FFmpeg riêng vẫn dựng video thật từ fixture cục bộ.

Vận hành với cấu hình hiện có:

```bash
make studio
# hoặc chọn hồ sơ, DB và cổng
uv run python -m studio.console --media media.yaml --db studio.sqlite --port 8765
```

Giữ `llm.yaml` và cấu hình provider TTS/ảnh của bạn; tham khảo [media.example.yaml](media.example.yaml) và ghép các block `video`, `render`, `production` từ [media.factory.yaml](media.factory.yaml). Hồ sơ factory mặc định provider giả để không tự phát sinh phí khi sao chép. API key dùng biến môi trường/`api_key_env`, không nhập vào source hoặc giao diện. Adapter hiện có hỗ trợ nhiều provider và TTS cục bộ qua lệnh không shell.

Trong **Cấu hình xưởng**, chọn phong cách hình ảnh, số luồng CPU, burn-in phụ đề và chế độ:

| Chế độ | Hành vi |
|---|---|
| `manual` | Chờ người duyệt kế hoạch và xuất bản |
| `production` | Tự duyệt kế hoạch hợp lệ, sản xuất đến bản cuối, chờ duyệt đăng |
| `publish` | Tự sản xuất và duyệt đăng khi review, bản quyền, metadata và QC đều đạt |

`publish` cần kết nối YouTube thật, provider thật và đủ quyền tài khoản. Fake media luôn bị giữ lại. Nếu có allowlist người duyệt, thêm `policy:studio` cho tự động và `human:editor` cho console. Reply và escalation vẫn cần con người quyết định. Tạm dừng xưởng có hiệu lực sau bước đang chạy.

```bash
uv run python -m studio.youtube login
uv run python -m studio.youtube status
```

Chọn `platform.provider: youtube` trong `media.yaml` sau khi hoàn tất OAuth của tài khoản bạn. Không chạy hai orchestrator trên cùng DB. Khi chạy VPS, giữ loopback và mở qua SSH tunnel; console không được thiết kế để public trực tiếp ra Internet.

## Những gì đã được nâng cấp

- **UI/UX:** dự án, tìm kiếm/lọc, phòng dựng theo cảnh, xem và tải bản cuối/SRT, checklist duyệt, nhật ký, pause/resume, cấu hình lưu bền; responsive và không phụ thuộc CDN.
- **Tiết kiệm gọi API:** TTS, ảnh, thumbnail có cache theo nội dung/cấu hình, checksum và commit nguyên tử. Cảnh sửa có định danh riêng; lỗi giữa chừng không làm mất phần đã tạo thành công. Không tái sinh cảnh đã khóa khi file bị thiếu: báo lỗi để khôi phục đúng tài nguyên.
- **Video dài:** chia kịch bản theo chương, chia scene planning/fact review theo nhóm; ghi nhận token từng chương. Segment mới dùng FLAC lossless, concat đọc trực tiếp cache để tránh bản sao trung gian; decoder cũng bị giới hạn luồng. Bộ dựng `bounded` mã hóa một cảnh mỗi lần, tái sử dụng segment, ghép video bằng stream copy; `graph` cũ vẫn dùng được.
- **Hình và tiếng:** ảnh có chuyển động, footage video, catalog có giấy phép, nhạc duck dưới lời đọc, chuẩn hóa loudness, phụ đề chia trang và burn-in tùy chọn. Motion template title/quote/bars vẽ chữ Việt bằng code; biểu đồ bắt buộc có nguồn.
- **Độ tin cậy:** retry media có giới hạn/backoff, hàng lệnh SQLite, lease một worker, policy có chữ ký riêng và thế hệ gate; kiểm tra file/checksum, thời lượng, nguồn, mô tả CC-BY và QC trước tự xuất bản. Web research có cache TTL và allowlist tên miền vẫn kiểm redirect/SSRF.

Tư liệu local: đặt file của bạn dưới `output/uploads`, điền [examples/library.yaml](examples/library.yaml), rồi ánh xạ video/cảnh bằng [examples/production-selections.yaml](examples/production-selections.yaml). Chỉ khai quyền sử dụng mà bạn thực sự có. Credit CC-BY được thêm vào mô tả trước preflight. File thay thế qua cut-list chưa có hồ sơ quyền sử dụng được đánh dấu `unknown` để review chặn.

## Thời lượng, tài nguyên và giới hạn thực tế

Console nhận mục tiêu **0–720 phút** (lớn hơn 0). [YouTube](https://support.google.com/youtube/answer/71673?hl=en) giới hạn 12 giờ hoặc 256 GB, tùy giới hạn đạt trước; trên 15 phút cần tài khoản đã xác minh. Đây là giới hạn thiết kế/đầu vào, **chưa có benchmark render và upload 12 giờ trong đợt nâng cấp này**.

- Bounded giảm độ lớn graph, không làm mất chi phí theo thời lượng. Bộ nhớ phụ thuộc một cảnh; cache âm thanh lossless và video trung gian vẫn cần ổ đĩa. Chưa có benchmark phần trăm tiết kiệm trên máy chuẩn.
- Burn-in phụ đề cần thêm lượt encode cuối. Muốn tiết kiệm CPU, tắt burn-in, dùng SRT riêng. Subtitle timing theo tỷ lệ lời đọc trong cảnh, chưa phải forced alignment/karaoke từng từ.
- Với video nhiều giờ, điều chỉnh `render.timeout_s` và `STUDIO_QC_TIMEOUT_S` theo máy; mặc định QC 300 giây. Ví dụ đặt `STUDIO_QC_TIMEOUT_S=86400` trước khi khởi động worker. Không tắt timeout hoàn toàn.
- Ngân sách token là token model text, không phải trần tiền cho TTS/ảnh. Dừng trước chương tiếp theo khi đã hết; một request đang chạy có thể vượt phần còn lại. Chất lượng sáng tạo, tính đúng đắn nội dung và thời lượng thực phụ thuộc model/provider; QC giữ lại sản phẩm lệch mục tiêu quá 20% mặc định.
- Chưa tích hợp stock search tự tải, video diffusion, tracking người nói, editor timeline kéo thả hoặc render cluster. Không cài đồng thời 28 thư viện; xem [đối chiếu nguồn và quyết định tích hợp](docs/RESEARCH-VIDEO-FACTORIES.md).

## Phát triển và xác minh

```bash
make verify            # lint/typecheck + coverage cả 3 package + eval replay
make studio-demo       # kiểm tra workflow offline
```

CI Linux cài FFmpeg/font, khóa phiên bản GitHub Actions và chạy cùng lệnh. Coverage yêu cầu 100% dòng cho từng package. Test renderer thực kiểm tra footage, nhạc, phụ đề, frame timing, cache và retry; test HTTP kiểm tra auth, Origin/CSRF và byte-range. Các lỗi retry nhánh, resume sau lỗi render, QC chưa hoàn tất và HTTP range được chặn bằng regression test; xem [ADR-0011](docs/adr/0011-recovery-and-bounded-checkpoints.md). Kiểm tra UI desktop/mobile bằng Chromium được ghi tại [VALIDATION.md](docs/VALIDATION.md).

- `src/studio/`: factory, console, orchestration, render, QC, provider và YouTube adapter.
- `src/studio/web/`: HTML/CSS/JavaScript native, đóng gói cùng Python.
- `agents/`, `skills/`, `topics/`: 14 vai, 24 skill và schema sự kiện; giữ prompt hiện có.
- `gateway/`, `xagents-core/`: thành viên uv workspace có test/coverage riêng.
- `tests/`: pytest 685 ca / 60 file.
- Test: 685 ca pytest của studio; xem báo cáo xác minh cho kết quả thực tế.
- `docs/adr/`: ADR 0001–0011; [ADR-0010](docs/adr/0010-production-factory.md) giải thích kiến trúc mới và đánh đổi.

Tham khảo thêm: [kiến trúc nền](docs/architecture.md), [CLI chi tiết](docs/CLI-REFERENCE.md), [CLAUDE.md](CLAUDE.md). Các hướng dẫn cũ về gate được bổ sung bởi policy ủy quyền ở ADR-0010.
