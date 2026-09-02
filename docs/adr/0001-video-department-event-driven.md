# ADR-0001: Phòng ban sáng tạo video là hệ đa agent event-driven, tự chứa, kế thừa software-company

Trạng thái: Accepted · Ngày: 2026-09-02

## Bối cảnh
Hub X-Agents có "công ty AI" đầu tiên là `software-company` (bus có schema, blackboard có chủ, human gate, runner trung
lập provider, orchestrator theo bảng route, eval ghi/phát lại). Cần một phòng ban thứ hai vận hành kênh YouTube từ đầu
tới cuối theo mô hình tham chiếu (AgentTube): nghiên cứu → kịch bản → giọng đọc & hình ảnh → ghép video → metadata →
duyệt → lên lịch → đăng → học từ số liệu; self-hosted; approval-first; provider linh hoạt.

## Quyết định
1. **Thư mục riêng `Studio-creators/`, package `studio`, không import từ `software-company`**: mỗi công ty đứng độc lập
   (chạy, test, CI riêng). Các thành phần lõi (events/bus/sqlite_bus/blackboard/registry/gates/gate_cli/llm/runner/evals)
   được sao chép và cắt gọn (bỏ tool-use, đổi khoá thành `video_id`), không dùng chung mã để hai công ty tiến hoá độc lập.
2. **7 khối, 14 agent + human gate** (chiến lược 2, sáng tác 1, sản xuất 3, phân phối 3, chất lượng 3, phân tích 1,
   giám sát 1). Gộp "voice director" và "visual designer" vào production-manager (một manifest mô tả cả narration lẫn
   visual prompt); tách fact-checker, rights-checker, quality-reviewer thành ba cổng độc lập vì mô hình tham chiếu đòi
   ba xác nhận riêng (factual, media-rights, quality) trước khi đăng.
3. **19 topic, 10 namespace** (`topics/README.md`); key = `video_id` (cấp kênh = `channel_id`).
4. **Bus SQLite là checkpoint**: mỗi giai đoạn là event; mở lại là replay và chạy tiếp ("resume interrupted production").
5. **Lớp media trung lập provider** và **renderer bằng code** — chi tiết ở ADR-0003; **scene manifest & sửa cảnh** — ADR-0004;
   **approval-first gates** — ADR-0002; **preflight** — ADR-0005.

## Hệ quả
- Một lệnh `make run` chạy cả phòng ban với model + media đã cấu hình; test/demo chạy offline bằng client giả và media giả.
- Trùng lặp một phần mã lõi với software-company là cố ý; sửa lỗi lõi phải làm ở cả hai nơi (ghi trong CONTRIBUTING).
- Chưa có adapter YouTube thật (upload, analytics, comments): publisher ghi ý định, số liệu do người/adapter nạp qua CLI.

## Phương án bị loại
- Dùng chung package `company` cho hai công ty: ràng buộc schema/route khác nhau, một thay đổi làm gãy công ty kia.
- Để model gọi tool TTS/ảnh trực tiếp (agentic media): chi phí không kiểm soát, provenance khó ghi, khó test offline.
