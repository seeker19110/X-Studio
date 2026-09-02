# ADR-0002: Approval-first — ba review độc lập và bốn human gate

Trạng thái: Accepted · Ngày: 2026-09-02

## Bối cảnh
Đăng video, trả lời bình luận, và cam kết kế hoạch là hành động công khai, khó đảo ngược, có rủi ro pháp lý (bản quyền,
thông tin sai) và thương hiệu. Mô hình tham chiếu quy định: mọi video phải qua factual review, media-rights confirmation
và approval trước khi publish; mọi reply chờ duyệt.

## Quyết định
1. **Ba review bắt buộc, ba agent khác nhau** (`REQUIRED_REVIEWS = {fact, rights, quality}`): fact-checker chạy trên kịch
   bản (chặn sản xuất nếu block); rights-checker và quality-reviewer chạy song song trên gói hoàn chỉnh (final video +
   manifest + metadata + thumbnail). Không agent nào review việc của mình.
2. **Gate `publish`** chỉ được xin khi desk thấy đủ: final_video + metadata + thumbnail + 3 review pass; checklist gate gồm
   verdict từng review và mọi finding preflight còn lại. Publisher chỉ chạy từ quyết định gate (`approved_by` trong đầu vào);
   không có phê duyệt → `failed`.
3. **Gate `plan`**: kế hoạch biên tập (nhiều brief) do code kiểm (estimate, budget ×1.5, id trùng, priority) rồi mới xin gate;
   approve → desk dispatch.
4. **Gate `replies`**: community-manager gom lô nháp → gate; approve → publisher đăng các reply không `requires_human`.
5. **Gate `escalation`**: retry > 3 hoặc supervisor escalate → ESC-<video>; approve = mở lại với hint, reject = đóng.
6. **Rework có hint**: review fail/block hoặc gate request_changes → desk phát lại `video-briefs` với `retry+1` và `hint`
   (`[script]`/`[production]`/`[gate]`), script-writer làm lại trên `previous_script`; mọi artifact và review cũ vô hiệu.
7. Four-eyes: người quyết ≠ người tạo gate; timeout 24h, nhắc 12h; quá hạn không tự đi tiếp.

## Hệ quả
- Không có đường nào từ bus tới "đăng" mà không qua `gate.decide` trong audit-log — kiểm được bằng test.
- Ba review làm tăng chi phí token mỗi video (~3 lượt strong); chấp nhận vì rủi ro đăng sai đắt hơn.
- Người duyệt cần thấy đủ: checklist gate là bản tóm tắt; artifact đầy đủ nằm ở `output/<video>/` và blackboard.
