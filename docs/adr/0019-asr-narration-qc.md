# ADR-0019 — QC có tai: đối chiếu lời đọc bằng whisper trong FFmpeg

Ngày: 2026-10-14. Trạng thái: accepted. Bổ sung ADR-0016 (QC trên file thật) và ADR-0003 (code hành động).

## Bối cảnh

QC của X-Studio đo khung hình, âm lượng, hình đen, đứng yên, khoảng lặng (ADR-0016) nhưng **không nghe được**:
TTS đọc sai số, bỏ một câu, nhạc át lời, hay cảnh ghép nhầm audio vẫn qua QC vì ba reviewer chỉ đọc JSON. Nghiên cứu
2026-10-14 (§3.9) ghi FFmpeg 8.x có filter `whisper` (whisper.cpp) chạy ngay trong `ffmpeg`, xuất SRT theo đoạn,
không cần Python package hay dịch vụ ngoài — vừa khít quy ước "QC là code đo file, không gọi model qua API".

## Quyết định

1. **Bật bằng cấu hình, im lặng khi thiếu.** `STUDIO_QC_ASR_MODEL` trỏ vào file ggml của whisper.cpp;
   `STUDIO_QC_ASR_LANGUAGE` (mặc định `auto`). `qc.asr_model()` chỉ trả model khi file có và `ffmpeg -filters`
   liệt kê `whisper`. Máy ffmpeg 6/7 hay CI không có model: không metric, không finding — thiếu tai không phải lỗi
   của video.
2. **Một lượt ffmpeg, SRT ra stdout** (`destination=pipe:1`, `format=srt`, `queue=10`), `read_only=True` như mọi lượt
   đo; không ghi file tạm. `parse_srt` chịu được cả `,` lẫn `.` ở mili-giây và khối thiếu mốc.
3. **WER theo cảnh, không theo toàn video.** `narration_wer` gán mỗi đoạn ASR cho cảnh chứa điểm giữa của nó (mốc
   từ `renderer.cues`, cùng công thức với assembler) rồi tính Levenshtein trên từ đã bỏ dấu câu/hoa thường. Cảnh có
   WER > 0,35 → finding `warn` tại `narration`; không `block` vì ASR tiếng Việt vẫn sai với tên riêng và số —
   người duyệt nghe lại cảnh được nêu tên. Metric `asr` (model, ngôn ngữ, WER trung bình, WER từng cảnh, số đoạn)
   vào audit-log và dòng `qc:` của checklist (`asr_wer=`).
4. **Orchestrator truyền `narration`** cho `qc_video` ngay trong `_after_final`: không cần đọc lại manifest ở QC.

## Hệ quả

- Người vận hành tải một model ggml (base/small đủ cho QC) và dùng bản ffmpeg ≥ 8 build `--enable-whisper`; mọi thứ
  khác giữ nguyên. Chi phí: một lượt ffmpeg nữa trên bản cuối, CPU.
- Chưa làm (§3.5 nghiên cứu): timestamp từng từ từ TTS (ElevenLabs `/with-timestamps`, edge-tts `WordBoundary`) cho
  phụ đề karaoke — việc của renderer/caption, không phải QC; và WER không phân biệt lỗi do TTS với lỗi do ASR.

## Phương án bị loại

- **Gọi API STT (Gemini, OpenAI)**: QC sẽ phụ thuộc mạng và tốn tiền cho mỗi bản dựng; trái ADR-0016.
- **`openai-whisper`/`faster-whisper` trong `.venv`**: kéo PyTorch/CTranslate2 vào workspace chung ba package.
- **Chặn (`block`) khi WER cao**: ASR tiếng Việt chưa đủ tin để giữ video lại một mình; để `warn` cho người duyệt.
