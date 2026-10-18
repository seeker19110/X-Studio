# X-Studio · Xưởng sản xuất video

Từ chủ đề đến kịch bản, lời đọc, hình ảnh/footage, dựng phim, phụ đề, kiểm tra và xuất bản YouTube. Một bảng điều khiển tiếng Việt, một worker, một database SQLite; tiếp tục công việc đã lưu sau khi khởi động lại.

![Bảng điều khiển X-Studio — dữ liệu demo](docs/screenshots/workflow-desktop.png)

Bốn đợt nâng cấp vận hành: doctor và backup/restore; kho ý tưởng SQLite; upload tiếp tục sau restart;
sửa cảnh/thư viện từ UI và timing TTS; contact sheet, ước tính chi phí/dung lượng, API tổng quan gọn và tạo Shorts
có gate riêng. Xem [hướng dẫn sử dụng](docs/OPERATIONS-UPGRADES.md) và [ADR-0021](docs/adr/0021-durable-operations-and-editing.md).

## Chạy xưởng

Cần Python 3.11+, [uv](https://docs.astral.sh/uv/) và FFmpeg/ffprobe có libx264, libass, drawtext cùng font Unicode (Linux: `ffmpeg fonts-dejavu-core`). Không cần Node, Redis hoặc GPU cho bảng điều khiển và bộ dựng mặc định.

```bash
uv sync --all-packages --locked
make studio-demo
```

Mở `http://127.0.0.1:8765`, nhập mã truy cập trong terminal, chọn **Tạo video mới**. Demo có DB riêng `demo.sqlite`, model/media/nền tảng giả; tệp MP4 demo không phải video thật. Các test FFmpeg riêng vẫn dựng video thật từ fixture cục bộ.

**Phòng duyệt** giữ các quyết định đang chờ. **Xuất bản** hiển thị bản cuối, lịch đăng và kết quả đăng cả sau khi đã duyệt; có bộ lọc chờ duyệt, đã lên lịch, đã đăng và cần xử lý. Có thể mở thẳng `http://127.0.0.1:8765/#publishing`. Lịch demo được ghi rõ là mô phỏng và không có liên kết video thật.

Với bản MP4 thật, chọn **Xem video** để phát ngay trong dự án, hoặc **Metadata & QC** để xem thông tin đăng và kết quả đo. Có thể dựng và xem video cục bộ trước khi kết nối YouTube; adapter xuất bản giả không biến bản dựng thật thành media mô phỏng.

Vận hành với cấu hình hiện có:

```bash
make studio
# hoặc chọn hồ sơ, DB và cổng
uv run python -m studio.console --media media.yaml --db studio.sqlite --port 8765
```

Giữ `llm.yaml` và cấu hình provider TTS/ảnh của bạn; tham khảo [media.example.yaml](media.example.yaml) và ghép các block `video`, `render`, `production` từ [media.factory.yaml](media.factory.yaml). Hồ sơ factory mặc định provider giả để không tự phát sinh phí khi sao chép. API key dùng biến môi trường/`api_key_env`, không nhập vào source hoặc giao diện. Adapter hiện có hỗ trợ nhiều provider và TTS cục bộ qua lệnh không shell.

Để dùng gói ChatGPT và Claude đã đăng nhập, cấu hình hai backend `codex` / `claude-code` trong `llm.yaml`, với `binary`, `config_dir` và model được tài khoản hỗ trợ. Không cần API key cho hai CLI này. CLI có thể dùng nhiều lượt nội bộ và báo cả token đọc cache; đặt `extra: {tool_budget_tokens: 180000}` ở gốc `llm.yaml` nếu cần trần riêng cho một tác vụ web. Bỏ cấu hình này giữ hạn mức từng agent; trần tổng video vẫn do brief và supervisor kiểm tra, không bỏ đếm token.

Giọng đọc mặc định cục bộ dùng VieNeu-TTS v3 Turbo, preset Ngọc Huyền, qua `scripts/vieneu-speech.py` và adapter `tts.provider: command`. [Hồ sơ quyền thương mại](docs/rights/vieneu-commercial-voice-evidence.json) giữ xác nhận của tác giả cho model, giọng dựng sẵn, đầu ra thương mại và sự đồng ý của người cung cấp giọng. Runtime riêng ghim `vieneu==3.8.3`, model/codec/preset kiểm theo SHA; Whisper cục bộ phải nhận đúng toàn bộ narration trước khi tạo mốc từ. Không dùng mẫu giọng bên ngoài.

Piper vẫn có wrapper riêng; giọng VAIS1000/Lessac của bản trước **chưa được chốt quyền phát hành**, xem [hồ sơ Piper](docs/rights/piper-release-assessment-20261017.json). Không kế thừa kết luận quyền giữa các giọng. Edge TTS vẫn được hỗ trợ và cần Internet.

Hình minh họa vector dùng `image.provider: command`, lệnh `.venv/bin/python -m studio.cli_visual --size {size} --out {out}` và `model: cli-vector`. CLI thiết kế SVG theo cảnh; code kiểm tra SVG rồi rasterize thành PNG thật. Khi gọi helper trực tiếp, SVG và file `.art.json` ghi model/token nằm cùng ảnh; khi đi qua cache renderer, PNG được giữ và tệp trung gian được dọn. Đây là minh họa vector, không phải mô hình sinh ảnh chụp. FFmpeg cần decoder `librsvg` cho hồ sơ này.

Sau khi cấu hình media thật và `production.automation: production`, chạy `bash scripts/run-live.sh`. Launcher dùng DB riêng `output/live/production.sqlite`, từ chối backend/media giả và giữ bước đăng ở cửa duyệt. Bản dựng MP4, SRT và thumbnail được lưu dưới `output_dir` của hồ sơ; toàn bộ cấu hình tài khoản và đầu ra này bị gitignore.

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

## Website → Video, lưu trên ổ C

Trong xưởng thật, mở **Website → Video** (`/#website`), dán link công khai, chọn giới thiệu/hướng dẫn/cả hai, thời lượng mỗi video (20–300 giây), giọng nữ/nam và Full HD hoặc 4K. Mặc định lưu ở `C:\Videos\X-Studio`; mỗi yêu cầu có thư mục riêng với MP4, SRT, poster, kịch bản/nguồn, ảnh chụp, audio và báo cáo kiểm tra. Tiến độ, dừng và tiếp tục nằm ngay trong màn hình; MP4 phát/tải trực tiếp sau khi kiểm tra đạt.

Sau `uv sync --all-packages --locked`, cài Chromium một lần trên **máy chạy console**:

```bash
uv run playwright install chromium
# Linux thiếu thư viện hệ thống: dùng lệnh install-deps của Playwright với quyền quản trị thích hợp
uv run python -m studio.console --db studio.sqlite --port 8765
```

Luồng này dùng backend `codex` hoặc `claude-code` đã đăng nhập trong `llm.yaml` để viết kịch bản; Edge TTS tạo giọng và mốc phụ đề thật. Cả CLI, Edge TTS và website cần Internet. Không cần API key mới. Cấu hình media của luồng dự án thường vẫn dùng các provider hiện có.

Có thể đặt `STUDIO_WEBSITE_CHROMIUM` (đường dẫn executable Chromium), `STUDIO_WEBSITE_FFMPEG`, `STUDIO_WEBSITE_FFPROBE` và `STUDIO_WEBSITE_FONT` (file font Unicode `.ttf`) nếu các công cụ không ở vị trí mặc định. Trên WSL, thư mục C được ánh xạ `/mnt/c`; trên Windows dùng trực tiếp ổ C. Font Segoe UI lấy từ Windows khi có; Linux dùng DejaVu Sans hoặc font bạn cấu hình.

Video hướng dẫn dựa trên những trang công khai, điều hướng và nội dung đã quan sát; không tự đăng nhập hay thực hiện thanh toán/gửi bài. Trang có CAPTCHA, nội dung cần POST hoặc phần chỉ dành cho thành viên có thể không truy cập được. Nút dừng xử lý ở checkpoint sau bước đang chạy. Video chỉ xuất cục bộ; việc đăng công khai vẫn qua gate của xưởng. Chi tiết: [ADR-0015](docs/adr/0015-website-to-local-video.md).

## Những gì đã được nâng cấp

- **UI/UX:** tổng quan với việc cần làm tiếp theo, kho ý tưởng có lưu/khôi phục, dự án dạng thẻ hoặc Kanban, tìm kiếm/lọc, brief video dài/ngắn, đọc kịch bản và nguồn, phòng dựng theo cảnh, xem/tải bản cuối/SRT, checklist duyệt và số liệu đã đồng bộ; responsive và không phụ thuộc CDN. Kho ý tưởng lưu trên trình duyệt; dự án sản xuất lưu trong SQLite.
- **Quy trình chuẩn:** [playbook tám bước](docs/QUY-TRINH-SAN-XUAT-VIDEO.md) với đầu ra, vai phụ trách và tiêu chí kiểm tra cho 16:9/9:16. Màn hình Quy trình chuẩn có hướng dẫn từng bước và nguồn GitHub tham khảo; các repo tham khảo không tự trở thành dependency.
- **Tiết kiệm gọi API:** TTS, ảnh, thumbnail có cache theo nội dung/cấu hình, checksum và commit nguyên tử. Nhật ký đầu ra render dùng lại event ID sau restart để tránh phát draft/editor lặp. Cảnh sửa có định danh riêng; lỗi giữa chừng không làm mất phần đã tạo thành công. Không tái sinh cảnh đã khóa khi file bị thiếu: báo lỗi để khôi phục đúng tài nguyên.
- **Video dài:** chia kịch bản theo chương, chia scene planning/fact review theo nhóm; ghi nhận token từng chương. Segment mới dùng FLAC lossless, concat đọc trực tiếp cache để tránh bản sao trung gian; decoder cũng bị giới hạn luồng. Bộ dựng `bounded` mã hóa một cảnh mỗi lần, tái sử dụng segment, ghép video bằng stream copy; `graph` cũ vẫn dùng được.
- **Hình và tiếng:** ảnh có chuyển động, footage video, catalog có giấy phép, nhạc duck dưới lời đọc, chuẩn hóa loudness, phụ đề chia trang và burn-in tùy chọn. Motion template title/quote/bars/list/stat vẽ chữ Việt bằng code; biểu đồ bắt buộc có nguồn.
- **Soát trước khi đăng (ADR-0016, học từ kho [`stored`](https://github.com/seeker19110/stored)):** QC bắt hình đứng yên ≥ 8 giây (nguy cơ slideshow, `STUDIO_QC_MAX_STATIC_S`); bản cuối có **contact sheet** — một khung giữa mỗi cảnh trích từ chính MP4 cuối, có nhãn cảnh/mốc giờ — hiện ở thẻ duyệt xuất bản và tab Kiểm tra & đăng; **sổ chi phí media** tính USD theo bảng giá bạn khai (`tts.price_per_1k_chars`, `image.price_per_image`, `scene_video.price_per_second`) cho mỗi lượt gọi provider thật, trần `production.media_budget_usd` chặn trước lượt gọi sẽ vượt.
- **Đồ họa theo cảnh do model chọn (ADR-0017, lần rà thứ hai kho `stored`):** production-manager điền `graphic` cho cảnh số liệu/so sánh/liệt kê/trích dẫn (`stat` số đếm lên, `bars`, `list` hiện lần lượt, `quote`, `title`) dưới dạng dữ liệu template — không phải code; số trên đồ họa phải có trong claim có nguồn của kịch bản đã fact-check, lệch thì code bỏ graphic và cảnh dùng ảnh. Layout do code đo: chữ thu nhỏ cho vừa ô, chừa vùng phụ đề đốt cứng và giao diện Shorts, không vừa thì báo lỗi chứ không cắt chữ. `production.graphics` của bạn vẫn ghi đè.
- **Theo kịp nền tảng và provider (rà lần ba kho `stored` + quét Internet 14/10/2026, [nghiên cứu](docs/2026-10-14-nghien-cuu-nang-cap.md)):** TTS Gemini đi qua Interactions API với model 3.8 (bản 2.5 preview tắt 17/11/2026, vẫn chạy được khi bạn khai model cũ), ảnh Gemini mặc định `gemini-3.1-flash-lite-image` + `image.image_size`, ElevenLabs mặc định model có tiếng Việt, thumbnail nhận tới 50 MB như YouTube, `edge-tts` ≥ 7.3. **Nhãn AI ([ADR-0018](docs/adr/0018-synthetic-media-and-hosted-video.md)):** `metadata.synthetic_media` do code quyết khi chắc (phim `generated_video` từ asset sinh) hoặc theo `production.synthetic_media`, hiện ở gate publish và gửi `containsSyntheticMedia` lên YouTube; **Veo 3.1** là provider `scene_video` thuê đầu tiên (checkpoint operation, chỉ tải từ googleapis, SynthID). **QC có tai ([ADR-0019](docs/adr/0019-asr-narration-qc.md)):** đặt `STUDIO_QC_ASR_MODEL` trỏ vào model whisper.cpp và dùng ffmpeg ≥ 8 có filter `whisper` thì QC nghe lại bản cuối, chấm WER từng cảnh và cảnh báo cảnh đọc sai/bỏ đoạn; thiếu thì im lặng. Sổ chi phí media cảnh báo một lần khi qua 80 % trần (`media.cost_warning`). `AGENTS.md` tóm tắt luật cho mọi công cụ agent. Cùng đợt: khai tài trợ trả tiền (`paid_product_placement`) lên YouTube theo `production.paid_product_placement`, provider clip **Gemini Omni**, TTS `edge` gốc và ElevenLabs có mốc từng từ cho phụ đề, giá TTS theo giây (`tts.price_per_second`), ảnh gpt-image-2 đúng 16:9, bình luận kèm ảnh có cờ `has_image`, console và gate publish báo khi đã dùng ≥ 80 % trần media; phụ đề karaoke đốt cứng (`video.karaoke`), snapshot YouTube lấy thời lượng qua `videos:batchGetStats` (rổ quota riêng). **Reviewer xem được ảnh ([ADR-0020](docs/adr/0020-multimodal-reviewers.md)):** editor thấy lưới ảnh từng cảnh, rights-checker thấy ảnh cảnh và thumbnail (bắt logo, khuôn mặt thật mà provenance không lộ), quality-reviewer thấy contact sheet bản cuối và chữ thật trên thumbnail; code chọn và thu nhỏ ảnh, model vẫn chỉ trả JSON.
- **Độ tin cậy:** retry media có giới hạn/backoff, hàng lệnh SQLite, lease một worker, policy có chữ ký riêng và thế hệ gate; kiểm tra file/checksum, thời lượng, nguồn, mô tả CC-BY và QC trước tự xuất bản. Web research có cache TTL và allowlist tên miền vẫn kiểm redirect/SSRF.

Tư liệu local: đặt file của bạn dưới `output/uploads`, điền [examples/library.yaml](examples/library.yaml), rồi ánh xạ video/cảnh bằng [examples/production-selections.yaml](examples/production-selections.yaml). Chỉ khai quyền sử dụng mà bạn thực sự có. Credit CC-BY được thêm vào mô tả trước preflight. File thay thế qua cut-list chưa có hồ sơ quyền sử dụng được đánh dấu `unknown` để review chặn.

## Tạo phim và hoạt hình

Trong **Tạo video mới**, chọn **Phim ngắn** hoặc **Hoạt hình**, nhập tóm tắt câu chuyện,
hồ sơ nhân vật và phong cách (điện ảnh/2D/3D/anime). Định dạng 16:9/9:16 chọn độc lập.
Phong cách, character bible, negative prompt và seed được lưu trong brief/manifest;
phòng dựng hiển thị storyboard và cho xem/tải clip của từng cảnh.

- **Ảnh có chuyển động**: dựng theo pipeline TTS + ảnh + FFmpeg hiện có, phù hợp xem trước nhịp câu chuyện.
- **Clip AI**: sinh shot ngắn qua **ComfyUI** với workflow API-format đã cài, hoặc chương trình
  cục bộ qua `command`. FFmpeg lặp footage để phủ lời đọc và ghép bản cuối, phụ đề, nhạc.
  Chọn phong cách 3D là chỉ dẫn hình ảnh, chưa phải engine rig/animation Blender.

Ghép phần `scene_video` từ [media.film.example.yaml](media.film.example.yaml) vào cấu hình
đang dùng. Provider mặc định `none`; demo dùng `fake` và chỉ sinh tệp mô phỏng. ComfyUI
cần model/nodes/GPU phù hợp, graph xuất ở **API format**, bindings prompt/seed bắt buộc
và output node lưu **MP4**. `num_frames` cho phép khai số frame phù hợp model, chẳng hạn
81 với workflow yêu cầu 4n+1. `scene_video` sinh shot; `video.provider=ffmpeg` ghép phim.
Không cần thêm dependency Python cho adapter ComfyUI.

Cache nhận diện graph và cấu hình; accepted job lưu `prompt_id` để tiếp tục sau timeout.
Sửa cảnh sinh lại dùng seed mới ổn định theo lượt sửa, giữ cảnh đã khóa; bản quyền của
clip mặc định `unknown` cho tới khi được khai đúng. Mô tả/seed giúp quản lý nhân vật,
chưa bảo đảm danh tính; v1 dùng một giọng kể, chưa tự upload reference hoặc lip-sync.
UI báo provider đã cấu hình, không xác nhận GPU/backend đang hoạt động.

Ví dụ brief: [film-brief.json](examples/film-brief.json). Đối chiếu ComfyUI, Wan,
HunyuanVideo, LTX, AnimateDiff, MoviePy, Blender và Storyboarder cùng license:
[nghiên cứu GitHub](docs/RESEARCH-FILM-ANIMATION.md), [ADR-0013](docs/adr/0013-film-animation-scene-generation.md).

## Thời lượng, tài nguyên và giới hạn thực tế

Console nhận mục tiêu **0–720 phút** (lớn hơn 0). [YouTube](https://support.google.com/youtube/answer/71673?hl=en) giới hạn 12 giờ hoặc 256 GB, tùy giới hạn đạt trước; trên 15 phút cần tài khoản đã xác minh. Đây là giới hạn thiết kế/đầu vào, **chưa có benchmark render và upload 12 giờ trong đợt nâng cấp này**.

- Bounded giảm độ lớn graph, không làm mất chi phí theo thời lượng. Bộ nhớ phụ thuộc một cảnh; cache âm thanh lossless và video trung gian vẫn cần ổ đĩa. Đã stress fixture tổng hợp 30 phút 24 giây ở 640×360, chưa đo 12 giờ hoặc phần trăm tiết kiệm trên máy chuẩn. Có thể đặt `render.segment_cache_max_bytes` để dọn segment cũ không dùng; `render.min_free_disk_bytes` mặc định 256 MiB. Đây là kiểm tra giữa các bước, không phải quota ổ đĩa; không dọn cache TTS/ảnh trả phí.
- Burn-in phụ đề cần thêm lượt encode cuối. Muốn tiết kiệm CPU, tắt burn-in, dùng SRT riêng. Provider Edge dùng word timing; command Piper có sidecar SHA và mốc từ từ tensor phoneme, ghi rõ fallback theo câu nếu không khớp token. Provider thiếu timing dùng ước lượng theo lời đọc.
- Với video nhiều giờ, điều chỉnh `render.timeout_s` và `STUDIO_QC_TIMEOUT_S` theo máy; mặc định QC 300 giây. Ví dụ đặt `STUDIO_QC_TIMEOUT_S=86400` trước khi khởi động worker. Không tắt timeout hoàn toàn.
- Quota YouTube Data API tính theo rổ từ 6/2026: mặc định khoảng 100 lượt `videos.insert` mỗi ngày cho một project Google Cloud, các lệnh khác dùng rổ 10 000 đơn vị. Xưởng nhiều kênh chung một project sẽ chạm trần upload trước.
- Ngân sách token là token model text. Tiền TTS/ảnh/clip đi qua sổ chi phí media riêng (ADR-0016): ước tính theo bảng giá bạn khai, không phải hóa đơn provider; không khai giá thì không có trần tiền. Ngân sách token dừng trước chương tiếp theo khi đã hết; một request đang chạy có thể vượt phần còn lại. Chất lượng sáng tạo, tính đúng đắn nội dung và thời lượng thực phụ thuộc model/provider; QC giữ lại sản phẩm lệch mục tiêu quá 20% mặc định.
- Đồ họa theo cảnh chỉ kiểm con số khớp claim có nguồn, không hiểu nhãn đi kèm có đúng nghĩa không — soát trên contact sheet ở gate publish. Bề rộng chữ là ước lượng theo font DejaVu Sans, không đo glyph thật. Chưa nhúng engine HTML (HyperFrames/Remotion) hay ManimGX: model không viết code chạy được (ADR-0003, ADR-0017).
- Video diffusion chạy qua workflow ComfyUI cấu hình riêng; chưa cài/benchmark model GPU. Chưa tích hợp stock search tự tải, tracking người nói, editor timeline kéo thả hoặc render cluster. Xem [đối chiếu nguồn và quyết định tích hợp](docs/RESEARCH-VIDEO-FACTORIES.md).

## Phát triển và xác minh

```bash
make verify            # lint/typecheck + coverage cả 3 package + eval replay
make studio-demo       # kiểm tra workflow offline
```

CI Linux cài FFmpeg/font, chạy smoke Chromium; job Windows kiểm SQLite/Unicode và dựng fixture ngắn. CI khóa phiên bản GitHub Actions và chạy cùng lệnh. Coverage yêu cầu 100% dòng cho từng package. Test renderer thực kiểm tra footage, nhạc, phụ đề, frame timing, cache và retry; test HTTP kiểm tra auth, Origin/CSRF và byte-range. Các lỗi retry nhánh, resume sau lỗi render, QC chưa hoàn tất và HTTP range được chặn bằng regression test; xem [ADR-0011](docs/adr/0011-recovery-and-bounded-checkpoints.md) và [ADR-0012](docs/adr/0012-durable-render-outbox-and-disk-budgets.md). Khởi động/đóng xưởng dọn khóa khi lỗi; ảnh bìa giữ bản cũ khi encode hoặc biến thể sau thất bại; decoder và filter áp dụng cấu hình luồng xử lý — xem [ADR-0014](docs/adr/0014-factory-cleanup-and-thumbnail-staging.md). Kiểm tra UI desktop/mobile bằng Chromium được ghi tại [VALIDATION.md](docs/VALIDATION.md).

- `src/studio/`: factory, console, orchestration, render, QC, provider và YouTube adapter.
- `src/studio/web/`: HTML/CSS/JavaScript native, đóng gói cùng Python.
- `agents/`, `skills/`, `topics/`: 14 vai, 24 skill và schema sự kiện; giữ prompt hiện có.
- `gateway/`, `xagents-core/`: thành viên uv workspace có test/coverage riêng.
- `tests/`: pytest 1329 ca / 84 file Python.
- Test: 1329 ca pytest của studio; xem báo cáo xác minh cho kết quả thực tế.
- `docs/adr/`: ADR 0001–0024; [ADR-0010](docs/adr/0010-production-factory.md) giải thích kiến trúc factory, [ADR-0013](docs/adr/0013-film-animation-scene-generation.md) bổ sung phim/hoạt hình, [ADR-0022](docs/adr/0022-default-quality-and-native-shorts.md) đưa chất lượng vào mặc định, [ADR-0023](docs/adr/0023-excerpt-ranges-and-rights-evidence.md) giữ mốc cảnh và hồ sơ quyền của bản trích, [ADR-0024](docs/adr/0024-current-export-traceability.md) nối đúng kịch bản và quyền thành phần của bản xuất.

Giấy phép: [Apache-2.0](LICENSE), cùng giấy phép với repo nguồn [`X-Agents`](https://github.com/seeker19110/X-Agents) mà `xagents-core/` và `gateway/` được vendor về.

Tham khảo thêm: [hướng nâng cấp tiếp theo và tiêu chí nghiệm thu](docs/RESEARCH-NEXT-UPGRADES.md), [kiến trúc nền](docs/architecture.md), [CLI chi tiết](docs/CLI-REFERENCE.md), [CLAUDE.md](CLAUDE.md). Các hướng dẫn cũ về gate được bổ sung bởi policy ủy quyền ở ADR-0010.
