# gateway — luật riêng (bổ sung `../AGENTS.md`, không thay)

Package `gateway`. Proxy OpenAI-compatible tại `127.0.0.1:1123/v1`, dịch sang Google Code Assist (Antigravity OAuth),
xoay vòng nhiều tài khoản Google. Hai công ty trỏ `base_url` vào đây và **không biết gì về Google** — giữ nguyên
điều đó. Vì sao mọi thứ như hiện nay: `docs/adr/0001` (xoay vòng), `0002` (cổng + daemon), `0003` (bảo mật).

## Chạy ở đâu

```bash
cd gateway
uv run pytest -q --cov && uv run ruff check src tests && uv run mypy src/gateway --ignore-missing-imports
uv run python -m gateway login | start | stop | status | models | setup | reset | logout
```

`status` exit 1 khi server tắt **hoặc** 0 tài khoản sẵn sàng (`manage.py:196`) — dùng được trong script, nhưng mã
thoát không phân biệt hai ca.

## Ba điều không được phá

1. **Hợp đồng OpenAI Chat Completions ở mặt ngoài**: `usage` trả token thật từ `usageMetadata` (kể cả cache) —
   software-company ghi audit-log từ đây; `finish_reason` thật (`tool_calls`, `length`) không bị đè thành `stop`
   (`client.py:1030-1032`).
2. **Chế độ hỏng phải tự khai báo** (`../TRAPS.md` khuôn 1): timeout → 504 có thông điệp (`server.py:57-58,70-72`),
   hết quota, refresh lỗi — mỗi cái một thông điệp riêng, mã riêng. Mọi tài khoản cooldown → **429 kèm "Thử lại sau
   khoảng Ns"** (`auth.py:358-362`) và header `Retry-After` (`server.py:95-99`); router của công ty khớp cả hai
   (`software-company/src/company/routing.py:38-39`) — đổi chuỗi là phá router.
3. **Không retry/backoff mũ trong gateway**: chỉ cooldown + xoay tài khoản (ADR-0001 §4). Retry là việc của lớp trên.

## Sửa cái gì phải làm gì

| Sửa | Phải |
|---|---|
| bảng cooldown / mã lỗi → hành vi | `auth.py:77-78` + bảng "Cơ chế xoay vòng" `README.md` + ADR-0001 §3 — ba chỗ phải khớp |
| model alias, model upstream, model anh em | `client.py` (`MODEL_ALIAS_MAP`, `IN_ACCOUNT_MODEL_FALLBACK`) + bảng model `README.md`; `models --probe` gọi thử thật |
| thông điệp 429 "thử lại sau" / dòng log `lần thử i/n` | kiểm `software-company/src/company/routing.py`, `Studio-creators/src/studio/routing.py`, và test `tests/test_failover.py:355-397` |
| endpoint mới, trường mới trong `/auth/status` | `server.py` + mục "Endpoint" `README.md` + bảng lộ dữ liệu ADR-0003 §2 + test HTTP giả |
| cổng, host, vòng đời daemon | `server.py:37-38`, `manage.py:91-161` + ADR-0002 |
| chỗ lưu token, quyền file | `auth.py:81-96,220-235` + ADR-0003 §1 + `../SECURITY.md` "Mô hình bí mật" |

## Không bao giờ

Log access token / refresh token (bảng ADR-0003 §2 là bề mặt được phép); commit dữ liệu tài khoản kiểu
`~/.x-agents`; gọi Google thật trong test (mọi test đi `httpx.MockTransport` / `aiohttp TestClient`); bind ngoài
loopback trong tài liệu hướng dẫn mà không nhắc SSH tunnel.
