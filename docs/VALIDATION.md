# Xác minh bản nâng cấp factory

Môi trường: Linux, Python 3.12.14, uv 0.12.23, FFmpeg 6.1.1. Ngày: 2026-10-11. Không gọi model/TTS/ảnh tính phí và không đăng lên YouTube thật.

| Phạm vi | Kết quả |
|---|---|
| Studio `uv run pytest -q --cov` | 656 ca đạt, 100% dòng (4.547 dòng thực thi) |
| Gateway `uv run --no-sync pytest -q --cov` | 251 ca đạt, 100% dòng |
| xagents-core `uv run pytest -q --cov` | 477 ca đạt, 100% dòng và nhánh |
| `make lint-all` | Ruff và mypy đạt cho cả ba package |
| `make eval-replay` | 28/28 ca ghi sẵn đạt, 14 vai; không phải eval mới với model live |
| HTTP thực | Login/cookie/bearer, Host/Origin, CSRF, JSON validation, byte-range, chặn file ngoài output và tải file không an toàn dạng attachment |
| FFmpeg thực | Segment cache, frame timing, phụ đề Việt, title/quote/bars, footage lặp, nhạc ducking kết thúc đúng độ dài |
| Chromium desktop 1440×1000 + mobile 390×844 | Tạo ba dự án demo, chờ review, đổi cấu hình, mở phòng dựng, giữ gate; 0 lỗi JavaScript, không tràn ngang |

Tổng cộng **1.384 ca Python** ở ba package. Coverage là bằng chứng đường code được chạy, không chứng minh sản phẩm sáng tạo hoàn hảo hoặc nội dung model luôn đúng.

Gateway cần cài thêm `socksio` trong môi trường kiểm tra này vì môi trường có SOCKS proxy; không thay đổi adapter hoặc tắt proxy để vượt kiểm soát mạng. Có một RuntimeWarning có sẵn ở test `runpy` của gateway. Hai fixture HTTP của studio đã được sửa `close()` để tránh warning dọn file của mock.

Ảnh chụp giao diện nằm trong [screenshots](screenshots/), hiển thị **MÔ PHỎNG OFFLINE** và trạng thái thật của demo. Script tái chạy: [scripts/console-smoke.cjs](../scripts/console-smoke.cjs); cần Playwright trong môi trường phát triển, không phải dependency runtime của ứng dụng.

Ví dụ chạy smoke với một console `--demo` đã mở ở cổng riêng:

```bash
STUDIO_TEST_URL=http://127.0.0.1:8765 \
STUDIO_TEST_TOKEN="$STUDIO_CONSOLE_TOKEN" \
STUDIO_SCREENSHOTS_DIR=/tmp/xstudio-shots \
node scripts/console-smoke.cjs
```

Script từ chối nếu server không ở chế độ demo. Có thể đặt `CHROMIUM_EXECUTABLE_PATH` khi dùng Chromium đã cài sẵn. Không dùng database sản xuất cho lượt smoke này.

Chưa xác minh: video 12 giờ, chất lượng giọng/ảnh live, chi phí tiền thực, quyền/quota OAuth của tài khoản, upload và lịch phát YouTube thật, render đa máy, Windows end-to-end. Không dùng kết quả demo để suy ra các phạm vi đó đã hoạt động.
