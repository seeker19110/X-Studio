# ADR-0013 — Phim, hoạt hình và sinh clip theo cảnh

Ngày: 2026-10-11. Trạng thái: chấp nhận. Bổ sung ADR-0004, 0009, 0010, 0012.

## Vấn đề

Pipeline hiện có dựng ảnh/footage thành video thuyết minh, nhưng brief chưa phân biệt
phim và hoạt hình, chưa giữ hồ sơ nhân vật/phong cách trong manifest, chưa có adapter
sinh video từng shot. Các model video có graph, giới hạn frame, GPU và license khác nhau.

## Quyết định

- `CinemaSpec` chứa loại dự án, chế độ hình ảnh, phong cách, logline, character bible,
  negative prompt và seed. Request đi vào brief và context của các vai hiện có; code
  đóng dấu cấu hình người dùng vào manifest. Không thêm vai hoặc sửa prompt/skill hiện có.
- `production_type` (`explainer`, `film`, `animation`) độc lập `format` (16:9/9:16).
  `visual_mode=images` là bản dựng ảnh có chuyển động; `generated_video` tạo clip ngắn.
  Lời đọc vẫn là một narrator, dùng thời lượng đo từ TTS; chưa có nhiều diễn viên/lip-sync.
- `scene_video` là provider riêng (`none`, `fake`, `comfyui`, `command`), độc lập
  `video.provider=ffmpeg` là bộ ghép. Mặc định `none`; demo khai `fake` và ghi rõ mô phỏng.
- ComfyUI dùng graph API-format do người vận hành chuẩn bị. Bindings chỉ sửa scalar input
  tồn tại; không thay link hoặc chạy graph/script từ model. Chọn output node cụ thể và chỉ
  nhận MP4 trong `images`, `gifs` hoặc `videos`; tải qua `/view` cùng endpoint, không redirect.
- Mỗi accepted job lưu `prompt_id` dưới cache. Timeout/restart tiếp tục poll/download cùng
  job, không tự submit lại. Checkpoint hỏng/terminal error báo rõ thay vì phát job trùng.
  Crash giữa server nhận job và client lưu ID vẫn có thể lặp submit; không cam kết exactly-once.
- Kiểm container MP4 và ffprobe video stream/thời lượng trước đăng ký asset. Có timeout,
  trần response/download và chạy command bằng argv + JSON UTF-8 stdin qua sandbox hiện có.
- Cache clip chứa workflow identity, tham số hình ảnh, seed và thế hệ sửa. Sửa một cảnh
  không gọi lại TTS/clip ở cảnh đã khóa. Manifest sửa giữ đầy đủ `cinema` qua restart.
- Clip ngắn được FFmpeg lặp để phủ lời đọc, sau đó ghép/caption/nhạc/QC qua đường hiện có.
  Mỗi clip ghi provider/model/checksum/prompt_ref và license khai báo; mặc định `unknown`
  để kiểm tra quyền sử dụng. Mọi xuất bản vẫn qua gate hiện có.

## Giới hạn

Text bible và seed không khóa danh tính nhân vật. V1 chưa tự upload reference image,
cài model, chọn GPU, thiết kế rig 3D hoặc tạo khẩu hình. Workflow có reference cố định
được người vận hành chuẩn bị riêng. Cần model/nodes/workflow/GPU phù hợp để sinh clip thật;
UI chỉ báo cấu hình provider đã có, không xác nhận endpoint/weights hoạt động.

Test giả lập HTTP xác minh giao thức/lỗi/cache; test FFmpeg xác minh bản dựng thật từ
fixture cục bộ. Cả hai không thay thế eval chất lượng phim hoặc benchmark diffusion live.
Nguồn và license: [nghiên cứu](../RESEARCH-FILM-ANIMATION.md).
