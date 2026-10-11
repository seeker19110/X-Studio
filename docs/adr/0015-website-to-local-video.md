# ADR-0015 — Nhập website và xuất video cục bộ

Ngày: 2026-10-11. Trạng thái: accepted.

## Quyết định

Console có luồng **Website → Video** riêng: URL công khai, giới thiệu/hướng dẫn/cả hai, 20–300 giây cho từng video, Full HD hoặc 4K, giọng đọc tiếng Việt và thư mục tuyệt đối trên ổ C. Lệnh đi qua auth/Origin/CSRF hiện có; bảng `website_jobs` dùng chung SQLite và lease của factory. Một thread website xử lý lần lượt; không chia sẻ mutable orchestrator/model client với worker chính. Snapshot đọc tiến độ đã lưu từ DB, vì một bước media có thể chạy lâu.

Model viết kịch bản từ bằng chứng bằng backend subscription CLI `codex`/`claude-code` đã cấu hình. Code kiểm schema, ID trang và nhịp cảnh; model không cấp selector, JavaScript, shell hoặc danh sách hành động trình duyệt. Kịch bản chỉ dẫn điều hướng và mô tả nội dung đã quan sát, không giả thực hiện đăng nhập, thanh toán hoặc gửi bài. Nội dung web được đánh dấu dữ liệu không tin cậy. Backend API không được tự chọn cho luồng này.

Chromium dùng phiên khách mới, chặn service worker/WebSocket/download, DNS trực tiếp và UDP WebRTC. Host website được ghim IP công khai trong DNS của Chromium; host khác đi qua transport ghim IP và kiểm từng redirect (ADR-0007). Host chưa ánh xạ không phân giải trực tiếp trong browser. Chặn mọi phương thức ghi. Phiên mới có thể nhận cookie khách từ website, không nhập cookie hoặc tài khoản của người dùng. Giữ một allowlist header công khai để ứng dụng như Next.js nhận đúng RSC; không chuyển Cookie/Authorization. Tải song song tối đa 6, giới hạn 1024 request, tài nguyên khai báo Content-Length/transport và thời gian, chỉ khám phá link cùng host không có query hoặc dấu hiệu hành động ghi. Chụp tối đa 8 trang ở 3200×2000; đợi nội dung `main` tải xong, bỏ trang trùng, bổ sung viewport cuộn cho site một trang.

Giọng đọc Edge TTS trả audio và WordBoundary thật. Cache audio theo giọng/nội dung; dịch vụ giọng đọc lỗi thử lại tối đa 3 lần với backoff; tốc độ tối đa 1.25, giữ lead-in/tail và chia cảnh ≤20 giây. Nếu lời đọc không vừa, model nhận thời lượng đo được và viết lại tối đa 2 lần; không cắt câu để đạt số giây. Phụ đề chia tối đa 2 dòng/42 ký tự, SRT tải riêng và ASS burn-in có PlayRes đúng kích thước, vô hiệu hóa markup do nội dung đưa vào. Layout được vẽ bằng Pillow ở kích thước đích, dùng ảnh website thật và font Unicode cục bộ.

FFmpeg dựng từng cảnh với giới hạn luồng, cache theo kịch bản/ảnh/giọng/thời lượng, ghép và cân bằng loudness; H.264 yuv420p 30 fps, BT.709, AAC stereo 48 kHz, faststart. Bản MP4 staging chỉ thay bản cuối sau kiểm codec/kích thước/fps/thời lượng và giải mã toàn bộ. Nguồn, kịch bản, SRT, poster và báo cáo SHA-256 nằm trong thư mục riêng của job trên ổ C. Media HTTP chỉ mở định danh artifact đã đăng ký và kiểm path containment; không nhận đường dẫn từ URL.

## Khôi phục và giới hạn

Tác vụ đang chạy khi app dừng chuyển lại `queued` ở lần khởi động sau. Dừng do người dùng giữ `cancelled`; tiếp tục giữ tư liệu/cache. Cache ảnh có phiên bản capture, cache kịch bản có fingerprint bằng chứng/yêu cầu để tránh dùng kịch bản cũ sau khi chụp lại. Nút dừng có hiệu lực ở checkpoint sau bước hiện tại; gọi CLI/FFmpeg có timeout. Tạm dừng xưởng ngăn bắt đầu job tiếp theo.

Đây là xuất video cục bộ; không tạo lịch đăng, upload hoặc vượt human gate. Không dùng tài khoản trình duyệt của người dùng, không vượt CAPTCHA. Website đòi POST kể cả đọc dữ liệu có thể không chụp được; điều này phải báo thật thay vì tạo screenshot minh họa giả. CLI, Edge TTS và website cần Internet; Chromium, FFmpeg/ffprobe và font phải có ở máy chạy console.

## Tham chiếu triển khai

- [Playwright network](https://playwright.dev/python/docs/network), [routing](https://playwright.dev/python/docs/api/class-route): chặn service worker và can thiệp request.
- [Edge TTS source](https://github.com/rany2/edge-tts/blob/master/src/edge_tts/communicate.py): audio và WordBoundary.
- [FFmpeg filters](https://www.ffmpeg.org/ffmpeg-filters.html): phụ đề ASS, fade, atempo và loudnorm.
