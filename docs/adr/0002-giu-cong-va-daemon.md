# ADR-0002: Cổng 1123 trên loopback, daemon tách tiến trình, PID file + healthcheck thay cho lock

Trạng thái: Accepted (ghi lại từ code đang chạy) · Ngày: 2026-09-06

Mọi dẫn chứng là `file:dòng` trong `gateway/src/gateway/`. Đổi code thì đổi ADR.

## Bối cảnh

Gateway phải sống **lâu hơn** phiên shell đã khởi động nó: công ty chạy hàng giờ, console mở riêng, người vận hành
đóng terminal. Đồng thời trên cùng máy có thể có một proxy khác cùng họ — bridge của `Plugin-For-Hermes` nghe ở
`127.0.0.1:8100`, và gateway trước đây cũng lấy 8100 nên hai bên tranh cổng (`README.md` "Cổng đã có người giữ").
Triệu chứng tranh cổng đọc rất giống "gateway hỏng": `start` chờ hết 10 giây rồi báo *healthcheck quá hạn*, `status`
báo OFFLINE, trong khi `curl` vào cổng vẫn trả 200.

Gateway **không có xác thực client** (`server.py:116`): ai nối được vào cổng là dùng được cả pool tài khoản Google.
Vì vậy câu hỏi "nghe ở đâu" là câu hỏi bảo mật, không chỉ tiện dụng.

## Quyết định

### 1. Mặc định `127.0.0.1:1123`; ra ngoài loopback phải cố ý và bị cảnh báo

`DEFAULT_HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")`, `DEFAULT_PORT = int(os.getenv("GATEWAY_PORT", "1123"))`
(`server.py:37-38`). 1123 chọn để không đụng 8100 của bridge Hermes; hai proxy chạy song song được khi khác cổng
(`README.md`). `--host/--port` có ở `start`, `status`, `setup`, `models` (`manage.py:473-475`).

`is_loopback_host` nhận `127.0.0.1`, `localhost`, `::1`, `[::1]`, và mọi `127.*` (`server.py:111-112`). Host khác →
`warn_if_public_host` ghi WARNING lúc chạy server (`server.py:115-122,273`) và `cmd_start` in cảnh báo ra console
(`manage.py:93-95`). Không chặn — VPS sau firewall là ca dùng hợp lệ — nhưng không im lặng.

### 2. Vòng đời daemon: spawn tiến trình con tách session, log ra file, PID ghi file

`cmd_start` (`manage.py:91-137`):

1. Nếu `/health` đã trả `{"service": "gateway"}` → in "đã chạy sẵn", exit 0, **không spawn** (`manage.py:96-98`;
   `is_server_running` ở `server.py:278-285` kiểm đúng trường `service`, không chỉ kiểm cổng mở).
2. `--foreground/-f` → chạy `run_server` ngay trong tiến trình hiện tại (`manage.py:99-104`).
3. Mặc định → `subprocess.Popen` tiến trình Python con gọi `gateway.manage._run_daemon` (`manage.py:108-125`):
   stdout/stderr nối vào `~/.x-agents/logs/gateway.log` (`server.py:46-49`), stdin `DEVNULL`,
   `PYTHONUNBUFFERED=1`. Windows: `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW`
   (`manage.py:113-114`); POSIX: `start_new_session=True` (`manage.py:124`) — đóng terminal không kéo daemon theo.
4. PID con ghi vào `~/.x-agents/gateway/gateway.pid` (`server.py:42-43`, `manage.py:126`).
5. Poll `/health` mỗi 0,3 s tối đa **10 s** (`manage.py:128-135`). Không lên → exit 1 kèm đường dẫn log
   (`manage.py:136-137`). Tiến trình con lúc này **vẫn có thể còn sống** hoặc đã chết vì không bind được cổng;
   `start` không giết nó.

`_run_daemon` (`manage.py:74-88`) đăng ký `atexit` xoá PID file **chỉ khi nội dung file đúng là PID của mình**
(`manage.py:82-85`) — tránh daemon cũ thoát muộn xoá PID của daemon mới.

`cmd_stop` (`manage.py:140-161`): đọc PID; Linux kiểm `/proc/<pid>/cmdline` có chữ `gateway` trước khi gửi
SIGTERM (`_pid_is_gateway`, `manage.py:60-71`) để không giết nhầm tiến trình tái dùng PID; hệ khác bỏ qua kiểm
này. Windows dùng `taskkill /F /PID` (`manage.py:153-154`). Luôn xoá PID file kể cả khi giết thất bại
(`manage.py:160`).

### 3. Không có lock file; "một daemon một cổng" đảm bảo bằng healthcheck + hệ điều hành

Gateway **không** giữ file lock. Chống chạy trùng gồm hai lớp: (a) `start` hỏi `/health` trước khi spawn
(`manage.py:96`); (b) tiến trình thứ hai không bind được cổng sẽ chết, healthcheck của `start` quá hạn và người
dùng thấy exit 1. Khoá duy nhất trong code là `threading.RLock` của pool tài khoản (`auth.py:165`), bảo vệ file
token **trong một tiến trình**, không liên quan tới cổng.

### 4. Cổng OAuth loopback riêng, cố định

Đăng nhập dùng HTTP server tạm ở `localhost:51121/oauth-callback` (`auth.py:70-72,517`), chờ tối đa 300 s
(`auth.py:455`), kiểm `state` (`auth.py:491-494`), đóng ngay khi có mã (`auth.py:526-530`). Cổng này khác 1123 và
chỉ sống trong lúc `login`; máy không có trình duyệt thì `login --no-browser` in URL (`auth.py:522-523`) hoặc copy
file token từ máy khác.

## Đã cân nhắc và bỏ

- **Lock file bên cạnh PID file.** Thêm một trạng thái phải dọn khi crash (lock mồ côi chặn `start` mãi). Healthcheck
  trả lời đúng câu hỏi thật — "có gateway đang phục vụ ở cổng này không" — còn lock chỉ trả lời "có ai từng khoá".
- **Giữ 8100 và bảo người dùng tắt bridge.** Hai proxy phục vụ hai hệ (Hermes và X-Agents) trên cùng máy là ca thật;
  đổi cổng mặc định rẻ hơn bắt người dùng chọn.
- **Bind `0.0.0.0` mặc định để dùng từ máy khác.** Không xác thực client → mở là chia sẻ tài khoản Google cho cả
  mạng. Đường đúng là SSH tunnel (`SECURITY.md`).
- **Tự chọn cổng trống khi 1123 bận.** Hai công ty và console đều cấu hình `base_url` cố định; cổng trôi thì mọi
  `llm.yaml` sai mà không ai báo. Thà báo lỗi rõ.

## Hệ quả

- "Healthcheck quá hạn" có **hai** nghĩa: gateway không lên (xem log) hoặc cổng đã có người khác giữ (`curl
  /health` xem trường `service`/`bridge`). `TRAPS.md` ghi cách phân biệt.
- Trên Windows và macOS `stop` không kiểm cmdline; PID tái dùng có thể bị `taskkill` nhầm — hiếm vì PID file được
  xoá lúc thoát bình thường, nhưng là giới hạn đã biết (`manage.py:63-64`).
- `status` exit 1 khi OFFLINE **hoặc** 0 tài khoản sẵn sàng (`manage.py:196`) — script chỉ đọc mã thoát không phân
  biệt được hai ca; đọc dòng `Server:` nếu cần.
- Không có supervisor/restart tự động: daemon chết là chết, `status` phải được ai đó gọi. Console hiển thị trạng
  thái gateway nhưng không khởi động lại.
