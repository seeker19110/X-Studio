# Gợi ý nâng cấp — rà soát 2026-10-13

> Lấy từ đợt rà soát chéo 19 repo của chủ sở hữu (ma trận vệ sinh ở [seeker19110/stored, mục 23 §8.1](https://github.com/seeker19110/stored/blob/main/23-own-repos-lessons/README.md); gợi ý tích hợp ở §7b của cùng file). Số liệu đo bằng lệnh trên HEAD (đếm `uses:` ghim SHA 40 ký tự, khối `permissions:`, `dependabot.yml`, `.claude/settings.json`), không suy ra từ văn xuôi. Đây là **đề xuất**, chưa sửa code. Mỗi mục kèm bằng chứng để kiểm lại trước khi làm; mục nào đã xử lý thì đánh dấu hoặc xoá.

## Hiện trạng đo được

| HEAD đọc | Action ghim SHA | Workflow thiếu `permissions:` cấp đầu | Dependabot | LICENSE · SECURITY | File test |
|---|---|---|---|---|---|
| `d7cb00d` (2026-10-13) | 3/3 | – | **không có** | không · không | 117 |

## Đề xuất (🔴 cao · 🟠 trung bình · 🟢 nhẹ)

| # | Mức | Đề xuất | Bằng chứng | Kiểm xong khi |
|---|---|---|---|---|
| 1 | ✅ | ~~Thêm `.github/dependabot.yml` (uv, github-actions)~~ — đã có từ `1e9ca1b` (#13) | Không có Dependabot, dù đã có `uv.lock` và 3/3 action ghim SHA | Dependabot mở PR đầu tiên |
| 2 | 🟢 | ~~Thêm `SECURITY.md`~~ (đã có từ `1e9ca1b`), **còn** LICENSE hoặc dòng "giữ mọi quyền" | Repo không có LICENSE và không có SECURITY.md | Có file |
| 3 | 🟢 | Thêm `AGENTS.md` (tóm tắt luật cho Codex / Gemini), như X-Agents đã làm | Chỉ có CLAUDE.md (52 dòng) | Có file |

## Giữ nguyên (điểm mạnh)

- 115 file test, `verify.yml` ghim SHA, render cache SHA-256, MotionSpec / `qc.py`.

## Gợi ý tích hợp từ catalog `stored`

- Engine render thật: HyperFrames / Remotion (stored mục 22 §2); quality gate kiểu motionmaxxing / claude-motion (§5, §8); `data/videos.json` của awesome-opus5-5-videos làm tập prompt tham khảo (§8b); ManimGX cho video giải thích (§9c).

## Ghi chú chung

- Ghim action theo SHA bằng [pinact](https://github.com/suzuki-shunsuke/pinact) hoặc [ratchet](https://github.com/sethvargo/ratchet), giữ comment tag (`# v7.0.1`), rồi để Dependabot `github-actions` tự nâng SHA. Đây là cách projects-template, xboss và Sales-Hunter đang làm.
- Chuẩn tham chiếu là `seeker19110/projects-template`: 25/25 action ghim SHA, mọi workflow có `permissions:` cấp đầu, Dependabot cho npm, github-actions và pip.

> Cập nhật 14/10/2026: #1 xong, #2 còn LICENSE, #3 còn mở — xem [nghiên cứu nâng cấp 14/10](2026-10-14-nghien-cuu-nang-cap.md) §4.
