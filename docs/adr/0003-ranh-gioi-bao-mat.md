# ADR-0003: Ranh giới bảo mật — token nằm ngoài repo, file 0600, log không chứa token, cổng không xác thực

Trạng thái: Accepted (ghi lại từ code đang chạy) · Ngày: 2026-09-06

Mọi dẫn chứng là `file:dòng` trong `gateway/src/gateway/` hoặc `gateway/tests/`. Chính sách chung của repo ở
`../../SECURITY.md`; ADR này chỉ nói phần gateway thực thi và phần gateway **không** hứa.

## Bối cảnh

Gateway giữ thứ nhạy cảm nhất trong hub: **refresh token Google** của nhiều tài khoản thật. Một refresh token bị lộ
= người khác dùng hạn mức của bạn cho tới khi bạn thu hồi ở Google. `SECURITY.md` xếp "gateway phục vụ token của tài
khoản này cho phiên của tài khoản khác" và "rò rỉ token ra log/artifact/repo" vào phạm vi phải xử lý.

Đồng thời gateway cố ý **không có xác thực client**: hai công ty gửi bearer bất kỳ (`COMPANY_LLM_API_KEY=gateway-local`).
Ranh giới tin cậy vì thế là **máy**, không phải **kết nối**.

## Quyết định

### 1. Token nằm một chỗ, ngoài cây repo, chỉ chủ sở hữu đọc được

- Đường dẫn: `$XAGENTS_HOME/auth/antigravity_tokens.json`, mặc định `~/.x-agents/` (`auth.py:81-86,95-96`). Không có
  đường dẫn nào trong repo; `.gitignore:43-44` chỉ bỏ `gateway/.venv/` vì không có gì khác để bỏ.
- Ghi bằng `_atomic_write` (`auth.py:220-235`): thư mục cha `mkdir(mode=0o700)`, file tạm mở bằng
  `os.open(..., O_WRONLY|O_CREAT|O_TRUNC, 0o600)` **ngay từ lúc tạo** — không có khoảng hở world-readable —
  rồi `os.chmod(S_IRUSR|S_IWUSR)` khi không phải Windows, và `os.replace` vào chỗ. Lỗi giữa chừng thì xoá file
  tạm (`auth.py:232-235`). Test đo quyền 0600/0700 chỉ chạy trên POSIX (`tests/test_account_pool.py:205,301`);
  nhánh chmod vẫn được phủ trên Windows bằng monkeypatch `os.name` (`tests/test_account_pool.py:307-312`).
- Trên Windows `0o600` của `os.open` và `os.chmod` **không** tương đương ACL "chỉ chủ sở hữu" — quyền file do ACL của
  `%USERPROFILE%` quyết định. Gateway không sửa ACL; đây là giới hạn đã biết.
- Xoá tài khoản (`remove_account`, `auth.py:277-291`) và xoá hết (`clear_credentials`, `auth.py:293-301`) đều xoá
  hẳn file khi pool trống; không để file rỗng chứa dấu vết.

### 2. Cái gì được ra ngoài, cái gì không

| Bề mặt | Có | Không có |
|---|---|---|
| `GET /auth/status` (`server.py:151-170`) | email, `project_id`, `expires_at`, `is_expired`, `has_refresh_token` (bool), `cooldown_remaining`, `last_failure_status`, `source` | access token, refresh token |
| `python -m gateway status` (`manage.py:185-191`) | email, project, hạn token, có/không refresh | giá trị token |
| log `gateway.log` (`client.py:900,915,924,933,941`; `auth.py:342,348,383,419`) | email, mã HTTP, model, `lần thử i/n`, số giây cooldown | token (test `tests/test_account_pool.py:112` khẳng định refresh token không xuất hiện trong log); URL đăng nhập chứa `state`/`code_challenge` được log ở `auth.py:520` — không phải bí mật sau khi dùng |
| thông điệp lỗi trả client (`client.py:833-853`) | `error.message` của Google, cắt 500 ký tự | nguyên body Google (có thể chứa chi tiết nội bộ) |

**Email là dữ liệu cá nhân** và có mặt ở cả ba bề mặt trên: `README.md` dặn không dán nguyên log vào issue công khai.
Gateway không che email vì đó là thứ duy nhất để người vận hành kiểm chứng xoay vòng (ADR-0001 §1).

### 3. Bearer của client không phải mật khẩu — nó chỉ là gợi ý chọn tài khoản

Header `Authorization: Bearer …` được server cắt ra và **xoá nếu là chuỗi giữ chỗ** (`server.py:39,220-223`), rồi
chuyển cho pool làm khoá ưu tiên (`auth.py:319-326`). Không có bearer nào bị từ chối. Điều này có nghĩa:

- Bind ngoài loopback là mở pool cho cả mạng (`server.py:115-122`, ADR-0002 §1). `SECURITY.md`: muốn dùng từ máy
  khác thì SSH tunnel.
- Bearer trùng email hoặc access token của một tài khoản **ghim** tài khoản đó lên đầu nhưng vẫn rơi sang tài khoản
  khác khi nó cooldown (`auth.py:329-334`). Gateway **không** cam kết "request của phiên A chỉ đi tài khoản A" — nếu
  cần cách ly tài khoản theo phiên thì phải chạy hai gateway với hai `XAGENTS_HOME`.

### 4. OAuth client id/secret của Antigravity là công khai, không phải bí mật của repo

`DEFAULT_CLIENT_ID`/`DEFAULT_CLIENT_SECRET` (`auth.py:49-54`) là client desktop công khai của Antigravity IDE, tách
làm ba mảnh chỉ để gitleaks không bắt nhầm mẫu `GOCSPX-…`. Ghi đè bằng `GATEWAY_ANTIGRAVITY_CLIENT_ID/SECRET/PROJECT_ID`
(`auth.py:45-47,171-175`). Đăng nhập là Authorization Code + **PKCE S256** với `state` ngẫu nhiên (`auth.py:458-462,
491-494`) — không có secret nào giữ được phiên đăng nhập ngoài refresh token nhận về.

### 5. Test không bao giờ chạm Google

Mọi test dùng `httpx.MockTransport`/`aiohttp TestClient`/monkeypatch `urllib` (`tests/`), token file ở `tmp_path`
(`tests/test_account_pool.py:37-38`). Không có test nào đọc `~/.x-agents`. Đây là luật cấm 4 của `AGENTS.md`.

## Đã cân nhắc và bỏ

- **Mã hoá file token bằng keyring/DPAPI.** Thêm phụ thuộc theo hệ điều hành; daemon không tương tác nên vẫn phải
  giữ khoá giải mã ở đâu đó trên cùng máy. Với mô hình "máy là ranh giới" nó không nâng mức bảo vệ thật.
- **Xác thực client bằng token cấu hình.** Hai công ty + console phải quản thêm một bí mật; và với loopback thì mọi
  tiến trình cùng user đã đọc được file token trực tiếp — xác thực cổng không chặn được kẻ đã ở trong máy.
- **Che email trong log.** Mất khả năng kiểm chứng xoay vòng, thứ ADR-0001 coi là bằng chứng duy nhất.

## Hệ quả

- Người dùng chịu trách nhiệm ACL của `~/.x-agents` trên Windows và toàn bộ máy khi bind ngoài loopback.
- Copy file token sang máy khác là **copy refresh token**: làm qua kênh an toàn, xoá bản tạm. Thu hồi ở Google
  (`myaccount.google.com` → quyền ứng dụng bên thứ ba) trước khi dọn dấu vết, đúng thứ tự `SECURITY.md`.
- Đổi trường trả về của `/auth/status` hay dòng log là đổi bề mặt lộ dữ liệu: rà bảng §2 trước khi thêm trường.
- Không có audit ai gọi gateway: bearer không định danh, log chỉ có tài khoản *phục vụ*, không có tài khoản *gọi*.
  Cần quy trách nhiệm theo phiên thì phải đọc audit-log của công ty, không phải log gateway.
