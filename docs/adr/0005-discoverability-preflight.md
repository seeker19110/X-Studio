# ADR-0005: Discoverability preflight bằng code, finding tư vấn, block chỉ cho giới hạn nền tảng

Trạng thái: Accepted · Ngày: 2026-09-02

## Bối cảnh
Metadata sai giới hạn nền tảng (tiêu đề quá dài, tag quá 500 ký tự, chapter sai) làm video không hiện đúng; metadata
"tối ưu quá tay" (viết hoa, nhồi từ khoá, lời hứa tuyệt đối) làm giảm CTR thật và vi phạm chính sách. Mô hình tham chiếu
chạy "versioned GEO/AIO/AEO checks" với finding advisory mà người duyệt giữ hoặc bỏ có lý do.

## Quyết định
1. `preflight.py` là bộ kiểm xác định trên `metadata-packages`: giới hạn nền tảng (title ≤ 100, description ≤ 5000,
   tags ≤ 500 ký tự, chapter 00:00/≥3/≥10s/tăng dần) và chất lượng (từ khoá chính trong tiêu đề và 200 ký tự đầu, viết hoa
   ≤ 50%, mô tả ≥ 200, không nhồi tag > 3 lần, không cụm bị cấm YMYL).
2. Mức `block` chỉ cho giới hạn nền tảng và cụm bị cấm; mọi thứ khác là `warn` (tư vấn).
3. Block → orchestrator gọi seo-optimizer **đúng một lần** với `preflight_findings`; còn block sau đó vẫn đưa lên gate để
   người quyết (publisher từ chối đăng khi còn block trong package).
4. Mọi lần chạy ghi audit `preflight` (blocked, findings) — có phiên bản theo commit của `preflight.py`.
5. Finding còn lại đi vào checklist gate `publish` dưới dạng `preflight:<level>:<location>:<text>`.

## Hệ quả
- Không cần model để bắt lỗi giới hạn; rẻ, tái lập được, test được.
- Quy tắc chất lượng là ý kiến biên tập → để ở mức warn để không chặn nhầm (vd. tên sản phẩm dài).
- Danh sách cụm bị cấm còn ngắn; mở rộng theo chính sách kênh (đưa vào cấu hình ở bước sau).
