# Gợi ý nâng cấp — rà soát 2026-10-13

> Lấy từ đợt rà soát chéo 19 repo của chủ sở hữu (ma trận vệ sinh ở [seeker19110/stored, mục 23 §8.1](https://github.com/seeker19110/stored/blob/main/23-own-repos-lessons/README.md); gợi ý tích hợp ở §7b của cùng file). Số liệu đo bằng lệnh trên HEAD (đếm `uses:` ghim SHA 40 ký tự, khối `permissions:`, `dependabot.yml`, `.claude/settings.json`), không suy ra từ văn xuôi. Bản gốc ghi đề xuất tại `d7cb00d`; trạng thái sau main mới được cập nhật bên dưới. Mỗi mục còn lại cần kiểm lại bằng chứng trước khi làm.

## Số liệu lịch sử tại d7cb00d

| HEAD đọc | Action ghim SHA | Workflow thiếu `permissions:` cấp đầu | Dependabot | LICENSE · SECURITY | File test |
|---|---|---|---|---|---|
| `d7cb00d` (2026-10-13) | 3/3 | – | **không có** | không · không | 117 |

## Cập nhật sau main 93a290a (2026-10-16)

Main đã có `.github/dependabot.yml` cho `uv` và `github-actions`, `SECURITY.md`,
`LICENSE` Apache-2.0 và `AGENTS.md`. Cả ba đề xuất vệ sinh ban đầu đã được xử lý;
không đề xuất tạo lại các file này. Chưa xác minh Dependabot đã mở PR đầu tiên.
Bảng HEAD ở trên là số liệu lịch sử, không phải hiện trạng của bản tích hợp.

## Giữ nguyên (điểm mạnh)

- Bộ test hiện hữu, `verify.yml` ghim SHA, render cache SHA-256, MotionSpec / `qc.py`; số ca/file hiện tại được cập nhật trong README.

## Gợi ý tích hợp từ catalog `stored`

- Engine render thật: HyperFrames / Remotion (stored mục 22 §2); quality gate kiểu motionmaxxing / claude-motion (§5, §8); `data/videos.json` của awesome-opus5-5-videos làm tập prompt tham khảo (§8b); ManimGX cho video giải thích (§9c).

## Ghi chú chung

- Ghim action theo SHA bằng [pinact](https://github.com/suzuki-shunsuke/pinact) hoặc [ratchet](https://github.com/sethvargo/ratchet), giữ comment tag (`# v7.0.1`), rồi để Dependabot `github-actions` tự nâng SHA. Đây là cách projects-template, xboss và Sales-Hunter đang làm.
- Chuẩn tham chiếu là `seeker19110/projects-template`: 25/25 action ghim SHA, mọi workflow có `permissions:` cấp đầu, Dependabot cho npm, github-actions và pip.

> Cập nhật 14/10/2026: #1 xong, #2 xong (LICENSE Apache-2.0 theo repo nguồn `X-Agents`), #3 xong (`AGENTS.md`) — xem [nghiên cứu nâng cấp 14/10](2026-10-14-nghien-cuu-nang-cap.md) §4.
