# CODEMAP.md — gateway: muốn đổi X thì sửa ở đâu

Bốn file nguồn (`src/gateway/`): `auth.py` (pool + OAuth, 587 dòng), `client.py` (dịch + xoay, 1055 dòng),
`server.py` (aiohttp, 285 dòng), `manage.py` (CLI, 521 dòng). Số dòng dẫn dưới đây đúng tại commit viết; lệch vài
dòng thì grep tên hàm.

| Muốn | Sửa | Kiểm |
|---|---|---|
| Thời gian cooldown theo mã lỗi (401→300s, 402/403/429→3600s, khác→60s) | `COOLDOWN_DEFAULTS`, `COOLDOWN_FALLBACK` (`auth.py:77-78`); ghi đè bởi `Retry-After`/"Resets in" ở `mark_account_unavailable` (`auth.py:370-384`) | `tests/test_account_pool.py:42`; bảng `README.md` "Cơ chế xoay vòng"; ADR-0001 §3 |
| Đọc gợi ý hồi hạn mức từ upstream (header hoặc thân "Resets in 7m29s") | `reset_hint_seconds`, `cooldown_hint` (`client.py:104-131`) | `tests/test_failover.py` (429 + `Retry-After`) |
| Làm mới access token (sớm 120s), refresh lỗi | `REFRESH_SKEW_SECONDS` (`auth.py:73`), `refresh_access_token` (`auth.py:392-420`), nhánh 4xx vs mạng (`auth.py:335-352`) | `tests/test_login_pkce.py:55-114`, `tests/test_account_pool.py:101` |
| Thứ tự tài khoản trong một lượt: ghim theo bearer, LRU, bỏ cooldown | `resolve_credential_candidates` (`auth.py:305-368`) | `tests/test_account_pool.py:154-188`; ADR-0001 §1 |
| Danh sách bearer giữ chỗ (không ghim) | `_DUMMY_BEARERS` (`server.py:39`) | `tests/test_server.py:83` |
| Thông điệp 429 khi cả pool nghỉ + header `Retry-After` | `auth.py:358-362`; `_error_response` (`server.py:90-101`) | `software-company/src/company/routing.py:38-39`, `Studio-creators/src/studio/routing.py` còn khớp; `tests/test_server.py:177` |
| Dịch OpenAI → Code Assist (messages, tools, response_format, ảnh) | `build_code_assist_request` và các `_translate_*` (`client.py:319-690`) | `tests/test_translation.py` |
| Dịch Code Assist → OpenAI (`finish_reason`, `usage`, tool_calls) | `_map_finish_reason`, `_usage_from_gemini`, `_parts_to_openai` (`client.py:693-830`) | `tests/test_translation.py`, `tests/test_client_coverage.py` |
| Vòng xoay không stream: model anh em, endpoint dự phòng, 4xx không xoay | `create_chat_completion` (`client.py:880-951`); `IN_ACCOUNT_MODEL_FALLBACK` (`client.py:87`); `_should_fail_over` (`client.py:92-96`) | `tests/test_failover.py`; ADR-0001 §2 |
| Vòng xoay stream (chỉ trước chunk đầu), chunk đóng, `include_usage` | `stream_chat_completion` (`client.py:953-1053`) | `tests/test_failover.py:385`, `tests/test_client_coverage.py:466-537` |
| Dòng log "lượt nào đi tài khoản nào" | `client.py:900,911,937,989` | `tests/test_failover.py:355-397` |
| Endpoint upstream chính / dự phòng, phiên bản client khai với Code Assist | `CODE_ASSIST_BASE_URL`, `FALLBACK_CODE_ASSIST_BASE_URL` (`client.py:30-31`); `ANTIGRAVITY_CLIENT_VERSION` (`client.py:36,299-308`) | `README.md` "Gateway tự cập nhật danh sách model" |
| Catalog model: dò upstream, TTL, bảng tĩnh, alias, strict | `MODEL_CATALOG_TTL_S` (`client.py:144`), `FALLBACK_MODELS`/`MODEL_ALIAS_MAP`/`map_model_name` (`client.py:~40-235`); `_refresh_catalog` (`server.py:179-198`) | `python -m gateway models [--probe]`; `tests/test_manage.py:91-160` |
| Trần thời gian chờ upstream | `UPSTREAM_TIMEOUT_S` (`client.py:862`, env `GATEWAY_UPSTREAM_TIMEOUT_S`); thông điệp 504 (`server.py:57-58,65-73`) | `tests/test_server.py:159,190` |
| Thông điệp lỗi trả client (cắt 500 ký tự, chỉ `error.message`) | `_upstream_error_message` (`client.py:830-853`) | ADR-0003 §2 |
| Endpoint HTTP, giới hạn body 32 MB | `GatewayServer.__init__` (`server.py:137-143`) và các `handle_*` | mục "Endpoint" `README.md`; `tests/test_server.py:220` |
| Trường trả về của `/auth/status` | `handle_auth_status` (`server.py:151-170`) | bảng lộ dữ liệu ADR-0003 §2; `tests/test_server.py:134` |
| Host/port mặc định, cảnh báo bind ngoài loopback | `server.py:37-38,111-122`; `manage.py:93-95` | `tests/test_server.py:231`, `tests/test_manage.py:40`; ADR-0002 §1 |
| Vòng đời daemon: spawn, PID file, healthcheck 10 s, stop | `cmd_start`/`cmd_stop`/`_run_daemon`/`_pid_is_gateway` (`manage.py:60-161`); đường dẫn PID/log (`server.py:42-49`) | `tests/test_x_manage_coverage.py:46-236`, `tests/test_manage.py:47,76`; ADR-0002 §2-3 |
| `status` (định dạng, mã thoát) | `cmd_status` (`manage.py:168-196`) | `tests/test_x_manage_coverage.py:238-266` |
| CLI subparser, cờ mới | `main` (`manage.py:463-517`), `__main__.py` | `tests/test_x_manage_coverage.py:411` |
| `setup` ghi `llm.yaml` cho software-company | `cmd_setup` (`manage.py:235-256`), mặc định model (`manage.py:52-53`) | `tests/test_manage.py:16`; không dùng khi `llm.yaml` đã có `backends:` |
| `models --probe/--probe-id/--probe-cli` | `_probe_antigravity`, `_probe_cli`, `classify_probe` (`manage.py:296-344`, `client.py:255-267`) | `tests/test_manage.py:135-233` |
| Đăng nhập OAuth PKCE (cổng 51121, `state`, timeout 300 s) | `login_pkce` (`auth.py:451-577`); hằng (`auth.py:57-72`) | `tests/test_login_pkce.py:193-388`; ADR-0002 §4 |
| Nơi lưu token, quyền file, ghi nguyên tử | `get_home_dir`/`default_token_file` (`auth.py:81-96`), `_atomic_write` (`auth.py:220-235`), `_update_account_fields` (`auth.py:255-275`) | `tests/test_account_pool.py:205-323`; ADR-0003 §1 |
| OAuth client id/secret, project id, biến môi trường | `auth.py:44-55,171-175,422-449` | mục "Dữ liệu và biến môi trường" `README.md` |
