---
name: content-policy
version: 1
standards: [YouTube Community Guidelines, Advertiser-friendly guidelines, YMYL, Minors safety, Misinformation policy]
---
# Skill: content-policy

## Tiêu chuẩn tham chiếu
- Nguyên tắc cộng đồng: không thù ghét, quấy rối, bạo lực, nội dung nguy hiểm, thông tin sai lệch y tế/bầu cử
- Thân thiện nhà quảng cáo: ngôn từ, chủ đề nhạy cảm, thumbnail gây sốc ảnh hưởng kiếm tiền
- YMYL: sức khoẻ, tài chính, pháp lý, an toàn — không lời khuyên cá nhân hoá, có tuyên bố miễn trừ, nguồn có thẩm quyền
- Trẻ em: không nội dung nhắm trẻ em nếu kênh không khai; không thu thập dữ liệu trẻ em
- Thông tin sai lệch: không claim y tế/bầu cử trái cơ quan có thẩm quyền

## Quy trình (làm đúng thứ tự)
Nhận diện chủ đề nhạy cảm từ brief `risk_tags` → áp quy tắc tương ứng → kiểm ngôn từ/hình ảnh → thêm miễn trừ khi YMYL →
ghi rủi ro còn lại cho gate.

## Quy tắc
- Chủ đề tài chính: không "đảm bảo", không lời khuyên mua/bán cụ thể; miễn trừ "không phải tư vấn tài chính".
- Chủ đề sức khoẻ: chỉ thông tin từ cơ quan y tế/nghiên cứu; không liều lượng cá nhân.
- Người thật: không suy đoán đời tư, không claim không nguồn.
- Không dùng thumbnail gây sốc/gợi dục/giả mạo.

## Checklist (supervisor và human gate dùng để chấm)
- [ ] risk_tags được xử lý bằng quy tắc tương ứng
- [ ] YMYL có miễn trừ và nguồn thẩm quyền
- [ ] Không ngôn từ/hình ảnh vi phạm
- [ ] Rủi ro còn lại ghi cho gate

## Ví dụ tốt
Brief risk_tags [finance]: kịch bản nói "theo dữ liệu X, lợi suất trung bình 5 năm là 7%; đây không phải tư vấn tài chính".

## Ví dụ xấu
"Mua ngay coin này, chắc chắn x10"; thumbnail giả bệnh viện.
