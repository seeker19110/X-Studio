# gateway — luật riêng (bổ sung `../AGENTS.md`, không thay)

Package `gateway`. Proxy OpenAI-compatible tại `127.0.0.1:1123/v1`, dịch sang Google Code Assist (Antigravity OAuth),
xoay vòng nhiều tài khoản Google. Hai công ty trỏ `base_url` vào đây và **không biết gì về Google** — giữ nguyên
điều đó.

## Chạy ở đâu

```bash
cd gateway
uv run pytest -q --cov && uv run ruff check src tests && uv run mypy src/gateway --ignore-missing-imports
uv run python -m gateway login | start | stop | status | models | setup | reset | logout
```

`status` exit 1 khi server tắt hoặc 0 tài khoản sẵn sàng — dùng được trong script.

## Ba điều không được phá

1. **Hợp đồng OpenAI Chat Completions ở mặt ngoài**: `usage` trả token thật từ `usageMetadata` (kể cả cache) —
   software-company ghi audit-log từ đây; `finish_reason` thật (`tool_calls`, `length`) không bị đè thành `stop`.
2. **Chế độ hỏng phải tự khai báo** (`../TRAPS.md` khuôn 1): timeout, hết quota, refresh lỗi — mỗi cái một thông
   điệp riêng, mã riêng. Mọi tài khoản cooldown → **429 kèm "thử lại sau khoảng Ns"**; router của công ty khớp
   chuỗi này để nghỉ đúng số giây — đổi chuỗi là phá router.
3. **Không retry/backoff mũ trong gateway**: chỉ cooldown + xoay tài khoản. Retry là việc của lớp trên.

## Sửa cái gì phải làm gì

| Sửa | Phải |
|---|---|
| bảng cooldown / mã lỗi → hành vi | `src/gateway/auth.py` + bảng "Cơ chế xoay vòng" trong `README.md` — hai chỗ phải khớp |
| model alias, model upstream | `client.py` + bảng model `README.md`; `models --probe` gọi thử thật |
| thông điệp 429 "thử lại sau" | kiểm `software-company/src/company/routing.py` và `Studio-creators/src/studio/routing.py` còn khớp |
| endpoint mới | `server.py` + mục "Endpoint" `README.md` + test HTTP giả |

## Không bao giờ

Log access token / refresh token; commit `~/.gateway`-kiểu dữ liệu tài khoản; gọi Google thật trong test.
