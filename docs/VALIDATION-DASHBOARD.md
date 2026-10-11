# Xác minh dashboard và quy trình video

Ngày 11/10/2026. Chạy trên Linux, Python 3.13.15, Chromium qua Playwright.
Console dùng database demo riêng; không gọi model/media tính phí và không đăng video thật.

## Hành vi đã kiểm tra

- Lưu ý tưởng trên trình duyệt, tải lại trang vẫn còn; chuyển thành dự án, chống tạo trùng từ cùng ý tưởng, xóa/hoàn tác.
- Nội dung chứa HTML được hiển thị dưới dạng văn bản, không thực thi.
- Tạo video dài 16:9 và video ngắn 9:16 bằng form; audience, hook và thông điệp tới brief sản xuất; metadata dùng nhãn ngôn ngữ English khi được chọn. Regression Python xác minh aspect/voice language thực tế và cả SEO retry.
- Tìm kiếm/lọc, chuyển thẻ/Kanban, mở kịch bản, cảnh, ba review và trạng thái chưa đồng bộ số liệu.
- Điều hướng các tab dự án bằng bàn phím; polling giữ chữ đang nhập khi yêu cầu duyệt còn cùng phiên bản.
- Checklist cũ không gửi lệnh duyệt cho phiên bản mới; “Để sau” giữ yêu cầu duyệt đang chờ.
- Draft cấu hình CPU được giữ sau blur/polling và trong lúc chờ worker áp dụng; kế hoạch thiếu ngân sách trả lý do lỗi và khôi phục nút thử lại cho ý tưởng.
- Chọn hướng dẫn riêng video dài/ngắn trong playbook tám bước.
- Desktop 1440px và sáu màn hình mobile ở 390px không tràn ngang trang; nhãn demo vẫn xuất hiện trên mobile.
- Sau chỉnh căn lề: kiểm tra **49 tổ hợp** bảy trang ở 1920, 1440, 1280, 1024, 768, 390 và 360px; không tràn ngang, chỉ một mục điều hướng active/aria-current và không có lỗi JavaScript. Kiểm tra thêm lưới bốn cột ở 1920px và cập nhật ảnh desktop/mobile.
- Không có lỗi JavaScript trong các lượt kiểm tra trình duyệt.
- Bảng màu sáng dùng thanh bên trắng, điểm nhấn xanh dương và thẻ pastel. Chín cặp màu chính (nút tạo video, điều hướng được chọn, workspace, thống kê, chữ phụ và tiêu đề poster) có tương phản ít nhất 4,5:1; tám ảnh giao diện được cập nhật bằng dữ liệu demo.

Script tái chạy: [dashboard-smoke.cjs](../scripts/dashboard-smoke.cjs). Playwright chỉ là công cụ phát triển, không phải dependency chạy xưởng.

```bash
STUDIO_TEST_URL=http://127.0.0.1:8765 \
STUDIO_TEST_TOKEN="$STUDIO_CONSOLE_TOKEN" \
STUDIO_SCREENSHOTS_DIR=/tmp/xstudio-dashboard-shots \
node scripts/dashboard-smoke.cjs
```

Chỉ chạy script với console `--demo` và database riêng; script tạo dự án và đổi cấu hình demo.

## Kiểm thử Python và kiểm tra mã

Lượt cuối `make verify` trên mã đã ổn định đạt: **778 Studio + 273 Gateway + 478 xagents-core = 1.529 ca**,
không có ca thất bại/skip; coverage **100%** ở mỗi package, gồm dòng và nhánh của xagents-core.
Coverage dùng file riêng để tránh các lượt pytest đồng thời ghi đè dữ liệu; ngưỡng 100% của repository giữ nguyên.
Chi tiết môi trường, sửa backend và giới hạn: [VALIDATION.md](VALIDATION.md).

Ruff/mypy cả ba package đạt; eval replay strict **28/28** đạt; `node --check` cho app và hai script smoke đạt.
Snapshot đã kiểm tra dữ liệu vắng mặt, phiên bản mới nhất, khởi động lại, trường riêng tư và phân biệt đăng video với đăng phản hồi.

## Bằng chứng giao diện

- [Desktop](screenshots/workflow-desktop.png)
- [Mobile](screenshots/workflow-mobile.png)
- [Playbook](screenshots/workflow-playbook.png)
- [Kịch bản](screenshots/workflow-script.png)

Các ảnh là dữ liệu demo. Chưa kiểm chứng chất lượng sáng tạo từ provider thật, upload YouTube thật hoặc adapter TikTok/Reels.
Kho ý tưởng chưa đồng bộ giữa thiết bị; khâu viết kịch bản/dựng không có cửa duyệt riêng mới trong lần thiết kế này.
