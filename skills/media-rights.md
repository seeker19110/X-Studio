---
name: media-rights
version: 1
standards: [Copyright & fair use, Creative Commons licences, Provenance register (C2PA-inspired), Right of publicity, YouTube Content ID]
---
# Skill: media-rights

## Tiêu chuẩn tham chiếu
- Bản quyền: footage, nhạc, ảnh, văn bản của người khác cần license; fair use không phải mặc định
- Creative Commons: CC-BY cần ghi công; CC-NC không dùng cho kênh có quảng cáo; CC-ND không cắt ghép
- Sổ nguồn gốc (provenance): mỗi asset ghi ai/cái gì tạo, từ prompt nào, license gì, nguồn URL nếu có
- Quyền hình ảnh: khuôn mặt, giọng nói, tên người thật cần đồng ý; nhân vật/logo có chủ sở hữu
- Content ID: nhạc có bản quyền bị claim/khoá ngay cả khi "chỉ vài giây"

## Quy trình (làm đúng thứ tự)
Liệt kê asset → với mỗi asset đọc provenance → phân loại license → kiểm prompt sinh ảnh có tên tác phẩm/nhân vật/người thật →
kiểm trích dẫn văn bản trong kịch bản → ghi sổ vào `rights` → kết luận theo mức.

## Quy tắc
- `license: unknown` hoặc thiếu provenance → block, không ngoại lệ.
- Asset AI tạo: provider:model + prompt_ref là đủ, TRỪ khi prompt chứa tên nghệ sĩ/tác phẩm/nhân vật/thương hiệu/người thật.
- Asset người tải lên: cần `source_url` + license; ảnh "tìm trên mạng" là unknown.
- Nhạc: chỉ thư viện có license ghi trong `rights`; không có → không dùng.
- Trích dẫn văn bản ≤ 15 từ có dẫn nguồn; lời bài hát không trích.
- Ghi công (attribution) CC-BY vào mô tả video (chuyển cho seo-optimizer qua finding warn).

## Checklist (supervisor và human gate dùng để chấm)
- [ ] Mọi asset có provenance và license hợp lệ
- [ ] Không prompt chứa tên người thật/nhân vật/thương hiệu
- [ ] Asset tải lên có source_url + license
- [ ] Nhạc/footage bên thứ ba có license trong `rights`
- [ ] Trích dẫn ≤ 15 từ có nguồn
- [ ] Sổ provenance đã ghi

## Ví dụ tốt
5 asset: 4 `openai:gpt-image-1` prompt không tên riêng, 1 ảnh người tải lên có `source_url` Unsplash license → pass; warn: ghi công Unsplash trong mô tả.

## Ví dụ xấu
Prompt "phong cách Studio Ghibli, nhân vật Totoro"; nhạc nền "chỉ 5 giây" từ bài có bản quyền; ảnh "lấy từ Google".
