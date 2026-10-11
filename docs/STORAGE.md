# Dung lượng của factory và giới hạn nhận dự án

Console hiển thị dung lượng trong Cấu hình. Cấu hình qua `production.storage`
trong media YAML, khởi động lại factory để áp dụng:

```yaml
production:
  storage:
    soft_limit_bytes: 85899345920   # cảnh báo từ 80 GiB
    hard_limit_bytes: 107374182400  # chặn dự án mới từ 100 GiB
    scan_interval_s: 30
    max_scan_entries: 100000
```

Cả hai ngưỡng mặc định là **0 (tắt)**. Không có dọn file tự động. Khi hard limit
bật, kiểm tra dung lượng mới nhất lúc nhận yêu cầu và trước khi bắt đầu dự án.
Dự án còn trong hàng đợi nhưng chưa phát brief được giữ lại nếu bị chặn, và có
thể tiếp tục khi đủ điều kiện. Dự án đã phát brief, job đang render, gate duyệt,
pause/run và settings không bị hủy bởi admission. Không xóa cache trả phí,
media người dùng, journal hoặc audit để cố đạt ngưỡng.

## Phạm vi đo

- Toàn bộ `output_dir`: output, cache media trả phí/scene video/chương/segment và
  thư mục tạm render nằm dưới đây.
- `upload_dir` được cấu hình riêng nếu nằm ngoài output.
- File SQLite của factory và sidecar `-wal`, `-shm`, `-journal`, kể cả nằm ngoài
  output. Không quét cả thư mục cha của DB.
- Không đếm trùng root lồng nhau hoặc hardlink cùng inode. Đây là số byte logic
  của file thường; không phải số block vật lý đã cấp trên ổ đĩa.

Không theo symlink, kể cả symlink trong thành phần đường dẫn trước `..`.
Đường dẫn được giữ nguyên ngữ nghĩa filesystem thay vì rút gọn `..` bằng chuỗi;
root có thành phần symlink được báo scan không đầy đủ. `..` thông thường không
qua symlink vẫn được hỗ trợ. Áp dụng cho output/cache, upload và DB/sidecars.
Scan thiếu quyền, lỗi hoặc vượt số entry được báo không đầy
đủ; nếu hard limit bật thì không coi số đo thiếu là đủ điều kiện nhận việc.
Số liệu giao diện được cache theo khoảng cấu hình. Khi hard limit bật, trước khi
cho nhận việc luôn quét mới; dự án đang bị chặn trong hàng đợi dùng lại kết quả
blocked còn hạn, nên có thể chờ tới khoảng scan kế tiếp (mặc định 30 giây) sau
khi dung lượng giảm. Khi hard limit tắt, admission dùng quan sát có cache.
Phân nhóm cache/tạm dựa trên vị trí và tên thư mục; tổng byte không phụ thuộc
việc một file được xếp vào nhóm nào.

## Đây không phải filesystem quota

Các ngưỡng chỉ bảo vệ admission của **Factory/console**. CLI chạy orchestrator
trực tiếp không đi qua lớp này. Không ước lượng hay giữ chỗ dung lượng tương lai,
không chặn syscall ghi file, không dừng encode/provider đang chạy. Nhiều dự án
được nhận khi còn dưới ngưỡng có thể cùng tăng dung lượng vượt ngưỡng sau đó.

Scan không phải snapshot nguyên tử: file đang ghi có thể thay đổi trong/sau lúc
đếm. File đã unlink nhưng còn được tiến trình mở, metadata filesystem, sparse/
compressed allocation, file tạm ngoài roots, model GPU hoặc cache bên ngoài
factory và storage từ dịch vụ/ComfyUI remote không được cộng. Nếu nhiều factory
dùng roots chồng nhau, mỗi factory báo phạm vi riêng; không có quota dùng chung.
`render.min_free_disk_bytes` và budget segment hiện có vẫn là bảo vệ riêng ở
renderer. Muốn trần ghi cứng phải cấu hình quota ở hệ điều hành/filesystem.

Nếu bị chặn, kiểm tra số liệu và cấu hình, đối soát/di chuyển dữ liệu bằng quy
trình vận hành của bạn, hoặc tăng ngân sách phù hợp. Không xóa file của job đang
chạy hoặc receipt/journal phục hồi chỉ để mở admission.
