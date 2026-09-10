# ADR-0004: Ranh giới điều khoản — gateway không hứa việc dùng nhiều tài khoản là hợp lệ

Trạng thái: Accepted · Ngày: 2026-09-07 · K8.1 kịch bản B

ADR-0003 nói gateway giữ token **an toàn** đến đâu. ADR này nói một câu khác hẳn, và là câu chưa ai viết ra:
giữ an toàn không có nghĩa là **được phép**. Ranh giới ở đây là điều khoản dịch vụ của nhà cung cấp, không phải
mã nguồn — nên nó không sửa được bằng code, chỉ nói rõ được.

## Bối cảnh

Cơ chế lõi của gateway (`docs/adr/0001-xoay-vong-tai-khoan.md`) là **xoay vòng nhiều tài khoản Google** để đi
vòng quanh hạn mức của từng tài khoản. `make login` chạy lại nhiều lần để thêm tài khoản thứ 2, thứ 3; khi một
tài khoản trả 429 thì pool chuyển sang tài khoản kế.

Đọc từ góc kỹ thuật, đó là load balancing. Đọc từ góc điều khoản, nó có thể là chuyện khác: phần lớn nhà cung
cấp AI tiêu dùng cấm dùng nhiều tài khoản để vượt hạn mức, và hậu quả nặng nhất **không** phải bị chặn request
mà là **khoá tài khoản Google** — cùng tài khoản đang giữ email, Drive, thanh toán.

Repo trước ADR này mô tả cơ chế rất kỹ mà **không nói một chữ nào** về rủi ro đó. Người mới đọc `README.md`, gõ
`make login` ba lần theo hướng dẫn, và không có chỗ nào để họ nhận ra mình vừa quyết định một chuyện có hậu quả
ngoài phạm vi kỹ thuật. Đó là thiếu sót của tài liệu, không phải của code.

## Quyết định

### 1. Rủi ro thuộc về người vận hành, và phải được nói TRƯỚC khi họ gõ lệnh thứ hai

`gateway/README.md` có mục **"Rủi ro tài khoản"** đặt ngay dưới khối `make login` — không phải ở cuối file, không
phải trong ADR mà người mới không đọc. Nội dung nói ba điều: nhà cung cấp thường cấm gì, hậu quả là gì, và ai
chịu.

Gateway **không** kiểm, không cảnh báo lúc chạy, không đếm số tài khoản. Thêm một cảnh báo ở `login` nghe có vẻ
tử tế nhưng thực chất là chuyển một quyết định pháp lý thành một dòng log người ta bấm qua. Chỗ đúng của nó là
tài liệu người đọc trước khi bắt đầu.

### 2. Mặc định của repo là MỘT tài khoản

`make llm` cài hồ sơ trỏ vào gateway chỉ khi máy **đã** có tài khoản đăng nhập (K8.2). Hồ sơ nhiều tài khoản
không phải mặc định và không phải thứ bạn rơi vào vì làm theo hướng dẫn — nó là lựa chọn có tên, gõ ra thì mới
có. Không ai được vào chế độ nhiều tài khoản một cách tình cờ.

### 3. Repo không đưa lời khuyên pháp lý, và cũng không giả vờ là đã kiểm

Chúng tôi không đọc thay bạn điều khoản của Google/Anthropic/OpenAI, không theo dõi khi nó đổi, và không khẳng
định cách dùng nào là hợp lệ. Câu duy nhất repo nói chắc: **đây là hành vi có rủi ro, bạn là người quyết**.

## Hệ quả

- Người mới đọc `README.md` gặp rủi ro trước khi gặp lệnh thứ hai của `make login`.
- Root `README.md` dòng gateway dẫn tới mục này, để người chọn công cụ biết trước khi cài.
- Khi nhà cung cấp đổi điều khoản, chỗ phải sửa là **một** mục trong `gateway/README.md`, không rải rác.
- Không thêm code, không thêm test — cố ý. Đây là ranh giới không kiểm được bằng máy.

## Phương án đã cân nhắc và bỏ

| Phương án | Vì sao bỏ |
|---|---|
| Cảnh báo ở `login` lần thứ hai, bắt gõ `yes` | Biến quyết định pháp lý thành thao tác bấm qua; và người đã gõ `make login` lần hai thì đã quyết rồi — cảnh báo tới muộn |
| Trần cứng số tài khoản trong code | Con số nào cũng tuỳ tiện, và nó ngụ ý "dưới ngưỡng này thì repo bảo đảm hợp lệ" — điều repo không thể bảo đảm |
| Chỉ ghi trong `SECURITY.md` | Sai chỗ: `SECURITY.md` nói về bảo vệ token, còn đây là điều khoản. Người tìm rủi ro tài khoản không mở file bảo mật |
