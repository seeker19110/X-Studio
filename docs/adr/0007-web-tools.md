# ADR-0007: Tool đọc web có ranh giới cho trend-researcher và fact-checker

Trạng thái: Accepted · Ngày: 2026-09-02

## Bối cảnh
ADR-0003 bỏ tool-use: model chỉ ra quyết định có cấu trúc, code hành động. Hệ quả ghi ngay trong ADR đó: model không
đọc được web, nên trend-researcher chỉ có nguồn do người đưa hoặc kiến thức model. Bản ghi eval bằng model thật (#71)
cho thấy đúng điều đó: trend-researcher trả `trends: []`, `sources: []` kèm ghi chú "không có công cụ tìm kiếm, không
bịa URL" (0/2 ca đạt); fact-checker block cả claim có nguồn vì không mở được nguồn (1/2). Prompt bắt "không bịa nguồn"
đang làm đúng việc của nó — thứ thiếu là năng lực đọc, không phải prompt.

Hai agent này cần đọc web; không agent nào cần ghi/đăng (publisher đăng qua adapter nền tảng, chưa có — README "Chưa có").
Software-company đã có tool-use trung lập provider (ADR-0010) và tool web (ADR-0012) với ranh giới tin cậy; ta port
phần gọn nhất, không port workspace/shell.

## Quyết định
1. **Tool là bảng tên cố định, chỉ đọc** (`tools.py`): `ToolSpec`/`ToolCall`/`ToolBox` trung lập provider (port từ
   software-company) và `WebTools` với đúng hai tool:
   - `web_fetch(url)`: urllib, chỉ `http/https`, chặn host phân giải ra IP riêng/loopback/link-local/metadata (kiểm cả
     từng chặng redirect), timeout 20 s, tối đa 2 MB tải về, HTML bóc thẻ thành văn bản, cắt ≤ 20 000 ký tự, trả kèm
     `title` và URL cuối cùng sau redirect. Đầu ra gắn nhãn "DỮ LIỆU KHÔNG TIN CẬY".
   - `web_search(query, max_results ≤ 8)`: gọi endpoint cấu hình `STUDIO_SEARCH_URL` (mặc định hiểu JSON kiểu SearXNG
     `?q=...&format=json` → `{results: [{title, url, content}]}`; URL có `{q}` thì thay vào chỗ đó). Không cấu hình →
     tool trả chuỗi lỗi rõ "chưa cấu hình search" để model chuyển sang nguồn khác hoặc nói rõ không tìm được.
   Không có tool nào ghi file, chạy lệnh hay đăng. `fetcher` tiêm được để test không chạm mạng.
2. **Tool-use trung lập provider** (`llm.py`): `ModelClient.complete` nhận thêm `tools` và `messages` (hội thoại trung
   lập user/assistant/tool) như software-company; `Completion.tool_calls` khác rỗng = model muốn gọi tool. Adapter
   `anthropic` (tool_use/tool_result block), `openai` (function calling; có tool thì không ép `json_object` ở lượt
   giữa) và `fake` (`tool_handler`) tự chuyển đổi.
3. **Provider `claude-code` uỷ quyền vòng tool cho CLI**: khi có `tools`, `ClaudeCodeClient` thay `--tools ""`
   `--max-turns 1` bằng `--tools WebFetch,WebSearch --allowedTools WebFetch,WebSearch --max-turns 8` và KHÔNG chạy
   vòng lặp tool phía ta — CLI tự tìm/đọc web rồi trả kết quả cuối; `WebFetch`/`WebSearch` coi là bản đồ 1-1 của
   `web_fetch`/`web_search` (cùng ranh giới chỉ đọc, do CLI cưỡng chế). Hệ quả: với provider này vết gọi tool không có
   trong `ToolBox.calls` (audit `tools_used` ghi `delegated: claude-code`), và ranh giới IP riêng là của CLI, không
   phải của `WebTools`. Token trả về là tổng mọi lượt của CLI.
4. **Runner bật tool theo front matter**: `AgentSpec.tools: list[str]` (mặc định `[]`), giá trị hợp lệ hiện có `web`.
   `AgentRunner` nhận `toolbox_factory(spec) -> ToolBox | None` (mặc định `default_toolbox`: `web` → `WebTools`);
   orchestrator và evals dùng mặc định, test truyền toolbox giả. `generate()` chạy `_tool_loop` (tối đa `max_turns`
   = 10, tổng token ≤ `budget_tokens_per_task` của agent; vượt → audit `budget_exhausted`, RunnerError). Hết lượt →
   ép một lượt chốt không tool. Kết thúc vòng ghi audit `tools_used` {turns, calls, urls}.
5. **Eval ghi/phát lại bỏ qua các lượt tool**: `RecordingClient` chỉ lưu completion cuối (không `tool_calls`) dưới khoá
   `hash(system, user)` với `user` là message lượt đầu (không kèm tool_result); `ReplayClient` tra cùng khoá và trả
   **câu trả lời cuối** ngay lượt đầu, không gọi tool, không gọi mạng — CI `--replay` vì thế offline như trước. Đổi
   prompt/skill → khoá đổi → phải ghi lại bằng model thật (cơ chế ADR-0010 giữ nguyên).
6. **Prompt**: trend-researcher và fact-checker thêm quy tắc dùng tool (tìm rồi mở nguồn; chỉ trích URL đã mở; không có
   kết quả thì nói rõ trong `assumptions`/`root_cause`, không bịa); tăng `version`, golden và bản ghi eval ghi lại.
   Skill không đổi.

## Hệ quả
- Mạng ra ngoài chỉ mở cho hai agent có `tools: [web]`; agent khác không đổi hành vi, prompt không đổi.
- `STUDIO_SEARCH_URL` là quyết định của người vận hành (SearXNG tự host hoặc endpoint tương thích); không có thì
  provider `claude-code` vẫn tìm được qua `WebSearch` của CLI, provider khác chỉ còn `web_fetch` trên URL đã biết.
- Nội dung web đi qua bộ lọc injection của runner như mọi đầu vào khác; nhãn "DỮ LIỆU" nằm ngay đầu mỗi kết quả tool.
- Chưa có: allowlist domain theo kênh, cache trang đã đọc giữa các lượt, và tool web cho agent khác (script-writer,
  community-manager cố ý không có — họ làm việc trên dossier đã kiểm).
