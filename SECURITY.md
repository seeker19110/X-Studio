# Chính sách bảo mật

## Báo lỗ hổng

Đừng mở issue công khai. Dùng **Security → Report a vulnerability** (GitHub private vulnerability reporting) của repo
này, mô tả cách tái hiện, phiên bản/commit và tác động. Chủ repo phản hồi trong khả năng của một dự án cá nhân; không
có chương trình thưởng.

## Phạm vi đáng báo

- Đi vòng **approval-first** (ADR-0002/0010): bất cứ đường nào đăng, lên lịch hay trả lời công khai trên nền tảng mà
  không qua gate `publish`/`replies` hoặc policy đã ký.
- Prompt injection từ dữ liệu ngoài (bình luận, trang web, kịch bản) khiến agent ghi topic ngoài quyền, hoặc khiến code
  chạy thứ model viết ra (ADR-0003, ADR-0017: graphic là dữ liệu template, không phải code).
- Rò bí mật: `client_secret.json`, token OAuth YouTube, khoá API, khoá ký policy (`STUDIO_POLICY_KEY_FILE`).
- Ghi/đọc file ngoài thư mục cho phép (upload thay thế, thư viện media, đường dẫn trong manifest).

## Ngoài phạm vi

Lỗ hổng của provider bên thứ ba (model, TTS, ảnh, YouTube) — báo cho họ; cấu hình sai trên máy của chính bạn.
