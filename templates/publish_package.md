# Gói đăng <video_id> — gate publish (PUB-<video_id>)
- final_video: output/<video_id>/final_v<n>.mp4 (checksum …) · thumbnail chosen: <variant> · metadata: v…
- review: fact = pass|block (findings…) · rights = pass|block · quality = pass|block
- preflight: 0 block · warn còn lại + lý do giữ
- provenance: mọi asset generated|licensed|owned (rights-checker ghi `rights`)
- lịch đăng dự kiến: <ISO 8601> theo `strategy`
- người tạo: desk · người duyệt: <human:…> (≠ người tạo)
## Quyết định
approve | request_changes(lý do → hint) | hold | reject
