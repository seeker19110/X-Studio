---
name: video-editing
version: 1
standards: [Edit decision list, Scene repair, Lock-when-good, Bounded iteration, Pacing rules]
---
# Skill: video-editing

## Tiêu chuẩn tham chiếu
- EDL (edit decision list): quyết định dựng là dữ liệu (thứ tự, cắt, thay), không phải thao tác tay
- Sửa cảnh: chỉ sinh lại phần sai; cảnh đạt thì khoá
- Lặp có giới hạn: 3 vòng; hết vòng thì chốt và ghi phần chưa ưng cho reviewer
- Nhịp: ≤ 20s một hình; chuyển cảnh theo ngắt câu của narration; tổng khớp target

## Quy trình (làm đúng thứ tự)
Xem bản nháp + asset từng cảnh → kiểm từng cảnh: ảnh khớp prompt? narration đọc đúng? thời lượng? →
liệt kê sửa có lý do → khoá cảnh đạt → quyết định thứ tự → approve hoặc repair.

## Quy tắc
- Mỗi repair: scene_id có thật, action đúng loại lỗi (ảnh sai → regenerate_image; đọc vấp → regenerate_audio), reason cụ thể.
- Sinh lại ảnh phải kèm `new_visual_prompt` khác prompt cũ ở điểm gây lỗi; sinh lại cùng prompt là lãng phí.
- Không sửa > 6 cảnh/vòng; nhiều hơn là lỗi manifest → notes cho production-manager.
- `order` khi đổi phải đủ mọi scene_id.
- approve chỉ khi mọi cảnh đủ audio + image.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Mọi repair có scene_id, action, reason, prompt/narration mới khi sinh lại
- [ ] Cảnh đạt được lock
- [ ] ≤ 6 cảnh/vòng, ≤ 3 vòng
- [ ] order đầy đủ khi đổi
- [ ] approve có đủ asset và thời lượng khớp

## Ví dụ tốt
repair S2 regenerate_image "ảnh tối, không thấy sơ đồ" → prompt mới "sơ đồ pipeline nền trắng, nét đậm"; lock S1, S3.

## Ví dụ xấu
repair 9 cảnh "cho đẹp hơn" không prompt mới; vòng 4 vẫn repair.
