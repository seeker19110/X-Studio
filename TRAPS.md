# TRAPS.md — bẫy riêng của gateway

Gateway là nơi khuôn 1 (`../TRAPS.md`: *chế độ hỏng không tự khai báo*) cắn nhiều nhất, vì nó đứng giữa hai bên đều
không nhìn thấy nhau.

| Bẫy | Đã xảy ra | Chốt chặn / lần sau |
|---|---|---|
| Timeout upstream 120s → `HTTP 500` thân RỖNG | `str(httpx.ReadTimeout(""))` là chuỗi rỗng; công ty retry vô ích (2026-09-04) | Thông điệp riêng cho timeout; đọc dấu thời gian: mọi lần hỏng cách nhau đúng 120,0s là timeout |
| Hạn mức 82 giờ bị che bằng cooldown mặc định 1 giờ | Pool "hết" lặp lại mỗi giờ, không ai thấy con số thật | Tôn trọng `Retry-After`; ghi số giây thật vào thông điệp 429 |
| Structured output về qua `tool_calls`, client đọc `content` rỗng, mã 200 | Không lỗi nào để bắt | Client của công ty đọc cả hai; gateway giữ `finish_reason` thật |
| Lỗi mạng khi refresh làm nguội cả pool | Một nhịp DNS chập chờn → mọi tài khoản cooldown | Bỏ qua tài khoản lượt này, không cooldown |
| Stream xoay tài khoản giữa chừng | Chunk đầu đã đi rồi thì không thể đổi | Chỉ xoay **trước** chunk đầu; 5xx giữa stream → sang tài khoản kế, không cooldown |
| Bearer token giữ chỗ bị coi là ghim tài khoản | `dummy`, `gateway-local`… | Danh sách bỏ qua trong `auth.py`; token trùng email/access token mới ghim |
| `usage` không có token cache | Audit-log công ty đếm thiếu → chi phí sai | Trả đủ từ `usageMetadata` |
| Đổi chuỗi "thử lại sau khoảng Ns" | Chưa xảy ra — và router hai công ty khớp chuỗi này | Đổi là phải sửa cả hai `routing.py` |

## Cách rà

Với mỗi nhánh lỗi trong `client.py`/`auth.py`: *"người vận hành đọc log này có biết đích xác chuyện gì không?"* và
*"lớp trên nhận mã này sẽ làm gì — retry, chờ bao lâu, đổi backend?"* Nếu câu trả lời là "chờ mặc định", đó là bẫy.
