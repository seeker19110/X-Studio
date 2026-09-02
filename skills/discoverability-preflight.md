---
name: discoverability-preflight
version: 1
standards: [Versioned content audit, GEO/AIO/AEO checks, Advisory findings with dismissal reason, Platform limits]
---
# Skill: discoverability-preflight

## Tiêu chuẩn tham chiếu
- Kiểm tra có phiên bản: bộ quy tắc preflight có version; kết quả ghi audit để so giữa các lần
- GEO (generative engine), AIO (AI overview), AEO (answer engine): mô tả có đoạn trả lời thẳng, tiêu đề là câu hỏi/lợi ích, chapter là mục lục
- Finding là tư vấn: người duyệt giữ hoặc bỏ với lý do; chỉ giới hạn nền tảng/từ cấm là block
- Giới hạn nền tảng là sự thật, không tranh luận

## Quy trình (làm đúng thứ tự)
CODE chạy `preflight.py` trên metadata → finding block → seo-optimizer sửa một lần → finding còn lại vào checklist gate publish →
người duyệt giữ/bỏ với lý do → publisher không đăng khi còn block.

## Quy tắc
- Sửa finding block trước, giữ nguyên phần đã đạt; không đổi video_id.
- Finding warn có thể giữ nếu có lý do (vd. tiêu đề 75 ký tự vì tên sản phẩm dài) — ghi lý do trong gate.
- Không "lách" quy tắc bằng cách bỏ chapter/tag; thiếu cũng là finding.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] 0 finding block khi xin gate
- [ ] Mọi warn còn lại có lý do giữ
- [ ] Mô tả có đoạn trả lời thẳng câu hỏi chính
- [ ] Kết quả preflight ghi audit

## Ví dụ tốt
Preflight: block "tiêu đề 120 ký tự" → sửa còn 64; warn "mô tả 180 ký tự" → thêm đoạn trả lời; gate thấy 0 block, 1 warn đã giải thích.

## Ví dụ xấu
Bỏ hết chapter để hết finding chapter; đăng khi còn block "cụm bị cấm".
