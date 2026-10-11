# Nghiên cứu tạo phim và hoạt hình cho X-Studio

Khảo sát ngày **11/10/2026** từ README, giấy phép và mã nguồn chính thức. Tài liệu này ghi cách chọn kiến trúc cho phiên bản đầu; chất lượng, VRAM và thời gian mà tác giả mô hình công bố không phải kết quả benchmark trên máy chạy X-Studio. Không tải weights, cài ComfyUI, custom node hoặc Blender trong bước nghiên cứu.

## Quyết định cho v1

X-Studio quản lý **phim → storyboard → từng shot → clip → bản dựng**. ComfyUI là dịch vụ tạo clip có workflow được người vận hành cấu hình; FFmpeg tiếp tục xử lý dựng và xuất bản. Phân chia này tận dụng `scene_video`, kho asset có provenance, cache và bộ dựng từng cảnh hiện có trong `renderer.py`, `media.py`, `production.py`, `render_cache.py`, `long_render.py`.

Các trường v1 đã nối vào request và brief qua `film.py/CinemaSpec`: `production_type` là `explainer`, `film` hoặc `animation`; `visual_mode` là `images` hoặc `generated_video`. `visual_style`, `logline`, `character_bible`, `negative_prompt` và `seed` lưu định hướng cho dự án. `SceneManifest.cinema` giữ bản sao định hướng để restart và sửa cảnh vẫn dùng cùng cấu hình. Các shot tiếp tục dùng `Scene.visual_prompt`, `narration`, `duration_s`, `order`, `locked`, `asset_refs`; mô tả hành động/camera nằm trong visual prompt, chưa có schema camera hoặc registry nhân vật riêng.

`media.yaml` có kênh `scene_video` độc lập với `video.provider=ffmpeg`: `none` tắt tạo clip; `fake` phục vụ test bằng JSON placeholder, không tạo MP4 xem được; `comfyui` chạy API workflow; `command` chạy argv cấu hình và nhận JSON qua stdin. `video_gen.py` và `renderer.py` nối clip vào `scene_video`, cache và provenance hiện có. V1 trao đổi **MP4**; không nhận WebM/MOV từ output ComfyUI.

Một phim dài được tạo từ nhiều shot ngắn. Mô tả nhân vật dùng chung bổ sung vào prompt, còn seed từng shot được tạo ổn định từ seed dự án và scene ID. Các kiểu hình ảnh điện ảnh, 2D, 3D, anime là chỉ dẫn cho workflow/model. Chọn kiểu không đồng nghĩa máy đã cài engine đó. Chế độ `images` giữ pipeline ảnh có chuyển động để xem trước nhịp và lời kể. Không có engine animatic/rig nhân vật mới, upload reference, tự tải diffusion model hoặc lip-sync trong v1.

## Repo được đối chiếu

| Repo | Học hỏi và khả năng liên quan | Vận hành, giấy phép | Áp dụng vào X-Studio |
|---|---|---|---|
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | Graph JSON tái dùng; queue và API; hỗ trợ nhiều model video | Core **GPL-3.0**; custom node và weights có license riêng. Model, VAE và text encoder phải được cài theo workflow | Adapter HTTP tách khỏi Python runtime X-Studio; workflow và node/input bindings cấu hình được; không nhúng source GPL hoặc đóng đinh một graph |
| [Wan2.1](https://github.com/Wan-Video/Wan2.1) | T2V và I2V; phiên bản 1.3B phù hợp khảo sát máy nhỏ hơn | README công bố T2V-1.3B cần **8,19 GB VRAM**, khoảng 4 phút cho clip 5 giây 480p trên RTX 4090 khi chưa tối ưu. Repo và model công bố **Apache-2.0**. [Giấy phép](https://github.com/Wan-Video/Wan2.1/blob/main/LICENSE.txt) | Một lựa chọn workflow nhỏ để thử tạo clip; không biến con số VRAM thành cam kết tốc độ hay khả năng chạy của máy hiện tại |
| [Wan2.2](https://github.com/Wan-Video/Wan2.2) | TI2V, I2V; có nhánh speech-to-video và animation | Lệnh TI2V-5B 720p có offload trong README yêu cầu **ít nhất 24 GB VRAM**; kích thước mẫu 1280×704. Model và repo công bố **Apache-2.0**. [Giấy phép](https://github.com/Wan-Video/Wan2.2/blob/main/LICENSE.txt) | Hỗ trợ qua workflow ComfyUI do người vận hành chọn; speech-to-video/lip-sync là năng lực riêng cần workflow phù hợp |
| [HunyuanVideo](https://github.com/Tencent-Hunyuan/HunyuanVideo), [HunyuanVideo-1.5](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5) | Tạo video và phiên bản 1.5 nhẹ hơn; tham khảo cách quản lý inference/offload | README 1.5: Linux, Python ≥3.10, NVIDIA CUDA; tối thiểu **14 GB GPU memory với offload**. [License 1.5](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5/blob/main/LICENSE) là Tencent Community License, bao gồm code và weights; có giới hạn khu vực EU/UK/Hàn Quốc | Backend tùy chọn; ghi đúng model, phiên bản và nguồn license. Không gọi mọi Hunyuan model là Apache/MIT |
| [LTX-Video](https://github.com/Lightricks/LTX-Video), [LTX-2](https://github.com/Lightricks/LTX-2) | LTX-Video cũ chuyển hướng sang LTX-2; repo mới có audio-video, keyframe, LoRA và retake | LTX-Video cũ dùng Apache-2.0. LTX-2 main hiện hướng tới LTX-2.5; bộ tải nhanh khoảng **66 GiB**. [LICENSE](https://github.com/Lightricks/LTX-2/blob/main/LICENSE) phân biệt bản cũ/mới; [LICENSE-2_x](https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x) ngày 11/08/2026 bao gồm code và weights, yêu cầu paid license cho sử dụng thương mại của tổ chức doanh thu ≥10 triệu USD/năm | Workflow tùy chọn cho cảnh có âm thanh/keyframe. Không dùng license LTX-Video cũ để mô tả LTX-2. Không tự động tải model |
| [AnimateDiff](https://github.com/guoyww/AnimateDiff) | Motion module cho model ảnh; MotionLoRA; SparseCtrl từ ảnh/phác thảo. Main dành cho SD1.5, nhánh SDXL riêng | Code repo **Apache-2.0** theo [LICENSE.txt](https://github.com/guoyww/AnimateDiff/blob/main/LICENSE.txt). Base model, checkpoint cộng đồng, LoRA và các weights tải ngoài cần kiểm tra license theo nguồn | Tách style khỏi chuyển động; workflow/operator có thể chuẩn bị reference trước. V1 không upload reference. Không suy ra license weights từ license code |
| [MoviePy](https://github.com/Zulko/moviepy) | Clip composition, cắt/ghép, title và hiệu ứng bằng Python | **MIT**; v2 có breaking changes. README nêu xử lý frame qua NumPy có thể chậm hơn FFmpeg trực tiếp | Học mô hình clip/composition; tiếp tục dùng FFmpeg hiện có để giảm dependency và tái dùng bounded rendering |
| [Blender](https://github.com/blender/blender) | Cơ sở cho hoạt hình 3D có rig, camera và scene do tác giả kiểm soát | [COPYING](https://github.com/blender/blender/blob/main/COPYING) xác định **GNU GPL**; license cụ thể theo thành phần. Asset/rig/texture cần quyền sử dụng riêng | Nhập clip Blender vào thư viện video hiện có. Engine dựng 3D/rig và `.blend` automation chưa thuộc adapter ComfyUI v1 |
| [Storyboarder](https://github.com/wonderunit/storyboarder), [board.js](https://github.com/wonderunit/storyboarder/blob/master/src/js/models/board.js) | Board có duration, reference và playback animatic; screenplay Fountain | Không xác định được SPDX license ở root/package của snapshot đã đọc; README trỏ tới bài viết về license. Không đủ cơ sở gắn nhãn MIT | Học cách tổ chức shot và xem trước nhịp phim; tự viết schema/UI, không sao chép source |

## Mẫu API ComfyUI được áp dụng

[Ví dụ API chính thức](https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/websockets_api_example.py) gửi graph bằng `POST /prompt`, lấy `prompt_id`, đọc `GET /history/{prompt_id}` và tải asset từ `GET /view` với `filename`, `subfolder`, `type`. [server.py](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py) còn cung cấp `/upload/image`, `/system_stats`, `/object_info`.

Workflow phải được export ở **API format**, dạng object có node ID và `class_type`/`inputs`; JSON giao diện chứa `nodes`/`links` không cùng hợp đồng. `scene_video.workflow_path`, `output_node` và `bindings` chỉ ra graph, node lưu clip và input nhận prompt. Các semantic binding v1: **`prompt` và `seed` bắt buộc**; `negative_prompt`, `width`, `height`, `fps`, `duration_s`, `frames` tùy workflow. V1 không bind reference upload hay audio. Không đoán node ID theo tên model. Workflow phải lưu **MP4**.

Native [SaveVideo](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_video.py) dùng `PreviewVideo`; [PreviewVideo.as_dict](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_api/latest/_ui.py) ghi danh sách file vào key **`images`**, dù file là video. Custom node có thể dùng `gifs` hoặc `videos`. Adapter v1 kiểm cả ba key của output node cấu hình và chọn extension MP4.

Đề xuất vận hành v1: một job cho mỗi shot, poll history có timeout, báo lỗi execution hoặc node validation rõ ràng, tải file có giới hạn kích thước và ghi atomically. Ghi model/workflow hash, seed, prompt và checksum vào cache/provenance. Dịch vụ là endpoint cấu hình rõ ràng; workflow tải từ Internet/custom nodes được người vận hành kiểm tra trước khi chạy. Đây là thiết kế suy ra cho X-Studio từ hợp đồng API, không phải khả năng mặc định của model.

## Tính liên tục của nhân vật

**Character bible** v1 là một chuỗi mô tả ổn định dùng chung cho prompt của các shot: hình dáng, màu sắc, trang phục, vật dụng, phong cách và điểm cần giữ. **Seed** dự án và seed suy ra cho từng scene được dùng để tái tạo lần chạy cùng điều kiện. Yêu cầu regenerate dùng revision của cut để tạo seed mới; retry cùng yêu cầu giữ seed đó. **Reference** là hướng nâng cấp cho I2V, reference conditioning hoặc LoRA; v1 chưa nhận hay upload reference từ dự án.

Các biện pháp này giúp quản lý continuity nhưng không bảo đảm danh tính chỉ bằng prompt/seed. V1 cần cho xem lại từng shot, thay clip lệch thiết kế và khóa shot đã chấp nhận. Muốn giữ nhân vật chắc hơn cần workflow I2V/reference hoặc model/LoRA được chuẩn bị riêng; cần thử trên dữ liệu phim thực trước khi hứa chất lượng.

Lời kể v1 dùng một giọng TTS, dựng theo thời lượng đo từ audio. Thời lượng yêu cầu tạo clip được giới hạn bởi `scene_video.duration_s` và thời lượng cảnh; FFmpeg lặp footage để chạy hết lời kể nếu cần. TTS phủ lên clip không tạo khẩu hình. Lip-sync/speech-to-video chưa được nối vào v1; lặp cảnh không tạo chuyển động mới.

## Điều kiện kiểm thử và triển khai

- Request/brief/manifest round-trip giữ `CinemaSpec`, thứ tự shot, duration và seed qua restart và repair.
- Chế độ `images` vẫn xuất MP4 thật bằng FFmpeg; `fake` phải thể hiện là dữ liệu mô phỏng.
- HTTP fixture kiểm API-format, prompt bindings, polling success/error/timeout, và video output dưới `images`/`gifs`/`videos`.
- Cấu hình workflow phải hợp lệ trước khi gửi job. Kiểm tra endpoint/node class và benchmark weights/GPU là các bước vận hành riêng, không suy ra chỉ từ việc adapter được tạo thành công.
- Smoke test với ComfyUI/model thật chỉ hoàn tất khi dịch vụ, model và workflow đã được cài. Test giả lập API không được ghi thành benchmark tạo sinh.

Trước khi bật một workflow production, lưu phiên bản ComfyUI, custom node, model và link license tương ứng. Chọn backend theo phần cứng đo được và bài thử shot đại diện. Bản nghiên cứu này ưu tiên kiến trúc thay backend được; nó không tuyên bố đã cài hay chạy các mô hình trong bảng.
