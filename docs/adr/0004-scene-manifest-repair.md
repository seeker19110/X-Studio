# ADR-0004: Scene manifest bền vững, sửa từng cảnh, giới hạn 3 vòng

Trạng thái: Accepted · Ngày: 2026-09-02

## Bối cảnh
Sinh lại toàn bộ video vì một cảnh hỏng vừa tốn tiền vừa làm hỏng những cảnh đã đạt. Mô hình tham chiếu có "Scene Repair
Studio": manifest bền vững, sửa/đổi thứ tự/khoá/thay asset/sinh lại đúng một cảnh.

## Quyết định
1. `scene-manifests` là nguồn sự thật sản xuất: `scenes[{scene_id, order, narration, visual_prompt, duration_s, locked,
   asset_refs}]`, `version`, `voice`, `aspect`. `scene_id` ổn định qua các version.
2. Editor (model) chỉ ra quyết định `cut-lists`: `approve` (kèm `order`) hoặc `repair[{scene_id, action, reason,
   new_visual_prompt|new_narration|replacement_path}]`, action ∈ regenerate_audio | regenerate_image | regenerate_both |
   replace_asset | lock.
3. Renderer (code) `apply_cutlist`: chỉ sinh lại phần được nêu; cảnh `locked` không bao giờ sinh lại; asset người tải lên
   (`replace_asset`) được khoá và mang provenance `human-upload` để rights-checker kiểm; manifest mới `version+1` publish
   lại (actor renderer) rồi ghép bản nháp mới.
4. Giới hạn `MAX_REPAIR_ROUNDS = 3` (desk đếm): vượt → orchestrator ghi `repair.limit` và chốt bản hiện có; quality-reviewer
   quyết định pass/block trên bản đó. Không hạ chuẩn vì hết vòng.
5. Retention theo cảnh: `analytics.retention_drops` map mốc rơi vào `scene_id` theo `duration_s` tích luỹ, để insight
   trỏ đúng cảnh cần sửa ở video sau.

## Hệ quả
- Chi phí media tỉ lệ với số cảnh bị chạm, không với số vòng.
- Thứ tự cảnh và khoá được ghi trong manifest → replay dựng lại được trạng thái sản xuất.
- Chưa có tool để người tải asset qua giao diện; `replacement_path` hiện là đường dẫn file do người đặt.
