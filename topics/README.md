# Topics

Mỗi file JSON Schema (`schemas/<topic>.json`) mô tả envelope + payload của một topic; sinh từ pydantic model trong
`src/studio/events.py` (topic có model) hoặc viết tay (channel-briefs, trend-reports, research-dossiers, audience-comments).
Bus validate trước khi ghi. Owner ghi của `shared-context` theo namespace (nguồn sự thật: `NAMESPACE_OWNERS` trong
`events.py`; test `test_registry` kiểm khớp front matter):

| namespace | owner | nội dung |
|---|---|---|
| strategy | channel-strategist | pillar, nhịp, lý do chọn chủ đề, insight đã rút |
| research | trend-researcher | kho nguồn dùng lại, xu hướng có ngày |
| voice | script-writer | giọng kênh: xưng hô, nhịp câu, từ cấm |
| production | production-manager | tham chiếu manifest, quy ước cảnh |
| brand | thumbnail-designer | bảng màu, kiểu chữ, quy tắc thumbnail, giả thuyết đã thắng |
| seo | seo-optimizer | kho từ khoá, cụm đã dùng, mẫu mô tả |
| rights | rights-checker | sổ provenance, license đã ghi nhận (nhạc, footage, ảnh) |
| insights | analytics-analyst | insight lặp qua nhiều video, kết quả thí nghiệm |
| community | community-manager | FAQ, chủ đề lặp, bình luận cần chủ kênh xử lý |
| knowledge | supervisor | bài học, estimate vs actual theo format |

## Quy ước khác trong payload
- `video-briefs.estimate_tokens` bắt buộc trước dispatch; `budget_tokens ≥ estimate_tokens × 1.5`; `retry`/`hint` do desk điền khi làm lại.
- `video-briefs.risk_tags` ⊂ {health, finance, legal, minors, politics, music, footage, brand, person} → fact-checker/rights-checker siết theo skill.
- `review-results.source` ∈ fact | rights | quality; cả ba phải `pass` trước gate publish. `findings[].location` = claim_id / scene_id / trường metadata.
- `scene-manifests.version` tăng mỗi lần renderer áp cut-list; `scenes[].locked` cảnh không sinh lại; `asset_refs` do renderer điền.
- `media-assets.kind` ∈ scene_audio | scene_image | draft_video | final_video | thumbnail; `provenance` bắt buộc; `checksum` do code tính.
- `cut-lists.decision` repair chỉ được tối đa 3 vòng/video (`MAX_REPAIR_ROUNDS`).
- `metadata-packages` đi qua preflight (code); finding block → seo-optimizer sửa một lần.
- `publish-events.status` scheduled/published chỉ hợp lệ khi có quyết định gate trong audit-log; `rolled_back` → làm lại.
- `performance-snapshots` do người/adapter nạp; `variant_id` để code kiểm định A/B thumbnail.
- `reply-drafts.requires_human = true` → không bao giờ tự đăng dù gate approve.
