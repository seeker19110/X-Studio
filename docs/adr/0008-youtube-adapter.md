# ADR-0008: Adapter nền tảng YouTube thật — upload, thumbnail, lên lịch, bình luận, số liệu (approval-first)

Trạng thái: Accepted · Ngày: 2026-09-02

## Bối cảnh
Tới #76 phòng ban chạy trọn vòng đời video nhưng dừng ở ranh giới nền tảng: publisher chỉ ghi *ý định* vào
`publish-events` (`platform_ref: yt:abc123` do model tự khai), số liệu và bình luận nạp tay qua CLI. README ghi rõ ở
"Chưa có": adapter YouTube (Data API upload/schedule, Analytics API, Comments API). Cần nối thật mà không phá bốn
nguyên tắc đã có: approval-first (ADR-0002), model quyết định – code hành động (ADR-0003), trung lập provider, số liệu
thật không bịa. Chủ dự án không muốn phụ thuộc SDK Google bắt buộc, và test/CI không được chạm mạng.

## Quyết định
1. **Interface `Platform` (`platform.py`)**, trung lập nền tảng, chỉ CODE gọi:
   `upload_video(path, metadata, privacy="private", publish_at=None, made_for_kids=False) -> UploadResult(platform_ref, url, status)`,
   `set_thumbnail(platform_ref, path)`, `schedule(platform_ref, publish_at)` (privacyStatus=private + publishAt),
   `list_comments(platform_ref, since=None) -> list[Comment]`, `reply(comment_id, text) -> ReplyResult`,
   `snapshot(platform_ref, window_days) -> SnapshotResult(snapshot: PerformanceSnapshot, evidence)`.
   Lỗi nền tảng ném `PlatformError` có HTTP status và thông điệp gốc (quota 403, 401 sau refresh, 5xx).
   - `FakePlatform`: lưu vào dict, sinh id giả, ghi `calls` để test/demo; mặc định khi không cấu hình.
   - `YouTubePlatform`: HTTP thuần `urllib` qua `fetcher(method, url, headers, body) -> (status, headers, bytes)` tiêm được.
     Upload **resumable hai bước** (`POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status` → `Location`
     → `PUT` bytes); `thumbnails/set`; `videos.update` part=status cho lịch; `commentThreads.list` (phân trang, lọc `since`);
     `comments.insert` (reply); Analytics `GET https://youtubeanalytics.googleapis.com/v2/reports` với
     `views,estimatedMinutesWatched,averageViewDuration,likes,comments` (filters `video==<id>`), retention qua
     `audienceWatchRatio` theo `elapsedVideoTimeRatio` (đổi sang giây bằng `contentDetails.duration` của `videos.list`).
     Impressions/CTR: Analytics API v2 không công bố metric này cho query thường; adapter thử truy vấn riêng, thất bại thì
     để `0` và **ghi rõ trong `evidence`** — không bịa. Mọi lời gọi ghi `evidence` gồm endpoint, HTTP status, id trả về.
2. **OAuth do người dùng tự chạy** (`python -m studio.youtube login --client-secrets <file>`): installed-app flow, redirect
   loopback `http://127.0.0.1:<port>/`, mở trình duyệt, scopes `youtube.upload`, `youtube.force-ssl`,
   `yt-analytics.readonly`. Token lưu `~/.x-agents/auth/youtube_tokens.json` (`STUDIO_YOUTUBE_TOKENS` để đổi), quyền 0600,
   không bao giờ nằm trong repo. Adapter tự refresh bằng `https://oauth2.googleapis.com/token` khi hết hạn hoặc gặp 401
   (đúng một lần). `status` in tình trạng token (có/hết hạn/scopes) không in secret. Code không bao giờ tạo credential.
3. **Cấu hình**: mục `platform:` trong `media.yaml` (`provider: fake | youtube`) hoặc `STUDIO_PLATFORM=fake|youtube`.
   Mặc định `fake` — test, demo, CI không chạm mạng.
4. **Nối vào orchestrator, giữ nguyên topic và vai trò**:
   - Gate `publish` approve → publisher vẫn trả `publish-events` (status/scheduled_at/evidence là QUYẾT ĐỊNH của model);
     sau đó CODE gọi `upload_video(final_video, metadata)` → `set_thumbnail(thumbnail chosen)` → `schedule(scheduled_at)`
     rồi **ghi đè** `platform_ref`/`url`/`evidence` bằng kết quả thật trước khi publish lên bus (giống renderer điền checksum;
     model không được tự khai id). Upload lỗi → `status: failed` + evidence lỗi; desk xử lý như trước (video ở `approved`,
     người quyết định làm lại/đăng tay). Model trả `failed` → không gọi adapter.
   - Gate `replies` approve → mỗi reply-draft `requires_human=false` → CODE gọi `reply(comment_id, text của draft đã duyệt)`;
     `publish-events kind=reply` với `platform_ref` là id reply thật; lỗi → `failed` cho từng reply.
   - Không có `gate.decide approve` thì không có đường nào tới adapter (test khẳng định `FakePlatform.calls == []`).
   - Audit `platform.upload` / `platform.upload_failed` / `platform.reply` / `platform.reply_failed` với bằng chứng.
5. **Kéo số liệu và bình luận bằng CLI** (`studio.youtube sync-comments <video_id>`, `sync-metrics <video_id> [--window 7]`):
   adapter đọc từ API rồi publish `audience-comments` / `performance-snapshots` dưới actor `adapter:youtube` (số thật, kèm audit
   `platform.snapshot` chứa evidence), orchestrator xử lý như dữ liệu người nạp. `platform_ref` tra từ `publish-events` của
   video (hoặc `--ref`). Chưa có lịch tự động; người/cron gọi.
6. **Prompt agent không đổi**: publisher/community-manager vẫn chỉ quyết định; ràng buộc "platform_ref/url do code điền" nằm
   ở orchestrator, không cần đổi version prompt hay ghi lại eval.

## Hệ quả
- Test offline toàn bộ: FakePlatform, YouTubePlatform với HTTP giả (resumable 2 bước, refresh khi 401, quota 403 →
  PlatformError, analytics thiếu quyền → trường 0 + evidence), orchestrator approval-first.
- Quota YouTube Data API mặc định 10 000 đơn vị/ngày: một upload ≈ 1 600, `commentThreads.list` 1/trang, `comments.insert`
  50; adapter không tự retry khi 403 quota — báo `failed`, người quyết định.
- Impressions/CTR phụ thuộc quyền và metric API; khi không có, analytics-analyst nhận `0` và evidence nói rõ, không suy diễn.
- Chưa có: playlist, Shorts flag, đổi lịch/gỡ video (rollback vẫn do người), adapter nền tảng khác (TikTok…) — interface đã sẵn.
