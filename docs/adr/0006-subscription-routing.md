# ADR-0006: Điều phối model theo gói tài khoản subscription, tier `light`

Trạng thái: Accepted · Ngày: 2026-09-02

## Bối cảnh
ADR-0003 làm phòng ban trung lập provider nhưng vẫn một provider tại một thời điểm. Chủ dự án vận hành bằng các gói
đăng ký đang có (Claude Pro/Max qua CLI `claude -p`, Google Antigravity qua `../gateway`, model local), mỗi gói hết hạn
mức vào lúc khác nhau. Cần chạy nhiều gói cùng lúc, tự chuyển khi một gói hết hạn mức, và chia việc theo mức độ để gói
mạnh dành cho sáng tác và ba cổng review. Cùng thiết kế với software-company ADR-0019.

## Quyết định
1. **Backend = gói tài khoản** trong `llm.yaml` (`backends:`), `STUDIO_LLM_BACKENDS` lọc/sắp lại; không có thì một
   backend như cũ.
2. **`RoutingClient` (`routing.py`)**: chọn theo `routing.prefer[tier]` rồi thứ tự khai báo; hết quota → nghỉ
   `cooldown_s` hoặc đúng Retry-After; lỗi mạng/5xx/timeout → nghỉ `transient_cooldown_s`; lỗi nội dung và từ chối →
   ném ra, không xoay. Mọi backend nghỉ → `LLMError` "thử lại sau Ns" (phòng ban không có lớp retry riêng; orchestrator
   ghi audit, supervisor thấy). Ghi chú xoay qua `drain_retries()`.
3. **Tier thứ ba `light`**; `model_for` lùi light → standard → strong. Bảng agent → tier: `../../docs/DIEU-PHOI-MODEL.md`.
   Xếp lại: publisher, community-manager, thumbnail-designer, supervisor → `light` (việc cơ học, phía sau có gate
   người hoặc số liệu thật); còn lại giữ.

## Hệ quả
- Chạy bằng gói đăng ký, không cần key; hết hạn mức một gói không dừng phòng ban.
- Trạng thái nghỉ nằm trong tiến trình; khởi động lại thử lại từ đầu.
- Media (TTS, ảnh) không đi qua routing này — vẫn `media.yaml` (ADR-0003).
