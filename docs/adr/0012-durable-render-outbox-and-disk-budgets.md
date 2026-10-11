# ADR-0012 — Nhật ký đầu ra render và ngân sách segment

Ngày: 2026-10-11. Bổ sung ADR-0011.

## Lỗi được tái hiện

Checkpoint route model không bao phủ bước code chạy trước route. Render thành công,
thumbnail-designer lỗi tạm thời, retry cùng scene-manifests sẽ phát draft lần hai và
gọi editor lần nữa. Checkpoint bước code giải quyết retry thông thường nhưng không
đủ khi tiến trình chết sau publish draft, trước lúc ghi checkpoint.

## Quyết định

- Checkpoint bước code theo event nguồn, chỉ sau thành công; lỗi vẫn được thử lại.
- Trước khi phát manifest/assets, renderer ghi toàn bộ envelope đã chuẩn bị trong một
  audit event `render.prepared` bền vững. Restart tìm theo event nguồn, dùng lại event ID
  và chỉ phát phần chưa có trong bus. Áp dụng draft, repair, final và thumbnail. Đây là
  nhật ký gửi đầu ra nội bộ, không phải transaction với API bên ngoài.
- Cut-list áp dụng vào manifest_version đã khai báo; không lấy version mới nhất có thể
  vừa được phát một phần trước khi bị gián đoạn. Một worker/lease vẫn là điều kiện vận hành.
- Khôi phục QC từ audit đúng actor và cấu trúc hợp lệ để reviewer vẫn nhận kết quả đo
  khi restart bỏ qua bước render đã checkpoint. Đây là phục hồi ngữ cảnh; auto-publish
  vẫn kiểm tra file hiện tại theo chính sách hiện có.
- `render.segment_cache_max_bytes` mặc định 0 (không tự dọn). Khi cấu hình, dọn blob
  ffmpeg/bounded-scene cũ nhất không thuộc bất kỳ cảnh nào của manifest hiện tại.
  Giới hạn áp dụng từng thư mục `.segments` của video, chỉ tính MKV đã commit, không
  tính metadata hoặc cache khác. Không xóa symlink, file lạ, cache paid media hay upload.
- `render.min_free_disk_bytes` mặc định 256 MiB; kiểm tra trước/sau encode và concat.
  Trước concat tính thêm dung lượng segment + AAC dự kiến và overhead. Thiếu chỗ báo
  MediaError để retry/pause, giữ final cũ. Nếu cảnh đang dùng vượt ngân sách, yêu cầu
  tăng ngân sách/chia manifest; không xóa checkpoint đang dùng để ép vừa ngân sách.

## Giới hạn

Một scene đang encode có thể tạm vượt ngân sách trước lần kiểm tra kế tiếp; cấu hình
này không thay filesystem quota. File mồ côi/metadata hỏng không tự xóa; tổng output,
cache model/TTS/ảnh, nhiều video và nhật ký SQLite vẫn tăng theo công việc.

Crash trước khi nhận và ghi bền kết quả provider vẫn có thể lặp lời gọi; kết quả
được chuẩn bị bền vững mới có thể phát lại mà không gọi provider. Lỗi/quyền truy cập
hoặc mất dữ liệu SQLite không được giải quyết bằng nhật ký này. Upload YouTube không
tham gia transaction nội bộ; mất tiến trình sau server nhận upload nhưng trước khi
ghi platform_ref vẫn cần đối soát thủ công. Không thay gate hay tự gọi upload để thử.

Stress test offline có trần thời gian, 1 thread cấu hình, fixture tổng hợp và dữ liệu
tạm tự dọn. Kết quả tại VALIDATION.md chỉ chứng minh lượt chạy đã đo, không suy ra
chất lượng sáng tạo hoặc khả năng render 12 giờ.
