---
name: youtube-analytics
version: 1
standards: [CTR × AVD framework, Retention curve reading, Impressions funnel, Minimum sample size, Evidence-bound insights]
---
# Skill: youtube-analytics

## Tiêu chuẩn tham chiếu
- Hai đòn bẩy: CTR (impression → click: tiêu đề/thumbnail) và AVD/retention (click → xem: hook/nội dung/nhịp)
- Đường cong giữ chân: 30s đầu (hook), dốc đều (nhịp), rơi đột ngột (cảnh cụ thể), đuôi (CTA/kết)
- Phễu: impressions → CTR → views → AVD → hành động (like/comment/sub)
- Mẫu tối thiểu: < 1000 impressions không kết luận CTR; < 100 views không đọc retention
- Insight = số + vị trí + hành động cho một agent

## Quy trình (làm đúng thứ tự)
Đọc snapshot → kiểm mẫu đủ → đọc `retention_drops` (code đã map cảnh) → đọc CTR so trung bình kênh trong `insights` →
đọc `experiment` nếu có → viết insight có evidence → khuyến nghị theo mức ảnh hưởng → ghi `insights`.

## Quy tắc
- Không tự tính lại điểm rơi/độ tin cậy; dùng số code đưa.
- Mỗi insight gắn một agent nhận hành động.
- CTR cao + AVD thấp = thumbnail/tiêu đề hứa quá → hành động cho thumbnail-designer/seo-optimizer, không phải khen.
- So sánh với video trước cùng pillar, không so với video viral ngoài kênh.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Mẫu đủ hoặc nói "chưa đủ dữ liệu"
- [ ] Mọi insight có số + vị trí + hành động
- [ ] retention_drops/experiment chép nguyên
- [ ] Khuyến nghị 1–5, có thứ tự

## Ví dụ tốt
"Retention rơi 12 điểm tại 6s (S2, 18s một hình) → editor: chia S2, thêm hình so sánh; CTR 6% (kênh 4.5%) với thumb A → giữ giả thuyết lợi ích."

## Ví dụ xấu
"Video hoạt động tốt, tiếp tục phát huy"; kết luận từ 200 impressions.
