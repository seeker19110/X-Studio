# ARCHITECTURE.md — gateway

```
công ty (provider openai, base_url=http://127.0.0.1:1123/v1)
   │  OpenAI Chat Completions (stream hoặc không), bearer bất kỳ (chuỗi giữ chỗ bị bỏ qua — server.py:39)
   ▼
server.py ── aiohttp: /v1/chat/completions · /v1/models · /auth/status · /auth/login · /health (server.py:138-142)
   │          mã lỗi + thông điệp luôn có nội dung; 429 kèm Retry-After (server.py:52-101)
   ▼
client.py ── dịch OpenAI ⇄ Google Code Assist; catalog model tự dò (TTL 1 giờ); một lượt = duyệt danh sách ứng viên
   │          một lần: model anh em cùng tài khoản → endpoint dự phòng → tài khoản kế; stream chỉ xoay trước chunk đầu
   │          (client.py:880-1053)
   ▼
auth.py ──── pool tài khoản Google (OAuth PKCE Antigravity) trong MỘT file JSON: thứ tự = bearer khớp rồi LRU;
   │          refresh sớm 120 s; cooldown theo mã lỗi, Retry-After / "Resets in" ghi đè; ghi nguyên tử 0600
   │          (auth.py:305-384, 220-235)
   ▼
Google Code Assist: daily-cloudcode-pa (chính) / cloudcode-pa (dự phòng) — Gemini 3.x, Claude Sonnet qua Antigravity
```

`manage.py` là CLI: daemon `start/stop/status` (tiến trình con tách session, PID file, healthcheck 10 s),
`login/logout/reset`, `setup` (ghi `llm.yaml` một provider), `models` (catalog + đối chiếu `llm.yaml`, `--probe`).

## Bốn quyết định giữ hình dạng này

| Quyết định | Ở đâu |
|---|---|
| Không retry/backoff trong gateway; cả pool nghỉ → 429 kèm số giây, để `routing.py` của công ty nghỉ đúng lúc rồi sang backend kế | ADR-0001 |
| Trạng thái xoay vòng (LRU, cooldown) sống trong **file token**, không trong RAM — sống qua restart, chia sẻ được giữa daemon và CLI | ADR-0001 §1, §3 |
| Cổng 1123 loopback, không xác thực client, không lock file: healthcheck `/health` trả lời "có gateway đang phục vụ không" | ADR-0002 |
| Token ngoài repo, 0600, không bao giờ ra log/HTTP; email là bề mặt lộ duy nhất và được giữ để kiểm chứng xoay vòng | ADR-0003 |

## Ranh giới với phần còn lại của hub

- **Vào**: chỉ HTTP OpenAI-compatible. Công ty không import `gateway`; `manage.py setup/models` là chỗ duy nhất
  gateway đọc `llm.yaml` của công ty (`manage.py:50-57`), và chỉ đọc.
- **Ra**: Google OAuth (`accounts.google.com`, `oauth2.googleapis.com`) và Code Assist (`auth.py:57-60`,
  `client.py:30-31`). Không có đích nào khác.
- **Đĩa**: `$XAGENTS_HOME` (`~/.x-agents`): `auth/antigravity_tokens.json`, `gateway/gateway.pid`, `logs/gateway.log`
  (`auth.py:81-96`, `server.py:42-49`). Không ghi vào cây repo trừ `setup` ghi `llm.yaml` theo yêu cầu.
- **Test**: không mạng; `httpx.MockTransport` + `aiohttp TestClient` + monkeypatch `urllib`; 216 ca (213 chạy, 3 skip
  quyền file POSIX trên Windows).

Nguồn gốc: mang từ `donghanhcungban/Plugin-For-Hermes` (`bridge/`), bỏ phần gắn với Hermes Agent. Lịch sử thay
đổi: `../CHANGELOG.md` (scope `gateway`).
