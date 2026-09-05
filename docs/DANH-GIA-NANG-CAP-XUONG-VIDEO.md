# Đánh giá và lộ trình nâng cấp xưởng sản xuất video (Studio-creators)

Ngày: 2026-09-04 · Phạm vi: lớp sản xuất thật (scene manifest → TTS → ảnh → ghép → sửa cảnh → review → thumbnail → đăng).
Mục tiêu: ra được **video thật** (chạy với provider thật không lỗi) và **chất lượng xuất sắc** (đo được, không cảm tính).

## 1. Kết luận

Phần điều phối của xưởng đã ở mức tốt: event-driven có schema, scene manifest bền vững sửa từng cảnh, provenance mọi asset,
ba review độc lập, human gate approval-first, resume từ SQLite, eval ghi/phát lại. Điểm nghẽn để ra video thật chất lượng
cao nằm ở **hai lớp cuối**:

1. **Lớp render** (`media.py`, `renderer.py`) là *slideshow ảnh tĩnh + giọng đọc*: mỗi cảnh một ảnh đứng im, cắt cứng,
   không nhạc, không phụ đề, không chuẩn hoá âm lượng (mục 4). Năm lỗi khiến lượt chạy provider thật đầu tiên hỏng hoặc
   sai khung đã được sửa và kiểm chứng bằng ffmpeg thật (mục 3).
2. **Lớp "mắt và tai"**: editor, quality-reviewer, rights-checker chỉ nhận JSON đường dẫn file. `qc.py` (mục 4) đã bù
   phần đo được bằng máy — khung hình, thời lượng, âm lượng, hình đen, khoảng lặng — nhưng những gì phải NHÌN mới biết
   ("ảnh khớp prompt", "không chữ/khuôn mặt thật trong ảnh") thì vẫn chưa ai kiểm được trước gate publish (mục 5).

Khuyến nghị: P0 và P1 (ADR-0009) đã xong. Còn lại theo thứ tự: reviewer đa phương thức + preview cho người duyệt
(ADR-0010, mục 5) → thư viện nhạc/footage có license và giá media (mục 6). ADR-0010 là bước duy nhất phải chạm front
matter của agent, nên phải ghi lại eval bằng model thật.

## 2. Hiện trạng lớp sản xuất (những gì đã có)

| Thành phần | Có | Thiếu |
|---|---|---|
| TTS | `openai`, `gemini`, `elevenlabs`, `azure`, `google`, `command`, `fake` *(5 provider sau thêm ở đợt này)* | provider có timestamps từng từ (phụ đề karaoke); cache audio theo hash narration |
| Ảnh | `openai`, `gemini` (Gemini Image / Imagen), `stability`, `replicate`, `fake` *(3 provider sau thêm ở đợt này)*; kích thước hợp lệ theo model | neo phong cách (seed/ảnh tham chiếu), ép `brand` vào prompt bằng code |
| Ghép video | một lệnh ffmpeg: chuyển động từng cảnh, `xfade`/`acrossfade`, `loudnorm` -14 LUFS, khung theo `aspect` | nhạc nền có license + ducking |
| Sửa cảnh | `apply_cutlist`: sinh lại đúng cảnh, khoá, thay asset, đổi thứ tự, ≤ 3 vòng | kiểm asset còn tồn tại; cache TTS theo nội dung |
| Thumbnail | 2–3 biến thể; nền do model ảnh vẽ (không chữ), code phủ chữ, 1280x720 JPEG ≤ 2 MB | đo độ đọc được ở 120 px; ảnh nền theo `brand` |
| Review | 3 agent độc lập; số đo file thật (QC) nằm trong `package` của quality-reviewer | vẫn không THẤY ảnh / NGHE audio (mục 5) |
| QC file | `qc.py`: ffprobe + ebur128/blackdetect/silencedetect → `package` + checklist gate | đo độ đọc được của thumbnail, so ảnh với prompt |
| Đăng | upload private, thumbnail (chặn > 2 MB), phụ đề (`captions.insert`), publishAt, comments, analytics | playlist, Shorts flag, đổi lịch/gỡ video |
| Duyệt | gate publish có checklist, đường dẫn file | xem bản nháp/final trong console |

## 3. Lỗi phải sửa ngay (P0) — ĐÃ SỬA (đợt 2026-09-04)

Năm lỗi này chặn chính việc "ra video thật": với cấu hình mẫu, lượt render thật đầu tiên hoặc là lỗi HTTP, hoặc ra file
sai khung, cụt tiếng. Tất cả đã sửa và kiểm chứng bằng ffmpeg thật (test đánh dấu `skipif` khi máy không có ffmpeg).

**A1. Kích thước ảnh không hợp lệ với `gpt-image-1`.** Mặc định cũ `1792x1024` là kích thước của DALL-E 3; `gpt-image-1`
chỉ nhận `1024x1024`, `1536x1024`, `1024x1536` → HTTP 400 ngay lượt đầu.
*Đã sửa:* `media.image_size(cfg, aspect)` tra bảng `MODEL_SIZES` theo model và tỷ lệ khung; cấu hình khai sai được thay
bằng kích thước hợp lệ **cùng tỷ lệ** thay vì để request chết; provider nhận tỷ lệ (gemini, stability, replicate) giữ
nguyên cấu hình. `MediaConfig` không còn `size` mặc định, `media.example.yaml` để `size` ở dạng chú thích.

**A2. Thời lượng cảnh là ước lượng, không đo từ file.** TTS trả `số từ / 2,5`, còn assembler cắt cứng `-t <ước lượng>`:
giọng đọc dài hơn thì **cụt giữa câu**, ngắn hơn thì ảnh đứng im lặng. Sai số lớn với tiếng Việt, và kéo theo chapter,
retention map theo cảnh, metrics của quality-reviewer đều lệch.
*Đã sửa:* `media.audio_duration` đo từ file thật (WAV đọc header, định dạng khác hỏi `ffprobe`), mọi provider TTS dùng nó,
`gemini` trả PCM nên đo tuyệt đối chính xác; assembler bỏ `-t`, để `-shortest` chạy hết giọng đọc rồi `apad` đệm
`tail_pad_s` (0,35 s) im lặng cuối cảnh. Kiểm chứng: audio thật 5 s truyền vào với thời lượng khai 1 s vẫn ra 5,35 s.
Còn lại (P2): cache audio theo hash(narration + voice) để sinh lại ảnh không phải trả tiền đọc lại.

**A3. Shorts 9:16 render sai khung.** Renderer lấy `resolution` từ cấu hình (1920x1080) bất kể `manifest.aspect`, ảnh dọc
bị pad thành khung ngang với hai dải đen.
*Đã sửa:* `media.frame_size(video, aspect)` hoán đổi chiều cho `9:16`; renderer dùng nó ở cả ba chỗ (`render`,
`apply_cutlist`, `finalize`). Kiểm chứng bằng ffprobe: manifest 16:9 ra 1920x1080, manifest 9:16 ra 1080x1920.
Kèm theo: `fit: cover` (mặc định) cho ảnh lấp đầy khung, nên ảnh 3:2 hay 1:1 của provider không còn tạo viền đen;
`fit: contain` giữ hành vi cũ khi muốn giữ trọn ảnh.

**A4. Thumbnail nhờ model ảnh vẽ chữ.** Prompt cũ nối `Overlay text: …`, trái skill `visual-direction`/`thumbnail-design`;
model ảnh viết sai dấu tiếng Việt. Kèm: không thu về 1280x720, không kiểm ≤ 2 MB.
*Đã sửa:* model ảnh chỉ vẽ **nền không chữ** (`A_base.png`, prompt kèm điều cấm chữ/logo/watermark); code phủ chữ bằng
`drawtext` — viết hoa, ngắt ≤ 3 dòng, cỡ chữ tính theo bề rộng khung, viền đen, đặt ở 72% chiều cao để tránh góc phải
dưới nơi nền tảng đè thời lượng — rồi xuất `A.jpg` 1280x720, hạ chất lượng dần cho tới khi ≤ 2 MB. Chữ và font đi qua
`textfile=`/`fontfile=` trong thư mục tạm dùng làm cwd nên không phải escape dấu `:`/`'` của đường dẫn hay của tiếng Việt.
Thiếu font thì ảnh vẫn đúng kích thước, `MediaResult.notes` và audit `thumbnail.finish` nói rõ là không phủ được chữ;
chữ quá dài bị cắt dòng cũng được ghi lại chứ không âm thầm nuốt. `YouTubePlatform.set_thumbnail` từ chối file > 2 MB
trước khi gọi API, để evidence nói đúng nguyên nhân thay vì HTTP 400 của Google.

**A5. `voice.pace` và `language` bị bỏ qua.** Skill `narration-tts` quy định pace medium/fast/slow (YMYL đọc chậm) nhưng
lớp TTS chỉ gửi `model`, `voice`, `input`.
*Đã sửa:* `pace` map sang `speed` (openai), `speakingRate` (google), `<prosody rate>` (azure), `voice_settings.speed`
(elevenlabs) và câu dẫn phong cách (gemini); `tts.instructions` cho openai; bảng `tts.voices` dịch `voice_id` của manifest
sang giọng của provider đang dùng, vì production-manager không biết trước nhà cung cấp nào sẽ đọc.

**E1. File trung gian lẫn vào asset.** `_seg000.mp4`, `_concat.txt` nằm cùng `output/<video_id>/`.
*Đã sửa:* segment và danh sách concat nằm trong thư mục tạm, xoá trong `finally`; `output/<video_id>/` chỉ còn asset thật.

## 4. Thiếu năng lực cốt lõi cho "chất lượng xuất sắc" (P1) — ĐÃ LÀM (đợt 2026-09-05, ADR-0009)

**B1. Video là slideshow ảnh tĩnh.** Trước: `-loop 1 -tune stillimage`, cắt cứng giữa cảnh, không chuyển động, không
chuyển cảnh, không nhạc.
*Đã làm:* cả bản dựng là MỘT lệnh ffmpeg — mỗi cảnh một kiểu chuyển động nhẹ (`motion_for` xoay vòng zoom_in →
pan_right → zoom_out → pan_left nên hai cảnh liền nhau không cùng kiểu; pan bằng cửa sổ `crop` chạy trên ảnh phóng to,
zoom bằng `zoompan`), nối bằng `xfade`/`acrossfade`. Mẹo giữ đồng bộ: `transition_s` bị kẹp ≤ `tail_pad_s` nên phần
chồng lấn luôn nằm trong đoạn đệm im lặng — không câu nào chồng lên câu nào, và hình với tiếng cùng ngắn đi đúng
`transition_s` mỗi mối nối. Tắt bằng `video.motion: none` / `transition_s: 0`.
*Còn thiếu:* nhạc nền có license và ducking dưới giọng đọc (cần thư viện nhạc, mục D4).

**B2. Không chuẩn hoá âm lượng.** *Đã làm:* `loudnorm=I=-14:TP=-1:LRA=11` ở lượt ghép cuối, AAC 192 kb/s 48 kHz.
Kiểm chứng: hai đoạn tiếng vào ở -52,7 và -24,6 LUFS đều ra đúng -14,0 LUFS, đỉnh -1,2 dBFS.

**B3. Không có phụ đề.** *Đã làm:* `timeline.srt` sinh SRT thẳng từ narration của manifest — narration CHÍNH LÀ văn bản
đã đọc nên không cần nhận dạng giọng nói — với mốc thời gian tính bằng đúng công thức assembler ghép. Publish thành
`media-assets` kind `captions`; sau khi upload video, code gọi `Platform.upload_captions` (YouTube `captions.insert`
dạng multipart/related). Phụ đề là phần thêm: lỗi khi đăng nó ghi vào evidence chứ không làm hỏng lượt đăng.
*Còn thiếu:* phụ đề theo từng cụm từ (cần timestamps của provider TTS), phụ đề đốt sẵn cho Shorts.

**B4. Chapter là số đoán.** seo-optimizer viết nhãn trước khi có video nên mốc của nó không thể khớp thời gian thật.
*Đã làm:* `timeline.snap_chapters` nắn từng mốc về đầu cảnh gần nhất, bỏ mốc trùng cảnh, mốc cách nhau < 10 giây và mốc
nằm ngoài video; code publish lại `metadata-packages` dưới actor `chapters` và preflight kiểm lại chính bản đã nắn.
Model đặt tên, code đặt giờ — đúng nguyên tắc ADR-0003.

**B5. Không có QC bằng code trên file thật.** *Đã làm:* `qc.py` đo bản cuối bằng ffprobe + `ebur128`/`blackdetect`/
`silencedetect`: khung hình, fps, thời lượng (so với manifest), LUFS, đỉnh, đoạn hình đen, khoảng lặng, kích thước và
dung lượng thumbnail, có phụ đề hay không. `block` cho thứ không được phép lên nền tảng (mất luồng âm thanh, thời lượng
lệch > 10%, sai khung, thumbnail > 2 MB), `warn` cho phần còn lại. Báo cáo đi vào `package` của quality-reviewer **và**
vào checklist gate publish. Thiếu ffprobe thì báo cáo nói thẳng là không đo được, không đoán bừa.
*Ghi chú:* QC chỉ báo cáo, không tự chặn — quyền quyết định vẫn ở quality-reviewer và người duyệt gate (approval-first).

## 5. Reviewer "mù và điếc" (P1, ảnh hưởng chất lượng lớn nhất)

**C1. Editor, quality-reviewer, rights-checker chỉ nhận JSON.** *(nửa code đã làm, xem cuối mục)* `orchestrator.py:152-166` truyền `manifest` + `scene_assets`
(path, checksum, provenance); `runner.py:89-111` đóng gói mọi thứ thành văn bản; `ModelClient.complete` (`llm.py:100-102`)
không có đầu vào ảnh/audio. Editor phải "kiểm ảnh khớp prompt, narration đọc đúng" mà không thấy ảnh, không nghe audio.
Bản ghi eval thật (`evals/recordings/editor.json`) cho thấy editor tự ghi chú không kiểm được và đẩy việc cho
quality-reviewer, agent cũng mù như nó. Rights-checker không thấy ảnh nên không bắt được logo, chữ, khuôn mặt thật vô tình
sinh ra. Đề xuất hai lớp, giữ nguyên tắc code hành động:

- *Lớp code (rẻ, xác định, chạy trước) — **ĐÃ LÀM** (`qc.qc_scenes`, ADR-0009):* mỗi cảnh được đo độ sáng và tương phản
  của ảnh (`signalstats`), thời lượng và tổng im lặng của giọng đọc, rồi so với số từ của narration. Ảnh gần đen, ảnh một
  màu, TTS trả về im lặng hoặc đọc thiếu đều thành finding có mức và vị trí trong `scene_qc` của payload editor. Kiểm
  chứng bằng file thật: cảnh lành lặn ra 0 finding, ảnh 0x0a0a0a bị bắt (độ sáng 25/255), audio im lặng bị bắt ở mức
  block. *Còn thiếu (cần thêm phụ thuộc):* OCR phát hiện chữ trong ảnh, phát hiện khuôn mặt, nhận dạng lại audio so với
  narration (whisper local).
- *Lớp model (đa phương thức):* mở rộng `ModelClient.complete` nhận `attachments: [{type: image, path}]` (Anthropic image
  block, OpenAI `image_url` base64); agent khai `inputs: [images]` trong front matter; runner gắn ảnh cảnh thu nhỏ (≤ 512 px)
  cho editor / quality-reviewer / rights-checker; provider `claude-code` đọc file ảnh qua tool. Eval ghi/phát lại lưu hash
  ảnh thay nội dung. Editor có thể lên tier `strong` khi có ảnh (chi phí thấp ở 512 px).

**C2. Người duyệt gate publish phải mở file tay.** `gates/checklists.md` yêu cầu "đã xem bản cuối `output/<video>/final_v<n>.mp4`";
console không xem được bản nháp/final (README "chưa có giao diện web xem bản nháp"). Đề xuất: console phục vụ `output/`
chỉ đọc ở màn *Xưởng video* (thẻ video, lưới ảnh từng cảnh với narration, thumbnail A/B, SRT), deep-link từ gate `PUB-*`.

## 6. Lớp provider media còn hẹp (P2)

**D1. TTS — đã mở rộng ở đợt này.** Trước chỉ có OpenAI-compatible (provider khác ném `MediaError`). Nay có `gemini`
(Gemini TTS, tiếng Việt, PCM → WAV đo thời lượng chính xác), `elevenlabs`, `azure` (vi-VN-HoaiMyNeural / NamMinhNeural),
`google` (vi-VN-Neural2), `command` (Piper, Kokoro, edge-tts chạy cục bộ — self-hosted, không tốn tiền, văn bản không rời máy).
Còn thiếu: timestamps từng từ (ElevenLabs `/with-timestamps`, Gemini) để làm phụ đề karaoke; cache audio theo hash narration.

**D2. Ảnh — đã mở rộng ở đợt này.** Nay có `gemini` (Gemini Image `gemini-2.5-flash-image` hoặc Imagen `imagen-*`, kích thước
đổi sang tỷ lệ 16:9 / 9:16), `stability` (core / sd3 / ultra, `negative_prompt`, `style_preset`), `replicate` (Flux, SDXL…,
`Prefer: wait` rồi poll, URL kết quả đi qua ranh giới `check_url`). Còn thiếu: ComfyUI/SD local (có thể đi qua `replicate`-compatible
hoặc thêm một class); nhất quán phong cách bằng **code**: `style_preset` cấp manifest nối tiền tố vào mọi prompt, seed / ảnh tham
chiếu để giữ bảng màu (hiện `brand` chỉ là chữ trên blackboard, không được ép vào prompt).

**D3. Không có footage hay biểu đồ thật.** Cảnh chỉ là ảnh AI. Đề xuất `visual_kind: image | clip | chart`: `clip` lấy b-roll
có license từ Pexels/Pixabay API (ghi `source_url` + license vào provenance); `chart` do code vẽ (matplotlib) từ số liệu
trong claim đã fact-check, giải quyết việc skill cấm số trong ảnh AI vì AI vẽ sai; tuỳ chọn adapter `VideoGen`
(Runway/Kling/Veo) cho một hai cảnh hook.

**D4. Nhạc không có cơ chế.** Skill `media-rights` nói "chỉ thư viện có license ghi trong `rights`" nhưng không có thư viện.
Đề xuất thư mục `music/` kèm `LICENSES.yaml` (tên, license, attribution) → renderer chọn theo `mood` → provenance
`licensed | cc-by` → seo-optimizer ghi công trong mô tả (finding warn đã có đường đi).

**D5. Chi phí media chưa tính tiền** (hệ quả ADR-0003). Thêm `pricing:` trong `media.yaml` (mỗi ảnh, mỗi 1k ký tự TTS, mỗi
giây video) → audit `render.*` ghi USD → supervisor áp ngưỡng 80/100 cho media như token → console hiển thị.

## 7. Vận hành (P2–P3)

- **E2.** `render(only=None)` bỏ qua cảnh `locked` có ≥ 2 `asset_refs` nhưng không kiểm file còn tồn tại/checksum; nên kiểm
  và sinh lại khi mất.
- **E3.** Shorts từ video dài (README "bước tiếp theo"): khi có duration thật + SRT, code cắt đoạn hook 45–60 s, re-frame
  9:16 (crop hoặc sinh lại ảnh dọc cho các cảnh đó), burn phụ đề lớn.
- **E4.** Test: ffmpeg thật cho 9:16, loudnorm, xfade, caption (CI đã cài ffmpeg, `.github/workflows/ci.yml:146`); golden
  cho báo cáo QC trên video giả; test kích thước ảnh theo model.

## 8. Định nghĩa "chất lượng xuất sắc" đo được

Để QC bằng code và quality-reviewer chấm cùng một thước, đề xuất ngưỡng đưa vào `docs/standards.md` và skill `quality-review`:

| Tiêu chí | Ngưỡng |
|---|---|
| Khung hình | 1920x1080 30 fps (long) · 1080x1920 (short); không dải đen |
| Âm thanh | 48 kHz, −14 LUFS ± 1, true peak ≤ −1 dBTP; không khoảng im lặng > 1,5 s; không click |
| Đồng bộ | thời lượng cảnh = thời lượng audio thật + đệm; tổng khớp Σ duration_s ± 1 % |
| Chuyển động | không cảnh tĩnh hoàn toàn > 8 s; hook có hình mới ≤ 3 s |
| Phụ đề | phủ 100 % narration, lệch ≤ 300 ms |
| Chapter | mốc khớp cảnh đầu của mục ± 1 s |
| Ảnh cảnh | OCR không thấy chữ; không khuôn mặt thật; không gần đen/gần trắng |
| Thumbnail | 1280x720, ≤ 2 MB; chữ ≤ 4 từ, OCR đọc được ở bản thu 120 px |
| Provenance | 100 % asset (kể cả nhạc, footage, phụ đề) có `generated_by` + license hợp lệ |

## 9. Lộ trình đề xuất

| Giai đoạn | Nội dung | Thay đổi kiến trúc |
|---|---|---|
| 0 — **xong** | A1–A5, E1 (+ 8 provider media) | Không đổi schema; `media.yaml` thêm `video.fit/tail_pad_s/font`; kiểm chứng bằng ffmpeg thật |
| 1 — **xong** | B1–B5 | **ADR-0009** Render pipeline v2. Thực tế nhẹ hơn dự kiến: chuyển động suy ra từ thứ tự cảnh nên KHÔNG phải thêm trường vào scene manifest; chỉ `AssetKind += captions`; `media.yaml += video.motion/transition_s/loudness_lufs` |
| 2 (≈ 1 tuần) | C1, C2, B3 | **ADR-0010** Reviewer đa phương thức có ranh giới: `attachments` trong `ModelClient`, front matter `inputs`, QC code trước model, eval lưu hash ảnh; console preview `output/` |
| 3 (theo nhu cầu) | D3–D5, E3 (D1–D2 đã xong ở đợt này) | **ADR-0011** Footage/video-gen + thư viện nhạc có license + giá media |

Thứ tự này ưu tiên đúng: trước hết video thật phải chạy không lỗi và đồng bộ (0), rồi nghe nhìn đạt chuẩn nền tảng (1),
rồi mới có người/agent thật sự **thấy** được sản phẩm trước khi đăng (2), cuối cùng là mở rộng chất liệu (3).
