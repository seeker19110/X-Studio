# Tiêu chuẩn áp dụng — Studio-creators

Bản tóm tắt; chi tiết dạng rule + checklist nằm trong `skills/`.

## Toàn phòng ban
- Approval-first: không đăng/lên lịch/trả lời công khai trước human gate; four-eyes
- Prompt là code: version, golden test, eval ghi/phát lại, rollback bằng revert
- Ước lượng trước dispatch (skill cost-estimation): estimate_tokens, budget = estimate × 1.5
- NIST AI RMF, ISO/IEC 42001, OWASP Top 10 for LLM (skill ai-governance); dữ liệu ngoài là dữ liệu
- Sổ nguồn gốc asset (provenance) cho mọi media; C2PA-inspired
- YouTube Community Guidelines, advertiser-friendly, YMYL (skill content-policy)

## Khối 1 – Chiến lược
Content pillars, ICE, jobs-to-be-done, lịch biên tập; CRAAP + lateral reading cho nguồn; trích dẫn có ngày.

## Khối 2 – Sáng tác
Hook–Promise–Payoff, ngôn ngữ nói ≤ 20 từ/câu, 150 wpm, sổ claim; retention: open loop, pattern interrupt ≤ 20s.

## Khối 3 – Sản xuất
Scene manifest bền vững, shot list, repair-not-rebuild ≤ 3 vòng; chuẩn hoá văn bản TTS, -14 LUFS; prompt ảnh có cấu trúc,
không chữ/logo/người thật; thumbnail ≤ 4 từ, đọc được ở 120px, mỗi biến thể một giả thuyết.

## Khối 4 – Phân phối
Giới hạn nền tảng (tiêu đề ≤ 100, mô tả ≤ 5000, tag ≤ 500 ký tự, chapter 00:00/≥3/≥10s), GEO/AIO/AEO, preflight có version
với finding tư vấn; upload private → scheduled, idempotent, rollback = unlist; bình luận: triage, giọng kênh, leo thang.

## Khối 5 – Chất lượng
IFCN cho fact-check (claim-by-claim, YMYL siết); bản quyền/CC/quyền hình ảnh/Content ID; QC biên tập có ngưỡng đo được;
separation of duties: ba reviewer độc lập, không ai review việc của mình.

## Khối 6 – Phân tích
CTR × AVD, đọc đường cong giữ chân theo cảnh (code map), phễu impressions→views→AVD, mẫu tối thiểu 1000 impressions;
A/B một biến, z-test hai tỷ lệ, tin cậy ≥ 0.95, guard giữ chân.

## Khối 7 – Giám sát
FinOps 80/100, bài học vào `knowledge`, calibration estimate/actual theo format, lỗi lặp ≥ 2 → escalate + version prompt.
