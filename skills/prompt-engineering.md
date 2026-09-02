---
name: prompt-engineering
version: 1
standards: [Prompt-as-code, Eval-driven change, Structured output, Context hygiene, One change at a time]
---
# Skill: prompt-engineering

## Tiêu chuẩn tham chiếu
- Prompt là code: version, review, golden test, rollback bằng revert
- Eval-driven: mỗi thay đổi có bộ ca chạy trước/sau (`evals/<agent>.yaml`, ghi/phát lại)
- Đầu ra có cấu trúc theo JSON Schema của topic
- Vệ sinh ngữ cảnh: đưa đúng artifact cần (enrich), không nhồi
- Sửa một thứ mỗi lần và đo

## Quy trình (làm đúng thứ tự)
Xác định tiêu chí đo → dựng ca vàng → viết prompt tối thiểu (vai trò, PHẢI, KHÔNG ĐƯỢC, đầu vào, đầu ra, DoD) →
đo → sửa một thứ → siết schema → thêm ca đối kháng (injection, thiếu nguồn) → tăng version, `make golden`, `make eval-record`.

## Quy tắc
- Quy tắc kiểm chứng được thay tính từ ("≤ 15 từ" thay "ngắn gọn").
- Ví dụ để trong skill; prompt agent không nhồi ví dụ dài.
- Prompt không chứa secret/PII; ví dụ dùng dữ liệu giả.
- Đổi skill dùng chung = đổi prompt mọi agent dùng nó; kiểm ảnh hưởng, tăng version các agent bị chạm.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] version tăng khi prompt/skill đổi; golden cập nhật
- [ ] PR có kết quả eval trước/sau
- [ ] Prompt đủ 6 mục
- [ ] Quy tắc đo được
- [ ] Đầu ra tuân schema

## Ví dụ tốt
script-writer v1 → v2: thêm "hook ≤ 15 từ"; eval 6 ca: hook đạt 6/6 (trước 3/6); golden + recording cập nhật cùng PR.

## Ví dụ xấu
Sửa 5 quy tắc một lượt, không eval, không tăng version.
