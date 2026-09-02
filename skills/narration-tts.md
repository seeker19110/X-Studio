---
name: narration-tts
version: 1
standards: [Text normalisation for TTS, Prosody by punctuation, Voice consistency, Loudness -14 LUFS]
---
# Skill: narration-tts

## Tiêu chuẩn tham chiếu
- Chuẩn hoá văn bản cho TTS: số, đơn vị, viết tắt, tên nước ngoài viết dạng đọc
- Ngữ điệu qua dấu câu: dấu phẩy = ngắt ngắn, chấm = ngắt dài, không dùng dấu chấm than liên tiếp
- Một giọng cho cả video (voice_id, pace, language cố định trong manifest)
- Âm lượng chuẩn nền tảng: -14 LUFS (renderer/ffmpeg chuẩn hoá; production chỉ đảm bảo văn bản sạch)

## Quy trình (làm đúng thứ tự)
Lấy narration → chuẩn hoá số/đơn vị/viết tắt → ngắt câu ≤ 20 từ → đặt dấu câu cho nhịp → chọn voice theo `voice`
trên blackboard → kiểm không còn markdown/emoji/URL.

## Quy tắc
- Không URL, emoji, markdown, ngoặc vuông trong narration.
- "2026" → "hai nghìn không trăm hai mươi sáu" chỉ khi provider đọc sai; mặc định giữ số Ả Rập và kiểm ở bản nháp.
- Từ nước ngoài quan trọng: thêm phiên âm trong ngoặc chỉ ở bản nháp, không đưa vào bản cuối nếu TTS đọc đúng.
- Pace: medium mặc định; short có thể fast; YMYL slow.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Không ký hiệu không đọc được
- [ ] Câu ≤ 20 từ, dấu câu tạo nhịp
- [ ] voice_id/pace/language nhất quán
- [ ] Số/đơn vị đọc được

## Ví dụ tốt
"Bốn mươi hai phần trăm người mới bỏ cuộc sau ba video. Con số này đến từ một khảo sát hai nghìn bốn trăm người."

## Ví dụ xấu
"42% (n=2,400) bỏ cuộc!!! 👉 xem https://..."
