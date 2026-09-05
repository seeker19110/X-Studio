# ADR-0009: Render pipeline v2 — chuyển động, chuyển cảnh, âm lượng chuẩn, phụ đề, chapter thật, QC bằng code

Trạng thái: Accepted · Ngày: 2026-09-05

## Bối cảnh

Sau khi sửa nhóm lỗi P0 (khung hình, thời lượng, thumbnail — xem `docs/DANH-GIA-NANG-CAP-XUONG-VIDEO.md`), xưởng ra được
video **đúng**, nhưng chưa ra được video **xem được**:

- Bản dựng là slideshow ảnh tĩnh cắt cứng. Skill `retention-storytelling` đòi ngắt mẫu mỗi 15–20 giây, mà lớp render chỉ
  có đúng một kiểu ngắt là đổi ảnh.
- Âm lượng để nguyên theo provider TTS. Nền tảng phát lại chuẩn hoá về khoảng −14 LUFS, nên video quá nhỏ bị đẩy lên
  kèm nhiễu, quá to bị nén; `docs/standards.md` ghi −14 LUFS từ đầu nhưng không có code nào thực hiện.
- Không có phụ đề, dù `narration` trong scene manifest **chính là** văn bản đã đọc.
- Mốc chapter do seo-optimizer viết trước khi có video, tức là số đoán; preflight chỉ kiểm định dạng chứ không kiểm nó có
  khớp video không.
- Ba reviewer chỉ đọc JSON. Không ai — người hay máy — đo file thật trước gate publish.

## Quyết định

1. **Một lệnh ffmpeg cho cả bản dựng** (`media.FFmpegAssembler.assemble`), thay cho ghép từng cảnh rồi `concat`:
   mỗi cảnh là ảnh có chuyển động nhẹ + giọng đọc; các cảnh nối bằng `xfade`/`acrossfade`; âm lượng đi qua `loudnorm`.
2. **Chuyển động** (`media.motion_for`): xoay vòng `zoom_in → pan_right → zoom_out → pan_left` nên hai cảnh liền nhau
   không bao giờ cùng kiểu. Pan làm bằng cửa sổ `crop` chạy trên ảnh đã phóng to (rẻ, chính xác), zoom bằng `zoompan`.
   Kiểu chuyển động là **việc của code**, suy ra từ thứ tự cảnh: không thêm trường vào scene manifest, không đổi prompt
   của agent nào.
3. **Chuyển cảnh ăn vào đoạn đệm im lặng**: `transition_s` bị kẹp ≤ `tail_pad_s`. Nhờ vậy phần chồng lấn luôn nằm trong
   khoảng lặng cuối cảnh — không câu nào chồng lên câu nào — và vì video lẫn tiếng cùng ngắn đi đúng `transition_s` mỗi
   mối nối nên hình và tiếng không bao giờ lệch nhau.
4. **Âm lượng −14 LUFS, đỉnh −1 dBTP** (`loudnorm`), tắt được bằng `video.loudness_lufs: null`.
5. **Phụ đề sinh từ manifest** (`timeline.srt`): không cần nhận dạng giọng nói, vì narration là văn bản gốc và mốc thời
   gian tính bằng đúng công thức mà assembler ghép. Publish thành `media-assets` kind `captions`; sau khi upload video,
   code gọi `Platform.upload_captions` (best-effort: lỗi phụ đề ghi vào evidence, **không** làm hỏng lượt đăng).
6. **Chapter: model đặt tên, code đặt giờ** (`timeline.snap_chapters`). Khi có bản cuối, code nắn từng mốc về đầu cảnh
   gần nhất, bỏ mốc trùng cảnh, mốc cách nhau < 10 giây và mốc nằm ngoài video, rồi publish lại `metadata-packages`
   dưới actor `chapters`. Preflight chạy lại trên bản đã nắn.
7. **QC bằng code trên file thật** (`qc.py`): ffprobe + `ebur128`/`blackdetect`/`silencedetect` đo khung hình, fps,
   thời lượng, âm lượng, đỉnh, đoạn hình đen, khoảng lặng, thumbnail và phụ đề. Báo cáo đi vào `package` của
   quality-reviewer **và** vào checklist gate publish. Không có ffprobe thì báo cáo nói thẳng là không đo được.

8. **QC từng cảnh cho editor** (`qc.qc_scenes`): editor là người duy nhất sửa được cảnh, và prompt của nó yêu cầu bắt
   "ảnh tối", "narration vấp" — nhưng nó chỉ nhận JSON đường dẫn. Code đo từng ảnh (độ sáng, tương phản qua
   `signalstats`) và từng file giọng đọc (thời lượng, tổng im lặng, so với số từ của narration), rồi gắn vào payload
   của editor dưới khoá `scene_qc`. Không đổi prompt: đây là dữ liệu, không phải chỉ thị. Máy không có ffmpeg thì
   trường này rỗng và editor làm việc như trước.

## Hệ quả

- Video ra có nhịp: mỗi cảnh có chuyển động riêng, chuyển cảnh mềm, tiếng đều ở mức nền tảng.
- `AssetKind` thêm `captions` (schema `media-assets` cập nhật theo). Không topic nào khác đổi, không agent nào đổi prompt,
  nên không phải ghi lại eval.
- Chi phí dựng tăng: một lượt encode toàn bộ thay vì copy khi nối, cộng `zoompan`. Đổi lại chỉ còn một lệnh ffmpeg cho
  mỗi bản dựng. Đo trên máy phát triển: bốn cảnh 1080p mất khoảng 10 giây.
- Mốc thời gian giờ có một nguồn sự thật duy nhất (`timeline.timeline`) dùng chung cho phụ đề, chapter và điểm rơi
  retention. Đổi `tail_pad_s`/`transition_s` mà quên sửa công thức này thì phụ đề sẽ lệch — đó là chỗ dễ hỏng nhất.
- Editor lần đầu có số để quyết định: một cảnh ảnh gần đen hay TTS trả về im lặng nay hiện thành finding có mức và
  vị trí, thay vì phải đoán từ đường dẫn file. Chi phí: hai lượt ffmpeg cho mỗi cảnh ở bước dựng nháp.
- QC chỉ **báo cáo**, không tự chặn: quyền quyết định vẫn ở quality-reviewer và người duyệt gate (ADR-0002,
  approval-first). Đổi lại, mọi finding block đều hiện nguyên văn trong checklist gate.

## Chưa làm

- Nhạc nền có license và ducking dưới giọng đọc (cần thư viện nhạc + sổ provenance riêng).
- Phụ đề theo từng cụm từ (cần timestamps của provider TTS), phụ đề đốt sẵn cho Shorts.
- Reviewer **nhìn** được ảnh cảnh (đầu vào đa phương thức). `scene_qc` bù được phần đo bằng máy (tối, một màu, im lặng)
  nhưng không thay được mắt người: "ảnh có khớp prompt không", "có chữ hay khuôn mặt thật trong ảnh không" vẫn phải
  nhìn. Việc đó chạm front matter của agent nên phải ghi lại eval bằng model thật, để sang ADR-0010.
