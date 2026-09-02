---
name: fact-checking
version: 1
standards: [IFCN Code of Principles, Claim-by-claim verification, YMYL strictness, Correction transparency]
---
# Skill: fact-checking

## Tiêu chuẩn tham chiếu
- IFCN: minh bạch nguồn, minh bạch phương pháp, sửa sai công khai
- Kiểm từng claim: supported / unsupported / misleading / no-source, mỗi kết luận có nguồn đã đọc
- YMYL (sức khoẻ, tài chính, pháp lý, an toàn): chuẩn cao hơn — cần primary hoặc cơ quan có thẩm quyền
- Số liệu: đúng số, đúng đơn vị, đúng phạm vi (năm, quốc gia, mẫu), đúng ngữ cảnh

## Quy trình (làm đúng thứ tự)
Liệt kê claim (kể cả câu chưa khai claim) → mở nguồn → đối chiếu số/thực thể/phạm vi → kết luận → xếp mức
(block/warn/nit) → viết root_cause một câu → metrics.

## Quy tắc
- Claim số liệu không nguồn đọc được → block. Claim diễn đạt quá phạm vi nguồn ("mọi người" khi nguồn nói "42%") → warn kèm câu sửa.
- Claim về người thật/tổ chức có thể gây hại danh dự → block nếu không có nguồn primary.
- Nguồn là video/bài của đối thủ → không đủ cho claim số; chỉ đủ cho "đã có người nói".
- Không kiểm giọng văn, không kiểm SEO; chỉ đúng/sai/nguồn.
- Kết luận phải tái lập được: nêu nguồn + vị trí (trang, mốc thời gian).

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Mọi claim có kết luận và finding có claim_id
- [ ] Block đúng cho claim số/YMYL/người thật không nguồn
- [ ] root_cause một câu, hành động được
- [ ] metrics đủ: checked / supported / unsupported / no_source
- [ ] Câu có số chưa khai claim được nêu

## Ví dụ tốt
C1 "42% người mới bỏ sau 3 video" ↔ report trang 14: "41.8% (n=2,400, 2025)" → supported (làm tròn hợp lý), nit: ghi năm.

## Ví dụ xấu
Pass vì "số này nghe hợp lý"; block cả kịch bản vì một lỗi chính tả.
