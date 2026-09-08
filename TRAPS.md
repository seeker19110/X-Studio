# TRAPS.md — bẫy riêng của Studio-creators

Bốn khuôn chung ở `../TRAPS.md` §1 áp nguyên vẹn (cùng kiến trúc bus/orchestrator/gate). Dưới đây là chỗ cắn người
riêng của xưởng video. Package này vận hành thật ít hơn software-company nên danh sách ngắn — **thêm khi mắc**, đừng
để trống cho có.

| Bẫy | Đã xảy ra | Chốt chặn / lần sau |
|---|---|---|
| Lệnh TTS mất tiếng Việt trên Windows | Đối số qua codepage console thành mojibake (#54) | Đã sửa + cổng CI chặn cả lớp lỗi; đưa chuỗi qua file/stdin UTF-8, không qua argv |
| `ffmpeg` thiếu → test bỏ qua, tưởng xanh | Test ghép video "pass" vì skip | Đọc số `skipped` trong output; CI có ffmpeg; máy dev muốn kiểm thật thì cài ffmpeg |
| Dựng lại cả video khi sửa một cảnh | Tốn render, mất cảnh đã đạt | ADR-0004: scene manifest có version, editor khoá cảnh đạt, renderer chỉ sinh phần bị chạm |
| Đăng/lên lịch trước khi qua gate | Chưa xảy ra — và phải giữ như thế | ADR-0002; `platform.py` chỉ được gọi sau `publish-events` có gate approve |
| Số liệu do model "ước" thay vì nạp | Insight không có số thật thì vô nghĩa | `youtube sync-*` nạp `performance-snapshots`; agent chỉ diễn giải |
| ~~Mọi backend nghỉ → `LLMError` (không hoãn như company)~~ **đã vá K3.3d** | Lỗi vận chuyển đọc ra "agent trả lời sai": ghi `agent_failed`, desk đếm một lần hỏng, event bị đóng dấu `orchestrated` nên **không bao giờ được làm lại** | `routing` nay ném `TransientError`; `orchestrator` bắt riêng ở cả ba chỗ gọi model (`_call`, `_plan`, `_decide`) và HOÃN event — `tick` sau thử lại, tôn trọng hẹn "thử lại sau Ns". Hai công ty nay cùng một hành vi, không còn phải nhớ hai kiểu |
| ~~Bộ lọc injection không chuẩn hoá ký tự vô hình~~ **đã vá K3.4** | `igno\u200bre previous instructions` đi thẳng qua bộ lọc — lỗ hổng thật, không phải khác biệt phong cách | Mẫu + `normalize()` nay ở `xagents_core.guard`, dùng chung với company |
| ~~Mẫu tiếng Việt lỏng: `quên + hướng dẫn` trần~~ **đã vá K3.4** | Báo nhầm câu bình thường ("tôi quên hướng dẫn cài đặt rồi" — đo được 3/3 câu lành). Mỗi lần báo nhầm là một bình luận khán giả bị bỏ oan | Mẫu chung đòi HOẶC từ chỉ lượng HOẶC từ bổ nghĩa sau; hai mẫu riêng của phòng ban (`developer mode`, `jailbreak`) giữ ở `CORE.extra_injection_patterns` |
| README số liệu lệch | 2026-09 (#46) | Test đếm từ đĩa; sửa README khi thêm test/ADR |

## Cách rà khi có lỗi mới

Cùng câu hỏi với software-company: nó thuộc khuôn nào trong bốn khuôn? Phần media thêm câu thứ năm: *"kết quả này
đo từ file thật (`qc.py` ffprobe) hay từ lời model?"*
