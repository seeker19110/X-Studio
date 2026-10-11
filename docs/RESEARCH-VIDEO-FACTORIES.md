# Đối chiếu dự án tham khảo và thay đổi đã tích hợp

Khảo sát ngày 2026-10-11 qua README, tree, giấy phép và các file liên quan trên GitHub. Tham chiếu bổ sung: infographic và danh sách 28 thư viện video-as-code do chủ repo cung cấp. Không sao chép source của các dự án dưới đây; các phần tích hợp được viết cho event model và adapter hiện có của X-Studio.

| Nguồn | Điểm mạnh học hỏi | Điểm yếu của X-Studio được xử lý | Tích hợp cụ thể |
|---|---|---|---|
| [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo), [material_cache.py](https://github.com/harry0703/MoneyPrinterTurbo/blob/main/app/services/material_cache.py) | Asset cache, tổ chức tài nguyên, quy trình text → voice → video | Gọi lại API khi render thất bại | `render_cache.py`: SHA-256, checkpoint từng thành phần, atomic commit, kiểm checksum và sửa từng phần |
| [ShortGPT](https://github.com/RayVentura/ShortGPT), [abstract_content_engine.py](https://github.com/RayVentura/ShortGPT/blob/main/shortGPT/engine/abstract_content_engine.py) | Workflow từng bước có lưu trạng thái | Mất công việc khi khởi động lại | `Factory` + SQLite command queue + chương kịch bản có checkpoint, vẫn giữ event bus gốc |
| [short-video-maker](https://github.com/gyoridavid/short-video-maker) | Phụ đề đọc được, nhạc, video dọc, API/UI | Narration dài thành một khối phụ đề; thiếu music/footage | Caption pages, SRT/burn-in, ducking, `scene_video`, giao diện responsive |
| [NarratoAI](https://github.com/linyqh/NarratoAI) | Tập trung vào thao tác sản xuất từ một giao diện | CLI rời rạc, khó biết đang chờ bước nào | Console dự án, phòng dựng, review, log, pause/resume, cấu hình vận hành |
| [ClipsAI](https://github.com/ClipsAI/clipsai) | Tách clip, reframe và bố cục theo đầu ra | Render dài bằng graph lớn | Dựng độc lập từng cảnh, tái dùng segment. Chưa tích hợp semantic clipping hoặc tracking người nói |
| [Remotion](https://github.com/remotion-dev/remotion) | Đồ họa theo dữ liệu và template | Chữ/biểu đồ dễ bị vẽ sai bằng model ảnh | `MotionSpec` cho title/quote/bars; FFmpeg vẽ chữ Unicode. Không thêm React/Chromium vào runtime |
| [Motion Canvas](https://github.com/motion-canvas/motion-canvas) | Timeline xác định, animation phối hợp lời đọc | Motion khó tái tạo nhất quán | Motion template khai báo, cùng thời gian lời đọc và frame grid |
| [Manim](https://github.com/ManimCommunity/manim) | Biểu diễn trực quan chính xác từ dữ liệu | Biểu đồ ảnh sinh khó kiểm chứng | Mẫu bars buộc labels/values/source, từ chối số âm/NaN và trường code tùy ý |

## Giấy phép và phụ thuộc

MoneyPrinterTurbo, ShortGPT, short-video-maker, ClipsAI và Manim công bố mã theo MIT tại thời điểm khảo sát; vẫn cần kiểm tra LICENSE của commit cụ thể nếu vendoring về sau. Remotion có giấy phép và điều kiện thương mại riêng: không coi nó là MIT. X-Studio không vendor hoặc thêm dependency từ các repo tham khảo này.

Không cài tất cả 28 thư viện trong danh sách: Three.js/R3F, GSAP/Anime/Motion và Remotion/Revideo/Hyperframes có các vai trò trùng nhau; stack đầy đủ sẽ tăng runtime, bề mặt lỗi và công bảo trì. Quyết định hiện tại là Python + FFmpeg + UI web native. React engine, Manim, Whisper và video diffusion có thể là adapter sau khi có nhu cầu cụ thể và benchmark. Không tuyên bố đã tích hợp những engine này.

## Chất lượng, độ dài và chi phí

- Chất lượng cao không đồng nghĩa tăng mọi thông số: ưu tiên kịch bản có nguồn, giọng rõ, bố cục nhất quán, chữ do code vẽ, asset có quyền sử dụng và QC thật.
- [YouTube Help](https://support.google.com/youtube/answer/71673?hl=en) nêu giới hạn upload 12 giờ hoặc 256 GB, tùy giới hạn đạt trước; video trên 15 phút cần tài khoản đã xác minh. API/OAuth và điều kiện tài khoản vẫn áp dụng.
- Console nhận tối đa 720 phút; video dài được chia chương/cảnh. Chưa render benchmark 12 giờ hoặc upload thật trong đợt nâng cấp này.
- Chưa có một số “tiết kiệm X%” đáng tin vì chưa có bộ mẫu và máy chuẩn. Có kiểm thử chứng minh số lần gọi lại TTS/ảnh giảm về 0 khi cache hợp lệ, và bounded renderer không tạo graph toàn video.
- Tự động hóa có thể giữ dự án lại khi thiếu nguồn, không đủ ngân sách, lỗi model hoặc QC. Không có cam kết “không bao giờ lỗi” hay “luôn hoàn hảo”.
