# CODEMAP.md — gateway: muốn đổi X thì sửa ở đâu

| Muốn | Sửa | Kiểm |
|---|---|---|
| Thời gian cooldown theo mã lỗi (401→300s, 402/403/429→3600s, khác→60s, `Retry-After` ghi đè) | `src/gateway/auth.py` | test cooldown; bảng `README.md` "Cơ chế xoay vòng" |
| Làm mới access token (sớm 120s), refresh lỗi | `auth.py` | test refresh 4xx vs lỗi mạng |
| Chọn tài khoản kế, ghim theo bearer, danh sách giữ chỗ | `auth.py` | — |
| Dịch OpenAI → Google Code Assist, model anh em cùng tài khoản, endpoint dự phòng | `src/gateway/client.py` | test HTTP giả |
| Stream (xoay trước chunk đầu), `finish_reason`, `usage` | `client.py` | — |
| Thông điệp 429 "thử lại sau khoảng Ns" | `client.py` / `server.py` | `software-company/src/company/routing.py`, `Studio-creators/src/studio/routing.py` còn khớp |
| Endpoint HTTP (`/v1/chat/completions`, `/v1/models`, health), daemon, host/port | `src/gateway/server.py` | mục "Endpoint" `README.md` |
| CLI `start/stop/status/login/logout/reset/setup/models` | `src/gateway/manage.py` (subparsers ở ~dòng 471), `__main__.py` | `make status` exit code |
| `setup` ghi `llm.yaml` cho software-company | `manage.py` | không dùng khi `llm.yaml` đã có `backends:` |
| Model alias → model upstream | `client.py` + bảng model `README.md` | `models --probe` |
| Nơi lưu tài khoản, biến môi trường | mục "Dữ liệu và biến môi trường" `README.md` | — |
