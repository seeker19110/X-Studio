# ADR-0003: Trung lập provider cho cả text lẫn media; model quyết định, code hành động

Trạng thái: Accepted · Ngày: 2026-09-02

## Bối cảnh
Kênh video cần ba loại năng lực ngoài: model text (kịch bản, review), TTS, sinh ảnh, và ghép video. Chủ dự án yêu cầu
framework chạy được mọi model/provider (Gemini miễn phí, OpenAI, OpenRouter, Kimi, GLM, model local), không khoá một nhà
cung cấp. Đồng thời chi phí media phải kiểm soát được và provenance phải ghi được.

## Quyết định
1. **Text**: `ModelClient.complete(system, user, schema, model_tier)` như software-company (ADR-0005 bên đó), adapter
   `anthropic` / `openai` (mọi server OpenAI-compatible) / `fake`; cấu hình `llm.yaml` + `STUDIO_*`. **Bỏ tool-use**: agent
   video không cần chạm hệ thống; mọi hành động có tác dụng phụ là code.
2. **Media**: ba interface nhỏ `TTS`, `ImageGen`, `VideoAssembler` (`media.py`), mỗi kênh chọn provider độc lập trong
   `media.yaml` + `STUDIO_MEDIA_*`: `fake` (offline, sinh WAV im lặng/PNG đơn sắc/MP4 giả hợp lệ), `openai`
   (`/audio/speech`, `/images/generations` — dùng được với bất kỳ server tương thích), `ffmpeg` (ghép ảnh + audio thành MP4).
   Thêm provider (ElevenLabs, Stability, Runway...) = thêm một class, không chạm renderer/agent.
3. **Renderer là code**: nhận scene manifest, gọi media, ghi asset với checksum + provenance (`provider:model`, `prompt_ref`,
   license), publish `media-assets` dưới actor `renderer`. Model không bao giờ gọi media.
4. **Token là số thật** từ `usage`; media đếm theo asset trong audit (`render.*`).

## Hệ quả
- Đổi model/TTS/ảnh là việc cấu hình; eval và test chạy offline không đổi.
- Chưa đo chi phí media bằng tiền (chỉ đếm asset); giá provider để FinOps ngoài code.
- Model không sửa được file/đọc web: nghiên cứu dựa trên nguồn do người/adapter đưa vào dossier hoặc kiến thức model — cần
  tool đọc web có ranh giới ở bước sau (ghi ở README "Chưa có").
