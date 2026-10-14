# Nghiên cứu nâng cấp — rà `stored` lần 3 và quét Internet, 2026-10-14

> Đối chiếu kho [`seeker19110/stored`](https://github.com/seeker19110/stored) HEAD `c4cf956` (2026-10-14 06:11 +07, diff
> từ `c66fc21` là HEAD của lần rà 2 trong ADR-0017) với mã X-Studio HEAD `5f5afae`, rồi kiểm tài liệu **chính thức** của
> Google, OpenAI, ElevenLabs, YouTube, FFmpeg trong cùng ngày. Đây là **đề xuất**, chưa sửa code; mỗi mục có bằng chứng
> (file:dòng hoặc URL + ngày) và tiêu chí "kiểm xong khi". Giá tiền là bảng giá công bố ngày đọc, không phải hoá đơn.

## 1. Kết luận ngắn

Ba việc **có hạn chót** và hai việc **tuân thủ nền tảng** đáng làm trước mọi tính năng mới:

| # | Mức | Việc | Hạn / lý do | Bằng chứng |
|---|---|---|---|---|
| 1 | 🔴 | TTS Gemini: đổi model mặc định và **đổi API** (generateContent → Interactions, style ra khỏi `text`) | `gemini-2.5-flash-preview-tts` tắt **17/11/2026** | `src/studio/media.py:539,549`; [deprecations](https://ai.google.dev/gemini-api/docs/deprecations) |
| 2 | 🔴 | Nâng `edge-tts` 7.2.8 → ≥ 7.3.1 | 7.3.0 (13/10/2026) thêm token Sec-MS-GEC **v2**; bản cũ bị 403 khi Microsoft xoay token | `uv.lock:417-418`; [releases](https://github.com/rany2/edge-tts/releases) |
| 3 | 🔴 | Khai báo `status.containsSyntheticMedia` khi đăng | YouTube **tự gắn nhãn AI** từ 5/2026 nếu kênh không khai; phim/hoạt hình photorealistic phải khai | `src/studio/platform.py:400`; [blog.youtube 27/5/2026](https://blog.youtube/news-and-events/improving-ai-labels-for-viewers-and-creators/) |
| 4 | 🟠 | ElevenLabs mặc định `eleven_multilingual_v2` **không có tiếng Việt** | kênh tiếng Việt đọc sai ngôn ngữ | `media.py:569`, `media.example.yaml:29`; [models](https://elevenlabs.io/docs/overview/models) |
| 5 | 🟠 | Ảnh Gemini: `imagen-4.0-generate-001` **đã tắt 17/8/2026** (vẫn trong ví dụ), `gemini-2.5-flash-image` tắt 15/3/2027 | ví dụ cấu hình dẫn người dùng vào model chết | `media.example.yaml:65,68`, `media.py:744` |

Từ `stored` lần này chỉ có **hai** mục mới chạm xưởng (§2); phần lớn giá trị đợt này đến từ thay đổi của chính các
provider và YouTube (§3).

## 2. `stored`: gì mới từ `c66fc21` → `c4cf956`

11 PR (#20–#30), +1.364 dòng, 18 file. Đọc diff từng mục, không đọc văn xuôi tổng.

| Mục | Mới | Liên quan X-Studio | Kết luận |
|---|---|---|---|
| 19 §1b OpenCreator (KrillinAI) | Daemon loopback + Codex CLI; **một máy trạng thái cho cả UI lẫn hội thoại**, mỗi lần sửa là một phiên bản; 7 skill điều khiển CLI; telemetry desktop bật mặc định | Xưởng đã có: bus SQLite là nguồn sự thật, console và CLI gate cùng ghi vào đó, manifest có version (ADR-0004) | Không có gì để lấy thêm. Ghi nhận mẫu "skill điều khiển CLI nội bộ" trùng với `cli_visual`/`gate_cli` |
| 19 §1c waoowaoo | Đổi từ "tiểu thuyết → phim ngắn" (CC BY-NC-SA) thành workspace ảnh/video (ELv2); **prompt cài đặt cho agent**: ghim digest, hỏi trước khi tốn tiền, không hỏi key trong chat; Assistant chạy container có hạn mức | Ta chưa có `AGENTS.md` (mục #3 của [ghi chú 13/10](2026-10-13-goi-y-nang-cap.md) còn mở) | **Lấy**: viết `AGENTS.md` ngắn theo tinh thần này (§4) |
| 22 §9d whiteboard-animation-vi | Ảnh tĩnh → video "tay vẽ bảng trắng", Gradio cục bộ, ISNet tách nền; CC0 chỉ phủ mã gốc, **ảnh bàn tay và tranh demo chưa có giấy phép** | Là một kiểu `scene_video` cho explainer giáo dục, chạy được qua `scene_video.provider: command` (ADR-0013) mà không thêm dependency | Tuỳ chọn, 🟢: nếu muốn thử, thay ảnh tay bằng ảnh có quyền rồi khai `license: owned` |
| 20 §3f social-media-skills | 6 repo trùng tên; bộ charlie947 chỉ **soạn nháp, không tự đăng**, hỏi trước khi chạy tool trả phí | Trùng nguyên tắc gate `replies` và "hỏi trước lượt gọi tốn tiền" (ADR-0016) | Không lấy |
| 21 §10b open-instinct | 6 mức tin cậy kiểm ở `beforeToolCall` + audit; nội dung ngoài bọc nhãn "untrusted" | Xưởng đã có ACL theo actor trên bus (`tests/test_bus_acl.py`), guard injection chuẩn hoá ký tự (TRAPS K3.4) | Không lấy |
| 23 §8.1 | Ma trận đo 11–13/10 vẫn ghi X-Studio **không** Dependabot, **không** SECURITY.md | HEAD đã có cả hai từ `1e9ca1b` (#13) | Kho `stored` cần đo lại; phía ta chỉ còn `AGENTS.md` và LICENSE |
| 14 §3 · 18 §3 (không đổi, chưa từng đối chiếu) | TTS mở: Kokoro/Chatterbox/Audio8 **không có tiếng Việt**; tiếng Việt thương mại được chỉ còn **VieNeu-TTS v3** (Apache-2.0, 25 giọng, CPU GGUF/ONNX; v4 đóng); F5-TTS-Vietnamese và viXTTS weights cấm thương mại; **PhoWhisper** (BSD-3, WER 4,67 VIVOS) cho ASR | `tts.provider: command` đã nhận mọi CLI cục bộ; chưa có ASR nào trong QC | **Lấy**: ghi VieNeu-TTS vào ví dụ `command` thay Piper (Piper không có giọng Việt chất lượng); PhoWhisper là ứng viên cho §3.9 |

Ba mục ADR-0016 ghi "chưa làm từ kho" (storyboard gate, beat sync nhạc, cảnh báo 80 % ngân sách media): vẫn chưa làm;
chỉ cảnh báo 80 % là rẻ (`media_cost.py:100-103` đã có `cap`/`used`, thiếu một dòng audit `media.cost_warning`).

## 3. Internet: nền tảng và provider đã đổi so với mã hiện tại

### 3.1 🔴 Gemini TTS: model tắt 17/11/2026 và API đổi hẳn

- [Deprecations](https://ai.google.dev/gemini-api/docs/deprecations) (đọc 14/10/2026): `gemini-2.5-flash-preview-tts`,
  `gemini-2.5-pro-preview-tts` và `gemini-3.1-flash-tts-preview` tắt sớm nhất **17/11/2026**; thay bằng
  `gemini-3.8-flash-tts` / `gemini-3.8-flash-lite-tts`.
- [Speech generation](https://ai.google.dev/gemini-api/docs/speech-generation): 3.8 gọi qua
  `POST /v1beta/interactions` (không phải `generateContent`), `response_format: {type: audio}`,
  `generation_config.speech_config: [{voice: Kore}]`. **`text` là transcript đọc nguyên văn**; phong cách và nhịp đi
  trong `annotations: [{type: speech_metadata, style: "..."}]`; tạm dừng bằng `<short pause>`/`<long pause>` trong
  transcript; không có tham số speed. Unary trả **WAV** 24 kHz mono 16-bit (trước là PCM thô). Tiếng Việt có.
- Mã hiện tại (`media.py:541-557`): nối câu dẫn phong cách vào `text` (`f"{style}: {text}"`) rồi gọi
  `generateContent`. Trên 3.8, câu dẫn đó **sẽ bị đọc thành tiếng**. Nhánh `"wav" in mime` đã có nên WAV không vỡ, nhưng
  thời lượng đo bằng `audio_duration` thay vì đếm mẫu.
- Giá ([pricing](https://ai.google.dev/gemini-api/docs/pricing), tới 31/12/2026): flash 9 USD/1M token audio
  (25 token/giây → ≈ 0,0023 USD mỗi 10 s, ≈ 0,08 USD cho 6 phút đọc), lite 6 USD/1M; **tăng gấp đôi từ 1/1/2027**.
  Sổ chi phí (ADR-0016) tính theo `price_per_1k_chars`; với Gemini nên cho phép thêm `price_per_second` ở `tts`.
- Thêm được nhờ 3.8: **voice design** từ mô tả chữ và **custom voice lưu 1 năm** (200 giọng/project) → "giọng kênh"
  cố định; **2 người nói/một request** → thoại phim (ADR-0013 ghi "một giọng kể" là giới hạn v1).

*Việc:* viết `GeminiTTS` v2 (Interactions API, `speech_metadata`, pace slow/fast → `style`), giữ nhánh cũ cho model
`2.5-*` tới ngày tắt; đổi mặc định sang `gemini-3.8-flash-lite-tts`; `media.example.yaml:23`. TDD với HTTP giả như
`tests/test_media_providers.py`. *Kiểm xong khi:* test ghi lại request Interactions đúng hình dạng và câu dẫn phong
cách **không** nằm trong `text`.

### 3.2 🔴 `edge-tts` 7.2.8 bị khoá, 7.3.x đã đổi token

[Releases](https://github.com/rany2/edge-tts/releases): 7.3.0 (13/10) "add Sec-MS-GEC v2 token support", 7.3.1 sửa parse
ngày theo locale. Luồng Website → Video (`website_render.py:75-81`) và ví dụ `tts.provider: command` đều dựa vào
edge-tts; `uv.lock` ghim 7.2.8. Khi Microsoft xoay token, bản cũ nhận 403 (cộng đồng pyvideotrans ghi nhận thêm giới
hạn tần suất). *Việc:* `uv lock --upgrade-package edge-tts` rồi `make verify`. *Kiểm xong khi:* `uv.lock` ≥ 7.3.1 và
một lượt Website → Video thật chạy qua. Lâu dài: Edge TTS không có SLA; coi là tiện ích, không phải provider sản xuất
— README đã nói "Edge cần Internet", nên nói thêm "có thể vỡ bất kỳ lúc nào".

### 3.3 🔴 Công bố nội dung tổng hợp (`containsSyntheticMedia`) — tuân thủ YouTube

- [Video resource](https://developers.google.com/youtube/v3/docs/videos): `status.containsSyntheticMedia` (có từ
  30/10/2024) cho chủ kênh khai nội dung **Altered/Synthetic thực tế** (người thật nói/làm điều không có; sự kiện/địa
  điểm thật bị sửa; cảnh thực tế chưa từng xảy ra). Đặt được trong `videos.insert`/`videos.update`.
- [blog.youtube 27/5/2026](https://blog.youtube/news-and-events/improving-ai-labels-for-viewers-and-creators/): từ
  5/2026 YouTube **tự gắn nhãn** khi phát hiện AI photorealistic mà kênh không khai; nhãn hiện dưới player (dài) hoặc
  phủ lên Shorts; nội dung hoạt hình/không thực tế chỉ hiện trong mô tả; nhãn là **vĩnh viễn** với video có metadata
  C2PA "fully generative" hoặc làm bằng Veo/Dream Screen; nhãn không ảnh hưởng đề xuất/kiếm tiền.
- Mã hiện tại (`platform.py:400`): `status` chỉ có `privacyStatus`, `selfDeclaredMadeForKids`, `publishAt`.
  `MetadataPackage` (`events.py:188-197`) không có trường nào về nội dung tổng hợp; preflight (ADR-0005) không kiểm.
- *Việc (cần ADR, chạm schema):* thêm `synthetic_media: bool | None` vào `metadata-packages` + `events.py`; **code
  điền**, không phải model: `True` khi manifest có cảnh `generated_video` hoặc `cinema.visual_style` photorealistic với
  asset `generated_by` là model ảnh/video; `False` cho explainer đồ hoạ template/minh hoạ vector; `None` → preflight
  `warn` "chưa khai" và checklist gate publish có dòng `synthetic_media:`. Adapter đặt `status.containsSyntheticMedia`.
  *Kiểm xong khi:* test platform ghi lại body `videos.insert` có trường này; demo film/animation ra `True`.
- Cùng họ: [YPP "inauthentic content"](https://support.google.com/youtube/answer/1311392) (đổi tên 15/7/2025): "AI-generated
  content made with generic or unoriginal templates giving the impression of mass production" không được kiếm tiền.
  Code không đo được "góc nhìn riêng"; chỗ đúng là `skills/content-policy.md` (hiện không nhắc) và checklist gate
  `plan`. Sửa skill = ghi lại eval với model thật (CLAUDE.md).

### 3.4 🟠 Ảnh: đổi mặc định và thêm `image_size`

- Gemini ([image generation](https://ai.google.dev/gemini-api/docs/image-generation), [pricing](https://ai.google.dev/gemini-api/docs/pricing)):
  `gemini-nano-banana-2.1` (0,0336 USD/ảnh 1K, 0,0504 2K, 0,113 4K), `gemini-3.1-flash-lite-image` (0,0336, chỉ 1K),
  `gemini-3-pro-image` (0,134 1K/2K, 0,24 4K); tham số `imageSize: "1K" | "2K" | "4K"` (chữ K **hoa**); tới 14 ảnh
  tham chiếu; có SynthID. `gemini-2.5-flash-image` (mặc định `media.py:744`) tắt 15/3/2027; `imagen-4.0-generate-001`
  (ví dụ `media.example.yaml:65`) **đã tắt 17/8/2026**. Nhánh `startswith("imagen")` giữ để tương thích Vertex.
- OpenAI ([image guide](https://developers.openai.com/api/docs/guides/image-generation)): model hiện tại
  `gpt-image-2.5-sunburst`/`-flare`; ngoài 3 kích thước khuyến nghị còn nhận **WIDTHxHEIGHT tuỳ ý** (bội số 16, tỷ lệ
  1:3…3:1, cạnh ≤ 3840); `quality` low…max. `MODEL_SIZES` (`media.py:72-77`) chỉ biết `gpt-image-1`/`dall-e`; model mới
  rơi về `DEFAULT_SIZES` 1536x1024 — vẫn hợp lệ, nhưng 16:9 thật (1920x1088) sẽ hết phải `cover`-crop.
- *Việc:* mặc định Gemini → `gemini-3.1-flash-lite-image` (rẻ nhất, đủ cho ảnh cảnh), `image.image_size` → `imageConfig.imageSize`;
  thêm `gpt-image-2`-trở-lên vào `MODEL_SIZES` với 1920x1088 / 1088x1920; sửa ví dụ và `docs/CLI-REFERENCE.md:86`.
  *Kiểm xong khi:* test `image_size()` cho model mới và request Gemini có `imageSize`.

### 3.5 🟠 ElevenLabs: tiếng Việt và mốc thời gian

[Models](https://elevenlabs.io/docs/overview/models): `eleven_multilingual_v2` **không** có tiếng Việt; `eleven_v4`
(10k ký tự), `eleven_v3` (5k), `eleven_flash_v2_5` (40k, rẻ hơn 50 %) có; `eleven_turbo_v2_5` đã deprecated.
Endpoint [`/with-timestamps`](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps) trả
`alignment.characters` + `character_start_times_seconds` (mức **ký tự**) → ghép thành mốc từng từ cho phụ đề karaoke
(DANH-GIA §4 B3 "còn thiếu"). *Việc:* mặc định `eleven_flash_v2_5`, `media.example.yaml:29` ghi rõ v2 không có tiếng
Việt; tuỳ chọn `tts.timestamps: true` → `MediaResult` mang `word_times`, `timeline.srt` dùng khi có. *Kiểm xong khi:*
SRT từ mốc thật lệch ≤ 300 ms so với audio (ngưỡng DANH-GIA §8).

### 3.6 🟠 Sinh video hosted: Veo 3.1 GA và Gemini Omni 1.1 Flash — cùng key Gemini đang dùng

| | Veo 3.1 ([docs](https://ai.google.dev/gemini-api/docs/veo)) | Gemini Omni 1.1 Flash ([docs](https://ai.google.dev/gemini-api/docs/omni)) |
|---|---|---|
| Model id | `veo-3.1-generate-001`, `veo-3.1-fast-generate-001` (GA, Vertex ghi 17/11/2025); các id `-preview` **tắt 22/10/2026** | `gemini-omni-1.1-flash` (`gemini-omni-flash-preview` tắt 22/10/2026) |
| Gọi | `:predictLongRunning` → poll operation ~10 s | `POST /v1beta/interactions`, `response_format: {type: video}`; > 4 MB thì `delivery: uri` rồi tải `files/{id}:download` |
| Tham số | `aspectRatio` 16:9/9:16; `resolution` 720p/1080p/4k; `durationSeconds` 4/6/8; `seed`; `image` (I2V), `lastFrame`, **`referenceImages` ≤ 3** (nhân vật/sản phẩm); `video` để nối dài tới 148 s | 16:9/9:16; 360p/720p/1080p/4k (upscale); ảnh tham chiếu nhiều; 3 clip tham chiếu ≤ 3 s; nối dài 3–10 s/lượt tới 40 s; không có negative prompt riêng |
| Âm thanh | luôn sinh kèm | sinh kèm theo prompt |
| Giá (paid, có audio) | 720p: standard 0,40 · fast 0,10 · lite 0,05 USD/giây; 4K 0,60 / 0,30 | ≈ 0,10 USD/giây 720p (5.792 token/giây) |
| Khác | SynthID; lưu 2 ngày; không tính tiền clip bị chặn | SynthID; `store=false` thì không sửa tiếp được; EEA/UK hạn chế sửa video tải lên |

`video_gen.py` hiện chỉ có `fake | command | comfyui`. Một adapter `scene_video.provider: veo | omni` dùng lại
`GEMINI_API_KEY`, `price_per_second` (ADR-0016), cache theo `prompt+seed+resolution+duration` (ADR-0013), `license:
generated`, và **tự đặt `synthetic_media=True`** (§3.3) vì đầu ra có SynthID. `referenceImages` của Veo trả lời đúng
giới hạn "text bible và seed không khoá danh tính nhân vật" của ADR-0013: code gửi 1–3 ảnh nhân vật từ `production.library`.
Chi phí tham khảo: phim 3 phút = 36 shot × 5 s × 0,05 USD (lite) ≈ 9 USD; standard ≈ 72 USD — trần
`production.media_budget_usd` đã có đường dừng. Các API khác (Kling ~0,08–0,17 USD/s, Runway Gen-4.5 ~0,12 USD/s;
Sora 2 **đã ngừng** 24/9/2026 theo nguồn thứ cấp) chỉ nên nhận qua `command` của người vận hành, không thêm key mới.
*Kiểm xong khi:* test HTTP giả cho poll/timeout/tải file, clip thật ≥ 1 qua ffprobe, dòng `media.cost` đúng giá khai.

### 3.7 🟢 YouTube Data API: quota, thumbnail, thống kê

[Revision history](https://developers.google.com/youtube/v3/revision_history) + [Getting started](https://developers.google.com/youtube/v3/getting-started):

- **1/6/2026** quota theo bucket riêng: mặc định **100 `videos.insert`/ngày** và 100 `search.list`, 10.000 unit cho phần
  còn lại; upload giảm từ ~1.600 unit (4/12/2025) xuống 1 unit trong bucket riêng. README "Giới hạn" và
  `docs/CLI-REFERENCE.md` chưa nói về quota → thêm một dòng; không cần đổi code.
- **14/9/2026** thumbnail/ảnh playlist nhận tới **50 MB**; **11/9** có kích thước 1080p/1440p/4K. `THUMBNAIL_MAX_BYTES`
  = 2 MB vẫn là trần tốt cho renderer, nhưng `platform.py:421` **từ chối** file > 2 MB là luật đã cũ → hạ thành `warn`
  trong QC (`qc.py:222`), bỏ `PlatformError`; ghi chú ở `media.py:78`.
- **3/6/2026** `videos.batchGetStats` (1 unit, bucket riêng 10.000/ngày): lấy view/like của nhiều video một lượt —
  rẻ hơn `videos.list` cho `sync-metrics` khi chỉ cần số công khai (Analytics API vẫn cho AVD/CTR).
- **24/8/2026** cách đếm view thống nhất cho dài/Live/Shorts (đếm khi bắt đầu phát) → `performance-snapshots` trước/sau
  ngày này không so trực tiếp; `youtube-analytics` skill nên ghi chú.
- **7/7/2026** part `brandPartner`; `paidProductPlacementDetails.hasPaidProductPlacement` → nếu kênh có tài trợ, metadata
  phải khai; hiện không có trường → gom vào cùng ADR với §3.3.
- **11/2026** comment có `snippet.imageUrl` (ảnh/GIF) → `sync-comments` giữ nguyên, nhưng `audience-comments` nên có
  trường tuỳ chọn để community-manager biết bình luận kèm ảnh (ảnh không bao giờ gửi cho model, ADR-0007).

### 3.8 🟢 Analytics API

[Revision history](https://developers.google.com/youtube/analytics/revision_history): 9/3/2026 `ageGroup` thêm nhóm dưới
18; 25/6/2026 bỏ báo cáo theo thành phố cho content owner (kênh không ảnh hưởng); giới hạn `videos × days ≤ 50.000` cho
traffic source (từ 11/2024). `sync-metrics` chạy theo một video nên không chạm; chỉ cần ghi vào skill `youtube-analytics`.

### 3.9 🟠 FFmpeg 8.x: bộ lọc `whisper` cho QC "đọc đúng narration"

FFmpeg 8.0 (22/8/2025) có bộ lọc `whisper` (whisper.cpp, build `--enable-whisper`, chạy cục bộ); 8.1 "Hoare" 16/3/2026,
8.1.2 17/6/2026 ([changelog](https://fossies.org/linux/ffmpeg/Changelog)). Dùng:
`-af "whisper=model=ggml-….bin:language=vi:queue=10:destination=out.srt:format=srt"` hoặc `format=json` — mốc theo
**đoạn**, không theo từ. Đây là đúng "lớp code" C1 của DANH-GIA §5 còn thiếu: so transcript với narration (WER theo
cảnh) để bắt TTS đọc thiếu/sai, không cần dependency Python. Model tiếng Việt: ggml multilingual hoặc PhoWhisper
chuyển sang ggml (`stored` 18). *Việc:* `qc.py` thêm bước tuỳ chọn khi `ffmpeg -filters` có `whisper`
(`STUDIO_QC_ASR_MODEL`), finding `warn` khi WER cảnh > ngưỡng; không có thì báo "không đo", không đoán. Phụ đề
karaoke từng từ vẫn phải lấy từ TTS (§3.5) hoặc edge-tts `WordBoundary` (đã có trong luồng website, chưa có trong luồng
dự án).

### 3.10 🟢 OpenAI TTS

[Guide](https://developers.openai.com/api/docs/guides/text-to-speech): `gpt-4o-mini-tts` vẫn là model mới nhất; thêm
giọng `marin`, `cedar` (khuyên dùng); `instructions` giữ nguyên; **không** có timestamps; điều khoản bắt công bố giọng
AI với người nghe (khớp §3.3). Không cần đổi code; ví dụ có thể đổi `voice: marin`.

## 4. Việc tồn từ các đợt trước (đối chiếu lại)

| Việc | Nguồn | Trạng thái 14/10 |
|---|---|---|
| Dependabot (uv, github-actions) | ghi chú 13/10 #1 | ✅ `.github/dependabot.yml` (từ `1e9ca1b`) |
| SECURITY.md · LICENSE | ghi chú 13/10 #2 | ✅ SECURITY.md · ❌ LICENSE (hoặc dòng "giữ mọi quyền" trong README) |
| AGENTS.md | ghi chú 13/10 #3 | ❌ — viết ≤ 40 dòng: ba điều không được phá, TDD, "hỏi trước khi tốn tiền", không hỏi key trong chat (học waoowaoo §1c) |
| Cảnh báo 80 % ngân sách media | ADR-0016 | ❌ — một dòng audit `media.cost_warning` + console |
| Reviewer đa phương thức (C1 lớp model) | DANH-GIA §5, ADR-0010 | ❌ — vẫn đúng hướng; contact sheet (ADR-0016) là đầu vào sẵn |
| Storyboard gate, beat sync | ADR-0016 | giữ "không làm" |

## 5. Lộ trình đề xuất

| Đợt | Nội dung | ADR | Khi nào |
|---|---|---|---|
| A — bảo trì có hạn | §3.2 edge-tts; §3.1 GeminiTTS v2; §3.4 mặc định ảnh + `image_size`; §3.5 mặc định ElevenLabs; §3.7 thumbnail 50 MB; sửa `media.example.yaml`, CLI-REFERENCE | không (không đổi schema/prompt) | **trước 17/11/2026** |
| B — tuân thủ nền tảng | §3.3 `synthetic_media` (schema + preflight + adapter + checklist), `hasPaidProductPlacement`; §3.6 adapter `veo`/`omni` tự đặt cờ | **ADR-0018** | sau A |
| C — tai cho QC | §3.9 whisper trong `qc.py`; §3.5 timestamps → SRT karaoke; `tts.provider: edge` native với `WordBoundary` cho luồng dự án | **ADR-0019** | khi có máy ffmpeg ≥ 8.0 build whisper |
| D — vệ sinh | AGENTS.md, LICENSE, cảnh báo 80 %, skill `content-policy` thêm "inauthentic content" (ghi lại eval) | không | bất kỳ |

Đợt A không chạm prompt agent nên không cần eval model thật; đợt B và D (skill) phải ghi lại eval theo CLAUDE.md.

## 6. Nguồn đã đọc (14/10/2026)

- `stored`: README, WORKFLOW.md, `git diff c66fc21..c4cf956` các mục 19, 20, 21, 22, 23; mục 14 §3, mục 18 §3.
- Google: [deprecations](https://ai.google.dev/gemini-api/docs/deprecations) · [speech-generation](https://ai.google.dev/gemini-api/docs/speech-generation) · [image-generation](https://ai.google.dev/gemini-api/docs/image-generation) · [veo](https://ai.google.dev/gemini-api/docs/veo) · [omni](https://ai.google.dev/gemini-api/docs/omni) · [pricing](https://ai.google.dev/gemini-api/docs/pricing) · [Vertex Veo 3.1](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate).
- YouTube: [Data API revision history](https://developers.google.com/youtube/v3/revision_history) · [getting started / quota](https://developers.google.com/youtube/v3/getting-started) · [videos resource](https://developers.google.com/youtube/v3/docs/videos) · [Analytics revision history](https://developers.google.com/youtube/analytics/revision_history) · [AI labels 27/5/2026](https://blog.youtube/news-and-events/improving-ai-labels-for-viewers-and-creators/) · [YPP policies](https://support.google.com/youtube/answer/1311392).
- OpenAI: [text-to-speech](https://developers.openai.com/api/docs/guides/text-to-speech) · [image-generation](https://developers.openai.com/api/docs/guides/image-generation).
- ElevenLabs: [models](https://elevenlabs.io/docs/overview/models) · [with-timestamps](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps).
- FFmpeg: [8.0 whisper (Phoronix)](https://www.phoronix.com/news/FFmpeg-8.0-Released) · [dùng bộ lọc whisper](https://dev.to/masonwritescode/ffmpeg-80-in-practice-transcribe-with-the-whisper-filter-and-probe-av1vulkan-5c20) · [8.1.2 (Ubuntu Handbook)](https://ubuntuhandbook.org/index.php/2026/06/ffmpeg-8-1-2-over-hundred-fixes/).
- edge-tts: [releases](https://github.com/rany2/edge-tts/releases) · [issue #458](https://github.com/rany2/edge-tts/issues/458).
- Giá video bên thứ ba (chỉ tham khảo, nguồn thứ cấp mâu thuẫn nhau): [CometAPI](https://www.cometapi.com/ai-video-api-pricing/) · [buildmvpfast](https://www.buildmvpfast.com/api-costs/ai-video) · [modelslab](https://modelslab.com/blog/api/veo-3-1-vs-kling-3-sora-2-ai-video-api-cost-2026).
