# Đánh giá và lộ trình nâng cấp xưởng sản xuất video (Studio-creators)

Ngày: 2026-09-04 · Phạm vi: lớp sản xuất thật (scene manifest → TTS → ảnh → ghép → sửa cảnh → review → thumbnail → đăng).
Mục tiêu: ra được **video thật** (chạy với provider thật không lỗi) và **chất lượng xuất sắc** (đo được, không cảm tính).

## 1. Kết luận

Phần điều phối của xưởng đã ở mức tốt: event-driven có schema, scene manifest bền vững sửa từng cảnh, provenance mọi asset,
ba review độc lập, human gate approval-first, resume từ SQLite, eval ghi/phát lại. Điểm nghẽn để ra video thật chất lượng
cao nằm ở **hai lớp cuối**:

1. **Lớp render** (`media.py`, `renderer.py`) hiện là *slideshow ảnh tĩnh + giọng đọc*: mỗi cảnh một ảnh đứng im, cắt cứng,
   không nhạc, không phụ đề, không chuẩn hoá âm lượng, thời lượng cảnh là số ước lượng chứ không đo từ file. Kèm năm lỗi
   khiến lượt chạy provider thật đầu tiên hỏng hoặc sai khung (mục 3).
2. **Lớp "mắt và tai"**: editor, quality-reviewer, rights-checker chỉ nhận JSON đường dẫn file. Không agent nào thấy ảnh
   hay nghe audio, cũng không có bước code nào đo file thật. Vì vậy các quy tắc "ảnh khớp prompt", "narration đọc đúng",
   "hook ≤ 5 giây", "không chữ/khuôn mặt thật trong ảnh" hiện **không kiểm được** ở bất kỳ tầng nào trước gate publish.

Khuyến nghị: sửa lỗi P0 ngay (một hai ngày, không đổi schema), rồi làm ba ADR theo thứ tự: render pipeline v2 + QC bằng
code → reviewer đa phương thức + preview cho người duyệt → mở rộng provider media và thư viện nhạc/footage có license.

## 2. Hiện trạng lớp sản xuất (những gì đã có)

| Thành phần | Có | Thiếu |
|---|---|---|
| TTS | `openai`, `gemini`, `elevenlabs`, `azure`, `google`, `command`, `fake` *(5 provider sau thêm ở đợt này)* | provider có timestamps từng từ (phụ đề karaoke); cache audio theo hash narration |
| Ảnh | `openai`, `gemini` (Gemini Image / Imagen), `stability`, `replicate`, `fake` *(3 provider sau thêm ở đợt này)* | neo phong cách (seed/reference); kích thước hợp lệ theo model ở code mặc định |
| Ghép video | ffmpeg: ảnh tĩnh + audio từng cảnh → concat | chuyển động, chuyển cảnh, nhạc, phụ đề, loudness, khung 9:16 |
| Sửa cảnh | `apply_cutlist`: sinh lại đúng cảnh, khoá, thay asset, đổi thứ tự, ≤ 3 vòng | kiểm asset còn tồn tại; cache TTS theo nội dung |
| Thumbnail | 2–3 biến thể, prompt + overlay | chữ do model ảnh vẽ; không resize 1280x720; không kiểm ≤ 2 MB |
| Review | 3 agent độc lập, checklist đo được trên giấy | không thấy ảnh/audio; metrics do model đọc manifest, không đo file |
| QC file | — | ffprobe, LUFS, frame đen, im lặng, đồng bộ, kích thước |
| Đăng | upload private, thumbnail, publishAt, comments, analytics | `captions.insert`, kiểm file trước upload |
| Duyệt | gate publish có checklist, đường dẫn file | xem bản nháp/final trong console |

## 3. Lỗi phải sửa ngay (P0) — chặn "video thật" chạy đúng

**A1. Kích thước ảnh mặc định không hợp lệ với `gpt-image-1`.** `media.py:62` mặc định `1792x1024`, `renderer.py:80`
fallback `1024x1792`/`1792x1024`, `renderer.py:163` thumbnail `1792x1024`, `media.example.yaml:14`. Tài liệu OpenAI:
`gpt-image-1` chỉ nhận `1024x1024`, `1536x1024`, `1024x1536` (1792x1024 là của DALL-E 3). Lượt render thật đầu tiên với
cấu hình mẫu sẽ nhận HTTP 400. Sửa: bảng kích thước theo model (`gpt-image-1` → 1536x1024 / 1024x1536; `dall-e-3` →
1792x1024 / 1024x1792), hoặc gửi `auto`; ảnh ra luôn qua bước code scale/crop về đúng khung 16:9 hoặc 9:16.

**A2. Thời lượng cảnh là ước lượng, không đo từ file audio.** `media.py:209` trả `estimate_duration(text)` (số từ / 2.5)
thay cho độ dài mp3 thật; `media.py:264-266` ghép bằng `-t {dur}` cộng `-shortest` → nếu giọng đọc dài hơn ước lượng thì
**bị cắt giữa câu**, ngắn hơn thì ảnh đứng im lặng. Tiếng Việt đọc theo âm tiết nên sai số lớn. Hệ quả dây chuyền: chapter,
retention map theo cảnh (`analytics.retention_drops` dùng `duration_s` tích luỹ), metrics của quality-reviewer đều lệch.
Sửa: đo thời lượng thật sau TTS (ffprobe, hoặc đọc header wav/mp3 bằng thư viện chuẩn), bỏ `-t`, đệm 0,3–0,5 s im lặng
cuối cảnh, ghi `duration_s` thật vào manifest (renderer đã publish lại manifest sau render, hạ tầng có sẵn).
*Đã làm một phần ở đợt này:* `media.audio_duration` đo từ file (WAV đọc header; định dạng khác hỏi `ffprobe` khi có trên
PATH), mọi provider TTS dùng nó; `gemini` trả PCM nên đo chính xác tuyệt đối. Còn lại: bỏ `-t` và đệm im lặng ở assembler.

**A3. Shorts 9:16 render sai khung.** `renderer.py:104-106`, `144-146`, `153-155` lấy `resolution` từ cấu hình (mặc định
1920x1080) bất kể `manifest.aspect`; ảnh dọc bị pad thành khung ngang với hai dải đen lớn. Sửa: khi `aspect == "9:16"`
dùng 1080x1920 (hoán đổi w/h của cấu hình); thêm test ffmpeg cho short.

**A4. Thumbnail nhờ model ảnh vẽ chữ.** `renderer.py:163` nối `Overlay text: …` vào prompt, trái với skill `visual-direction`
và `thumbnail-design` ("không chữ trong ảnh sinh, chữ là overlay"); chữ tiếng Việt có dấu do model ảnh vẽ thường sai.
Kèm: không resize về 1280x720, không kiểm ≤ 2 MB; `platform.py:346-351` gửi nguyên file. Sửa: sinh ảnh nền **không chữ**
→ code phủ chữ (Pillow, font có dấu, viền/đổ bóng, tránh góc phải dưới nơi YouTube đè thời lượng) → xuất 1280x720
JPG/PNG ≤ 2 MB → kiểm kích thước và dung lượng trước `set_thumbnail`.

**A5. `voice.pace` và `language` bị bỏ qua.** `media.py:206` chỉ gửi `model`, `voice`, `input`. Skill `narration-tts` quy định
pace medium/fast/slow và YMYL đọc chậm nhưng không có tác dụng. Sửa: map pace → `speed` (≈ 0,9 / 1,0 / 1,15), truyền
`instructions` (giọng, ngữ điệu, ngôn ngữ) cho model TTS hỗ trợ; cảnh không đổi narration thì dùng lại audio cũ theo
hash(narration + voice) để không tốn tiền khi chỉ sinh lại ảnh.
*Đã làm ở đợt này:* `pace` map sang `speed` (openai), `speakingRate` (google), `<prosody rate>` (azure), `voice_settings.speed`
(elevenlabs), câu dẫn phong cách (gemini); `tts.instructions` cho openai; bảng `tts.voices` dịch `voice_id` của manifest sang
giọng provider (id giọng khác nhau giữa các nhà). Còn lại: cache audio theo hash.

**E1 (đi kèm).** `media.py:263-271` để lại `_seg000.mp4`, `_concat.txt` trong `output/`; dùng thư mục tạm hoặc dọn sau concat.

## 4. Thiếu năng lực cốt lõi cho "chất lượng xuất sắc" (P1)

**B1. Video là slideshow ảnh tĩnh.** `-loop 1 -tune stillimage`, cắt cứng giữa cảnh. Không chuyển động (Ken Burns), không
chuyển cảnh, không nhạc nền, không intro/outro, không lower-third. Skill `retention-storytelling` đòi "ngắt mẫu mỗi 15–20 s"
nhưng render chỉ tạo được một kiểu ngắt là đổi ảnh. Đề xuất: `Scene` thêm `motion` (zoom_in | zoom_out | pan_left |
pan_right | static; mặc định xoay vòng để không hai cảnh liền nhau cùng kiểu), `transition` (cut | fade | dissolve, 0,3–0,5 s);
render `zoompan` theo fps, ghép bằng `filter_complex` + `xfade` thay `concat -c copy`; nhạc nền từ thư viện có license
(mục D4) với ducking dưới giọng (≈ −18 dB), fade in/out, ghi provenance kind `music` để rights-checker kiểm.

**B2. Không chuẩn hoá âm lượng.** Skill nêu −14 LUFS, README ghi "chưa có", `media.py` không có `loudnorm`. Đề xuất: ở bước
final chạy `loudnorm` hai lượt (I = −14, TP = −1, LRA = 11), AAC 192 kb/s 48 kHz, `afade` 20 ms đầu/cuối mỗi cảnh chống click,
đệm im lặng giữa cảnh.

**B3. Không có phụ đề.** Không sinh SRT/VTT, adapter YouTube không có `captions.insert`. Phụ đề vừa là accessibility, vừa
là SEO, vừa quyết định xem-không-tiếng trên Shorts. Manifest đã có narration + thời lượng từng cảnh nên sinh SRT theo cảnh
không cần nhận dạng giọng; mức cao hơn dùng timestamps của TTS (ElevenLabs trả về) hoặc whisper local để burn-in kiểu
từng cụm từ cho Shorts. Thêm `AssetKind` `captions`, đưa vào `package`, upload sau gate.

**B4. Chapter là số đoán.** `orchestrator.py:207` cho seo-optimizer chạy ngay khi fact pass, **trước** render; mốc chapter
không thể khớp thời gian thật; `preflight.py` chỉ kiểm định dạng (00:00, ≥ 3, ≥ 10 s). Đề xuất: code tính chapter từ
manifest cuối (mục kịch bản → cảnh đầu của mục → thời điểm tích luỹ) và ghi đè `chapters[].time` ở bước finalize;
seo-optimizer chỉ đặt nhãn. Đây đúng tinh thần "model quyết định, code hành động".

**B5. Không có QC bằng code trên file thật.** Không ffprobe, không kiểm final có audio, duration khớp Σ duration_s,
resolution/fps đúng, frame đen, im lặng dài, dung lượng, thumbnail hợp lệ. `metrics` của quality-reviewer do model đọc
manifest. Đề xuất module `qc.py` (code) chạy sau finalize → báo cáo (duration thật, LUFS, true peak, % frame đen, khoảng
im lặng > 1,5 s, resolution, bitrate, kích thước và dung lượng thumbnail, độ phủ phụ đề) → nhét vào `package` cho
quality-reviewer và vào checklist gate; block cứng khi thiếu audio hoặc độ dài lệch > 10 %.

## 5. Reviewer "mù và điếc" (P1, ảnh hưởng chất lượng lớn nhất)

**C1. Editor, quality-reviewer, rights-checker chỉ nhận JSON.** `orchestrator.py:152-166` truyền `manifest` + `scene_assets`
(path, checksum, provenance); `runner.py:89-111` đóng gói mọi thứ thành văn bản; `ModelClient.complete` (`llm.py:100-102`)
không có đầu vào ảnh/audio. Editor phải "kiểm ảnh khớp prompt, narration đọc đúng" mà không thấy ảnh, không nghe audio.
Bản ghi eval thật (`evals/recordings/editor.json`) cho thấy editor tự ghi chú không kiểm được và đẩy việc cho
quality-reviewer, agent cũng mù như nó. Rights-checker không thấy ảnh nên không bắt được logo, chữ, khuôn mặt thật vô tình
sinh ra. Đề xuất hai lớp, giữ nguyên tắc code hành động:

- *Lớp code (rẻ, xác định, chạy trước):* ảnh thu nhỏ mỗi cảnh (contact sheet); đo độ sáng trung bình và tương phản
  (bắt ảnh gần đen/gần trắng); OCR nhẹ phát hiện chữ trong ảnh; phát hiện khuôn mặt (OpenCV Haar); nhận dạng lại audio
  (whisper local) so với narration, WER > 10 % → đề nghị `regenerate_audio`. Kết quả gắn vào `scene_assets[].qc` để editor
  quyết định trên dữ liệu thật.
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
| 0 (1–2 ngày) | A1–A5, E1 | Không đổi schema (field mới đều optional); cập nhật README, `media.example.yaml`, test |
| 1 (≈ 1 tuần) | B1, B2, B4, B5 | **ADR-0009** Render pipeline v2: `Scene += motion, transition, visual_kind`; `AssetKind += music, captions`; topic hoặc payload `qc_report`; `media.yaml += audio.loudness, music, captions`; chapter do code tính |
| 2 (≈ 1 tuần) | C1, C2, B3 | **ADR-0010** Reviewer đa phương thức có ranh giới: `attachments` trong `ModelClient`, front matter `inputs`, QC code trước model, eval lưu hash ảnh; console preview `output/` |
| 3 (theo nhu cầu) | D3–D5, E3 (D1–D2 đã xong ở đợt này) | **ADR-0011** Footage/video-gen + thư viện nhạc có license + giá media |

Thứ tự này ưu tiên đúng: trước hết video thật phải chạy không lỗi và đồng bộ (0), rồi nghe nhìn đạt chuẩn nền tảng (1),
rồi mới có người/agent thật sự **thấy** được sản phẩm trước khi đăng (2), cuối cùng là mở rộng chất liệu (3).
