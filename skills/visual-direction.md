---
name: visual-direction
version: 1
standards: [Prompt anatomy (subject/context/light/composition/style), Visual consistency, Safe-area, No-text-in-image]
---
# Skill: visual-direction

## Tiêu chuẩn tham chiếu
- Cấu trúc prompt ảnh: chủ thể → bối cảnh → ánh sáng → bố cục/góc máy → phong cách → điều cấm (negative)
- Nhất quán thị giác: cùng bảng màu, cùng phong cách vẽ/chụp trong một video (theo `brand`)
- Vùng an toàn: chủ thể trong 80% giữa khung; 9:16 tránh 15% trên/dưới (UI nền tảng che)
- Không chữ trong ảnh sinh (AI viết sai chữ); chữ là overlay ở thumbnail

## Quy trình (làm đúng thứ tự)
Đọc visual_notes của kịch bản → chọn một chủ thể minh hoạ ý → viết prompt theo cấu trúc → thêm phong cách `brand` →
thêm điều cấm (chữ, logo, khuôn mặt thật, watermark) → kiểm tỷ lệ khung.

## Quy tắc
- Một cảnh một chủ thể; cảnh so sánh thì bố cục chia đôi rõ.
- Không tên nghệ sĩ/tác phẩm/nhân vật/thương hiệu/người thật trong prompt (media-rights).
- Prompt ≤ 60 từ, cụ thể hơn là dài hơn.
- Ảnh minh hoạ số liệu: mô tả biểu đồ đơn giản (cột/đường), không số cụ thể trong ảnh (narration nói số).

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Prompt đủ chủ thể/bối cảnh/ánh sáng/bố cục/phong cách
- [ ] Không chữ, logo, người thật, tên riêng
- [ ] Nhất quán phong cách với `brand`
- [ ] Đúng tỷ lệ khung và vùng an toàn

## Ví dụ tốt
"Bàn làm việc gỗ sáng bừa bộn giấy và cốc cà phê, ánh nắng chiều qua cửa sổ, góc máy 45 độ, phong cách minh hoạ phẳng màu ấm, không chữ, không logo."

## Ví dụ xấu
"Ảnh đẹp về YouTube theo phong cách Pixar có chữ 'SUBSCRIBE'."
