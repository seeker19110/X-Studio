# ADR-0001: Xoay vòng tài khoản — LRU + cooldown theo mã lỗi, không retry, thứ tự thử cố định trong một lượt

Trạng thái: Accepted (ghi lại từ code đang chạy) · Ngày: 2026-09-06

Mọi dẫn chứng là `file:dòng` trong `gateway/src/gateway/` tại commit viết ADR. Đổi code thì đổi ADR.

## Bối cảnh

Hai công ty gọi gateway như một provider OpenAI duy nhất; phía sau là **nhiều tài khoản Google** cùng đi Code
Assist qua đăng nhập Antigravity, mỗi tài khoản một hạn mức riêng. Ba điều kiện đặt ra cơ chế xoay:

1. Quota của Code Assist có hai lớp độc lập trên cùng tài khoản — Gemini và Claude tính riêng
   (`client.py:85-87`), nên "tài khoản hết quota" thường chỉ là "hết quota *một họ model*".
2. Code Assist **không gửi header `Retry-After`**; nó ghi thời điểm hồi trong thân thông điệp
   (`"Resets in 7m29s"`, `client.py:100-103`). Cooldown mặc định 1 giờ mà không đọc thân thì cho tài khoản nghỉ
   dài gấp nhiều lần mức cần (`TRAPS.md`, bẫy "hạn mức bị che").
3. Lớp trên (`software-company/src/company/routing.py:34,39`, `Studio-creators/src/studio/routing.py`) đã có
   router riêng: nghỉ backend đúng số giây và đổi backend khác. Gateway retry thêm là retry hai tầng — thời gian
   chờ nhân đôi và không ai biết ai đang chờ.

## Quyết định

### 1. Thứ tự ứng viên cho một lượt: bearer khớp lên đầu, còn lại LRU

`AntigravityAuthManager.resolve_credential_candidates` (`auth.py:305-368`) là **nơi duy nhất** quyết định thứ tự.
Mỗi lượt request nó đọc lại file token (`auth.py:315`), rồi sắp theo khoá `(not matches_bearer(c), c.last_used_at)`
(`auth.py:329`):

- Bearer của client trùng access token, tiền tố `ya29.` + 20 ký tự đầu, hoặc trùng email (không phân biệt hoa
  thường) thì tài khoản đó được "ghim" lên đầu (`auth.py:319-326`). Các chuỗi giữ chỗ `dummy`, `none`, `token`,
  `default`, `antigravity`, `gateway-local`, `sk-gateway` bị server xoá trước khi tới đây (`server.py:39,222-223`),
  nên `COMPANY_LLM_API_KEY=gateway-local` không ghim gì cả.
- Phần còn lại theo **LRU**: `last_used_at` nhỏ nhất (lâu chưa dùng nhất) đi trước (`auth.py:113,329`).
- Tài khoản đang cooldown bị bỏ khỏi danh sách (`auth.py:333-334`); tài khoản hết hạn access token được refresh
  ngay trong bước này (`auth.py:335-352`).

Chỉ ứng viên **đầu tiên** được đóng dấu `last_used_at` (`auth.py:366-367`), và dấu luôn `max(now, newest + 1ms)` vì
đồng hồ Windows thô ~15 ms: hai lượt cùng mốc thì khoá LRU hoà, thứ tự rơi về thứ tự trong file và pool "kẹt" ở
một tài khoản (`auth.py:363-365`; test `tests/test_account_pool.py:170`).

Hệ quả đọc log: `lần thử i/n` trong dòng `INFO gateway.client: <model> → <email> (lần thử i/n)`
(`client.py:900`) là **vị trí trong danh sách của lượt đó**, không phải số thứ tự cố định — lượt trơn tru luôn
là `1/n`; `2/n` trở lên nghĩa là đã bỏ qua tài khoản nào đó. Danh tính nằm ở email.

### 2. Trong một lượt: thử theo đúng thứ tự, mỗi tài khoản tối đa ba nhịp, không quay lại

`AntigravityClient.create_chat_completion` (`client.py:880-951`) duyệt danh sách một lần, `for index, creds in
enumerate(candidates, start=1)` (`client.py:888`). Với mỗi tài khoản:

| Upstream trả | Gateway làm (dẫn chứng) |
|---|---|
| 200 | trả về, ghi log tài khoản đã phục vụ (`client.py:900-901`) |
| 401/402/403/429 hoặc thân có `resource_exhausted`, `rate limit`, `quota`, `invalid_grant`, `token expired` (`client.py:89-96`) | model Gemini → thử **model anh em `claude-sonnet-4-6` trên cùng tài khoản** (`client.py:87,906-913`); vẫn lỗi cùng họ → cooldown tài khoản, sang tài khoản kế (`client.py:914-926`) |
| 4xx khác (payload hỏng, model lạ) | ném `UpstreamError` ngay, **không xoay** — tài khoản khác không cứu được (`client.py:929-931`) |
| 5xx endpoint chính `daily-cloudcode-pa` | thử endpoint dự phòng `cloudcode-pa` cùng tài khoản (`client.py:30-31,933-935`); vẫn lỗi có thể xoay được → cooldown + tài khoản kế (`client.py:941-946`) |
| hết danh sách | ném lỗi của response cuối cùng với mã HTTP thật (`client.py:948-951`) |

Stream (`client.py:953-1053`) chỉ xoay **trước chunk đầu tiên** (`client.py:954`): 5xx lúc mở stream → tài khoản kế
**không cooldown** vì là lỗi phía Google (`client.py:975-978`); 4xx thuộc họ quota → cooldown rồi sang tài khoản kế
(`client.py:979-984`). Đã phát chunk rồi thì lỗi giữa chừng thành chunk `error` + `[DONE]` (`server.py:252-255`),
không đổi tài khoản.

### 3. Hạn mức nghỉ (cooldown) ghi vào file, theo mã lỗi, `Retry-After`/"Resets in" ghi đè

`mark_account_unavailable` (`auth.py:370-384`): `COOLDOWN_DEFAULTS = {401: 300, 402: 3600, 403: 3600, 429: 3600}`,
mã khác `COOLDOWN_FALLBACK = 60` (`auth.py:77-78`). Nếu có gợi ý từ upstream thì `max(1, int(float(retry_after)))`
thắng (`auth.py:375-377`). Gợi ý lấy bằng `cooldown_hint` (`client.py:123-131`): header `Retry-After` trước, không có
thì đọc `"Resets in XhYmZs"` trong thân bằng `reset_hint_seconds` (`client.py:104-120`).

Cooldown ghi hai trường `unavailable_until`, `last_failure_status` vào file token qua `_update_account_fields`
(`auth.py:255-275,378-381`) — **không ghi đè cả object**, vì request khác có thể vừa refresh token mới trong lúc request
này chờ upstream. Xoá cooldown bằng tay: `python -m gateway reset [EMAIL]` → `mark_account_healthy` (`auth.py:386-388`).

Refresh access token: làm sớm `REFRESH_SKEW_SECONDS = 120` trước hạn (`auth.py:73,121`), gọi Google với timeout 20 s
(`auth.py:409`). Google từ chối (HTTP 4xx) → cooldown như 401 (`auth.py:340-345`); lỗi mạng → **bỏ qua lượt này,
không cooldown** (`auth.py:346-352`), để một nhịp DNS chập chờn không làm nguội cả pool.

### 4. Cả pool nghỉ → 429 kèm số giây thật, và không retry trong gateway

Không còn ứng viên: gateway tính mốc `unavailable_until` sớm nhất và ném `UpstreamError("Mọi tài khoản Antigravity
đều đang cooldown hoặc hết hạn. Thử lại sau khoảng {wait}s.", 429)` (`auth.py:358-362`). Server đọc lại số giây đó
và đặt header `Retry-After` chuẩn (`server.py:95-99`). Router của công ty khớp cả header lẫn chuỗi tiếng Việt
(`software-company/src/company/routing.py:38-39`).

Gateway **không có** retry hay backoff mũ: một lượt = một lần duyệt danh sách. Timeout upstream là
`UPSTREAM_TIMEOUT_S` = 900 s mặc định, chỉnh bằng `GATEWAY_UPSTREAM_TIMEOUT_S` (`client.py:862`); hết giờ trả 504 kèm
thông điệp có nội dung (`server.py:57-58,70-72`), không phải 500 thân rỗng.

## Đã cân nhắc và bỏ

- **Round-robin theo bộ đếm trong RAM.** Mất khi daemon khởi động lại; hai tiến trình (CLI `models --probe` và
  daemon) cùng đọc pool sẽ có hai bộ đếm. `last_used_at` trong file là trạng thái duy nhất, sống qua restart.
- **Cooldown cố định 1 giờ cho mọi 429.** Là hành vi cũ; với hạn mức "Resets in 7m" nó lãng phí 88 % thời gian tài
  khoản trên pool nhiều tài khoản (`client.py:100-103`).
- **Retry trong gateway với backoff.** Lớp trên đã retry/đổi backend theo `Retry-After`; hai tầng retry làm thời
  gian chờ không dự đoán được và giấu con số thật khỏi người vận hành.
- **Xoay tài khoản giữa stream.** Chunk đầu đã đi rồi thì client đã nhận `id` và có thể đã in nội dung; đổi tài
  khoản giữa chừng làm nội dung ghép hai lượt sinh khác nhau.

## Hệ quả

- Cooldown là **của file token**, không phải của tiến trình: hai proxy đọc hai file khác nhau (gateway và bridge
  Hermes) không thấy cooldown của nhau dù cùng tài khoản Google (`README.md` "Cổng đã có người giữ").
- Đổi `COOLDOWN_DEFAULTS`, chuỗi "Thử lại sau khoảng Ns" hay dòng log `lần thử i/n` là đổi **hợp đồng** với hai
  `routing.py` và với người đọc log — `CODEMAP.md` liệt kê chỗ phải sửa theo.
- Mỗi lượt request đọc và có thể ghi file token (LRU stamp, cooldown, refresh). File nhỏ, ghi nguyên tử, khoá
  `threading.RLock` trong tiến trình (`auth.py:165,314`); giữa nhiều tiến trình thì không có khoá — chấp nhận vì
  chỉ có daemon ghi thường xuyên, CLI ghi khi người gõ lệnh.
- Test bao cả hai chiều cho từng nhánh: `tests/test_account_pool.py` (cooldown, refresh 4xx vs mạng, bearer, LRU,
  đồng hồ thô), `tests/test_failover.py` (model anh em, endpoint dự phòng, stream, dòng log).
