# gateway — proxy xoay vòng tài khoản Google Antigravity

Daemon cục bộ (mặc định `http://127.0.0.1:8100/v1`) nhận request theo chuẩn **OpenAI Chat Completions**,
dịch sang Google Code Assist (Gemini / Claude qua đăng nhập Antigravity OAuth) và tự động xoay vòng
nhiều tài khoản Google. Mọi công ty trong X-Agents dùng provider `openai` với `base_url` trỏ vào gateway,
không đổi code hay prompt (đúng nguyên tắc trung lập provider của `software-company/src/company/llm.py`).

Mang từ [donghanhcungban/Plugin-For-Hermes](https://github.com/donghanhcungban/Plugin-For-Hermes) (`bridge/`),
bỏ phần gắn với Hermes Agent (ProviderProfile, đồng bộ `auth.json`, `install.py`, adapter Claude Code CLI).

## Cơ chế xoay vòng

| Tình huống upstream | Gateway làm gì |
|---|---|
| 429 / 402 / 403, hoặc body có `RESOURCE_EXHAUSTED`, `quota`, `rate limit` | Thử **model anh em cùng tài khoản** (Gemini hết quota → Claude Sonnet, quota riêng). Vẫn lỗi → ghi cooldown tài khoản (tôn trọng `Retry-After`, mặc định 1 giờ) → tài khoản kế |
| 401 / `invalid_grant` | Cooldown 5 phút → tài khoản kế |
| Google từ chối refresh token (HTTP 4xx) | Cooldown 401 |
| Lỗi mạng khi refresh (timeout, DNS) | Bỏ qua tài khoản **lượt này**, không cooldown (một nhịp mạng chập chờn không được làm nguội cả pool) |
| 5xx endpoint chính | Thử endpoint dự phòng cùng tài khoản; stream thì sang tài khoản kế, không cooldown |
| 4xx khác (payload hỏng) | Trả lỗi ngay, không xoay |
| Mọi tài khoản đều cooldown (hoặc pool trống) | Trả **429** kèm "thử lại sau khoảng Ns" để lớp trên fallback tiếp (router của công ty khớp chuỗi này để nghỉ đúng số giây) |

Thời gian cooldown (`auth.py`): 401 → 300s; 402/403/429 → 3600s; mã khác → 60s; `Retry-After` ghi đè. Access token được làm mới
sớm 120s trước hạn. Gateway **không** tự retry hay backoff mũ: chỉ cooldown + xoay tài khoản; request upstream timeout 120s.

Thêm: bearer token của client trùng access token hoặc email một tài khoản thì tài khoản đó được ưu tiên; các chuỗi giữ chỗ
`dummy`, `none`, `token`, `default`, `antigravity`, `gateway-local`, `sk-gateway` bị bỏ qua (không ghim tài khoản).
Stream chỉ xoay tài khoản **trước** chunk đầu tiên; `finish_reason` thật (`tool_calls`, `length`) không bị đè bằng `stop`.
`usage` trả token thật từ `usageMetadata` (kể cả cache) để `software-company` ghi audit-log đúng.

## Dùng nhanh

```bash
cd gateway
uv sync
make login        # mở trình duyệt, đăng nhập Google; chạy lại để thêm tài khoản thứ 2, 3...   (login --no-browser: in URL)
make start        # daemon tại 127.0.0.1:8100   (start --foreground/-f chạy tiền cảnh; --host/--port)
make status       # server + từng tài khoản: sẵn sàng / cooldown / hạn token; exit 1 nếu server tắt hoặc 0 tài khoản sẵn sàng
make setup        # ghi ../software-company/llm.yaml: provider openai, base_url trỏ gateway (--target, --strong, --standard)
make fix          # ruff --fix
```

Không có `make`: `PYTHONPATH=src uv run python -m gateway <lệnh>`. OAuth loopback dùng cổng cố định `127.0.0.1:51121`
(`/oauth-callback`), chờ tối đa 300s; máy VPS không có trình duyệt thì đăng nhập ở máy cá nhân rồi copy file token.

`setup` chỉ ghi dạng **một provider** (`provider: openai`, `base_url`, `models.strong/standard`, `max_tokens`), không có tier
`light` và không có `backends:`. Nếu `llm.yaml` đã dùng `backends:` (nhiều gói, xem `../docs/HUONG-DAN-VAN-HANH.md` §3.2) thì các
khoá `setup` ghi ra bị bỏ qua — khi đó khai gateway là một backend `antigravity` thay vì chạy `setup`. Studio-creators: dùng
`--target ../Studio-creators/llm.yaml` hoặc khai backend tay.

Rồi ở `software-company`:

```bash
export COMPANY_LLM_API_KEY=gateway-local   # chuỗi bất kỳ; gateway tự xác thực Google
make run
```

Hoặc chỉ dùng biến môi trường, không cần `llm.yaml`:

```
COMPANY_LLM_PROVIDER=openai
COMPANY_LLM_BASE_URL=http://127.0.0.1:8100/v1
COMPANY_MODEL_STRONG=claude-sonnet-4-6
COMPANY_MODEL_STANDARD=gemini-3.7-flash
```

Lệnh khác: `python -m gateway reset [EMAIL]` (xóa cooldown), `python -m gateway logout EMAIL`, `python -m gateway stop`.

## Endpoint

| Method | Path | |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI-compatible, hỗ trợ `stream`, `tools`, ảnh base64 |
| GET | `/v1/models` | danh sách model |
| GET | `/auth/status` | pool: email, cooldown còn lại, hạn token (không lộ token) |
| POST | `/auth/login` | mở trình duyệt thêm tài khoản |
| GET | `/health` | |

Model: `gemini-3.7-flash` (+ `-medium`, `-low`), `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.1-pro`,
`claude-sonnet-4-6`, `claude-opus-4-6`, `gpt-oss-120b`. Alias được map về id nội bộ của Code Assist trong
`src/gateway/client.py` (`MODEL_ALIAS_MAP`); upstream thật chỉ có 4 model: `gemini-3-flash-agent` (nhận `gemini-3.7-flash*`,
`gemini-3.5-flash`, `gpt-oss-120b`), `gemini-3.6-flash-medium`, `gemini-3.1-pro-low`, `claude-sonnet-4-6` (nhận cả
`claude-opus-4-6`). Tên model lạ rơi về `gemini-3-flash-agent` kèm cảnh báo trong log, không báo lỗi.

## Dữ liệu và biến môi trường

- Token: `$XAGENTS_HOME/auth/antigravity_tokens.json` (mặc định `~/.x-agents/`), quyền 600, ghi nguyên tử. Không commit.
- PID/log: `~/.x-agents/gateway/gateway.pid`, `~/.x-agents/logs/gateway.log`.
- `GATEWAY_HOST`, `GATEWAY_PORT`; `GATEWAY_ANTIGRAVITY_CLIENT_ID/SECRET/PROJECT_ID` ghi đè OAuth client (hiếm khi cần).
  `.env.example` chỉ là tài liệu: gateway không đọc file `.env`, phải export biến trong shell.

Triển khai VPS: copy file token từ máy cá nhân lên, chạy `make start`; refresh token tự làm mới access token.

## Kiểm thử

```bash
make lint
make test        # 42 ca, không gọi mạng: httpx MockTransport + aiohttp TestClient
```
