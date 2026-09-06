# ARCHITECTURE.md — gateway

```
công ty (provider openai, base_url=http://127.0.0.1:1123/v1)
   │  OpenAI Chat Completions (stream hoặc không), bearer bất kỳ
   ▼
server.py ── /v1/chat/completions · /v1/models · health
   │
client.py ── dịch request/response OpenAI ⇄ Google Code Assist; chọn model upstream; endpoint chính/dự phòng;
   │          xoay tài khoản khi 429/402/403/quota; thử model anh em cùng tài khoản trước; stream chỉ xoay trước chunk đầu
   ▼
auth.py ──── pool tài khoản Google (OAuth Antigravity): access token (làm mới sớm 120s), cooldown theo mã lỗi,
              tôn trọng Retry-After, ghim tài khoản theo bearer, bỏ qua token giữ chỗ
   ▼
Google Code Assist (Gemini 3.x / Claude Sonnet qua Antigravity)
```

`manage.py` là CLI (daemon start/stop/status, login/logout/reset, setup, models). Không có retry/backoff mũ trong
gateway — chỉ cooldown + xoay; mọi tài khoản cooldown → 429 kèm số giây, để `routing.py` của công ty nghỉ đúng
lúc rồi sang backend kế. Chi tiết bảng tình huống → hành động: `README.md` "Cơ chế xoay vòng".

Nguồn gốc: mang từ `donghanhcungban/Plugin-For-Hermes` (`bridge/`), bỏ phần gắn với Hermes Agent.
