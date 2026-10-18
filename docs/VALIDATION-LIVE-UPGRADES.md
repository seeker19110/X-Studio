# Chạy thử thực tế sau bốn đợt nâng cấp

Ngày cập nhật: 2026-10-18. Xem [hướng dẫn vận hành](OPERATIONS-UPGRADES.md).
Phạm vi người vận hành đã chọn trước đó: chạy các phần còn lại trước YouTube; kiểm GPU/công cụ hiện có,
chưa dựng pipeline sinh video AI bằng GPU. Lượt rà soát tiếng bên dưới dùng ASR chạy CPU trong cache thử riêng.

## Thử video dài hơn và Shorts bằng model thật (2026-10-18)

Kết quả mới tại `output/real-validation/long-default-20261018-201730/`, chủ đề **Sắp xếp tư liệu
trước khi dựng video**. Dùng cấu hình đang cài: backend text/hình thật, VieNeu v3 Turbo/Ngọc Huyền,
FFmpeg thật, bố cục editorial và phụ đề đốt vào hình. Chỉ tách thư mục output/upload cho bài thử.
Đây là lượt có phát hiện và sửa lỗi, có can thiệp được audit; không mô tả là chạy tự động trơn tru ngay lần đầu.

| Hạng mục | Bản giao để xem cục bộ |
|---|---|
| Video chính | `video-chinh-1080p.mp4`; `P3ee9f05119e04c70a40b83bb-V1`, manifest v3 / script v2; **82,21 giây**, 1920×1080/25 fps, **10 cảnh / 324 từ / 37 cue** |
| Shorts cuối | `shorts-1080x1920.mp4`; `SHORT-25b7edf0940a9f8f8835918d`, v1; **30,85 giây**, 1080×1920/25 fps, **4 cảnh / 126 từ / 19 cue** |
| Âm thanh | Cả hai **-14,0 LUFS / -1,0 dBTP**; H.264/AAC, 48 kHz stereo; không có finding QC |
| Kiểm media | Giải mã toàn bộ bằng FFmpeg `-xerror`; không thấy đoạn đen hoặc im lặng dài; token SRT khớp narration, không tràn/chồng, không cue một từ |
| Hình cuối | Codex xem khung giữa của **56 cue**, chia 5 trang ngang và 4 trang dọc, cùng ba thumbnail; các chữ và hình nằm trong khung |
| Review thật | Hai bản **fact pass / quality pass / rights pass**; quality và rights mới nhất của main không còn finding |
| Source cuối | `make verify`: **1.179 Studio + 273 Gateway + 478 core = 1.930 test**; 100% dòng từng package, core 100% nhánh; Ruff/mypy và strict replay **28/28** đạt |
| Windows | Runtime Python **3.12.14**: 20 ca wrapper/command timing và 5 regression mới đạt; không gọi đây là toàn bộ suite Windows |
| Ngân sách | Main **1.080.243 / 2.000.000**, Shorts cuối **250.704 / 2.000.000** token; Shorts sơ bộ **285.771** giữ riêng trong lịch sử |
| Ledger | **1.740.200** text token toàn root, gồm history; **24** generation media thành công (10 TTS + 14 ảnh), **2** thất bại, **24** cache hit; **26 khoản chưa có đơn giá**, không coi USD 0 là miễn phí |
| Dừng | Worker đóng và tạm dừng, queue/deferred **0/0**; **0 publish-event / 0 upload-session**, không lên lịch, không ký duyệt người |

Các sửa lỗi đưa vào mặc định đều có regression đỏ trước sửa và được kiểm lại với dữ liệu thật:

- TTS thử tối đa ba seed liên tiếp, chỉ nhận WAV có lời nhận dạng và timing đạt; giữ seed/SHA từng candidate.
  Mẫu “để chỉnh” được giữ như regression thật: seed đầu không đạt, seed sau đạt, không thay transcript.
- Chia scene planning theo chương kiểm cả checkpoint và proposal mới với đúng lời đọc của riêng chương.
  Model từng trả lại cả kịch bản ở chương cuối, tạo 18 cảnh thay vì 10; bản mới có đúng 10 phần, không lặp mở đầu.
  Proposal sai vẫn ghi token trước khi chặn, không âm thầm cắt nó thành kết quả đạt.
- Nhóm phụ đề kiểm việc xuống hai dòng thực tế. Shorts sơ bộ từng có từ “bản” lóe **17 ms**;
  bản cuối chia lại từ mốc đo, không còn cue một từ. Đã kiểm cả 10 timing thật ở độ rộng 38 và 26.
- Ghi công hoàn tất đoạn tiền tố nguyên văn hoặc bỏ đoạn tiền tố đã có notice đầy đủ ở nơi khác.
  Main không còn lặp đoạn giọng; ba chapter lấy từ timeline thật tại **00:00 / 00:31 / 00:48**.

Kịch bản ban đầu có những cụm giọng khó phân biệt. Model thật viết lại sáu phần cuối; bốn phần đầu
đã đạt được giữ nguyên để dùng cache. Fact review lại đạt; planner thật trả proposal hoàn chỉnh,
code nối lại scene ID bền vững theo thứ tự. Sau draft, editor thật yêu cầu làm sáng riêng hình cảnh
“Nghe thử tiếng”: **1 vòng sửa / tối đa 3**, giữ 10 audio và chín ảnh còn lại. Sửa metadata không đổi MP4 main.
Một phản hồi rights của Shorts sơ bộ sai JSON được gọi lại đúng reviewer và hồ sơ; kết quả thật đạt.
Không cung cấp verdict dựng sẵn, không duyệt escalation hay publish để vượt lỗi.

ASR là bằng chứng bổ sung có bất đồng, **không phải chứng nhận đã nghe của người**. Nhận toàn file, không
gợi transcript: small/large-v3-turbo có khoảng cách từ main **12 / 1**, Shorts cuối **3 / 3**. Nhận từng
cảnh từ chính MP4 được giữ riêng; cảnh hộp ảnh nhận đúng khi tách riêng dù nhận toàn file nhầm “bàn/bản”.
Đối chiếu các lượt đọc thật có bằng chứng cho đủ **324/324** và **126/126** token, nhưng đây là đối chiếu
giữa nhiều lượt, không phải một transcript toàn file có lỗi bằng không. Một thử nghiệm thêm với VAD và
cấu hình Whisper chuẩn tự nhận thêm câu ngoài nguồn ở cuối main; giữ như lượt ASR không đạt, không thêm
câu đó vào video hoặc sửa transcript thành kịch bản. Các JSON/raw/log đều còn để đối chiếu.

Shorts là bốn cảnh đầu khép lại bằng quy tắc, giữ chỉ số cảnh 1–4/10 từ video nguồn. Quality còn một
**warn không có CTA hành động**; fact còn **nit C3 được kế thừa nhưng không dùng trong narration**.
Không thêm lời đọc vào đoạn trích chỉ để xóa cảnh báo. Phần nguồn chỉ gán MASV cho gom theo dự án/đặt tên;
tách nguồn khỏi bản làm việc được nói rõ là gợi ý của kênh, không gắn MASV lên hình.

`result.json` chọn main và Shorts cuối; `state-full.json` cùng SQLite giữ cả Shorts sơ bộ
`SHORT-004589ddf2ac9fadbb841432`. Lượt thử đầu
`output/real-validation/long-default-20261018-174606/` vẫn giữ escalation chờ xử lý, không giả duyệt.
Một lượt full test gặp danh mục agent rỗng trên ổ Windows; recheck đơn và full suite sau đó đạt.
Nguyên nhân nền của lần đọc thư mục đó chưa được xác lập, không gọi lượt thất bại là đạt.

`verification-summary.json` giữ QC/hash và các nhận dạng; `visual-inspection.json` giữ hash frame/video đã xem.
`runtime-validation-evidence.json` nối log TDD, full verify và Windows. Hai SRT, metadata, bìa dọc,
notice/font/license/card và `ho-so-quyen-giong-doc.json` nằm cùng gói. Quyền giọng dựa trên grant đã lưu
của nhà phát hành; phạm vi vận hành vẫn chỉ xem cục bộ, `inspection.human_required` tiếp tục đúng.

## Hoàn thiện quy trình mặc định và xuất cặp video mới (2026-10-18)

Thư mục kết quả: `output/real-validation/default-factory-20261018-083322/`.
Đây là bài thử mới từ quy trình mặc định: hình minh họa mới do backend model thật tạo, bốn take
VieNeu/Ngọc Huyền mới, encode FFmpeg thật và review bằng backend text đã cấu hình. Tên model backend
thực báo được giữ trong audit; các lượt review tiếp tục ngày 18/10 báo `gpt-5.6-sol`.
Không dùng verdict dựng sẵn hoặc thay kết quả nhận dạng bằng kịch bản.

| Hạng mục | Bản xem cuối |
|---|---|
| Video chính | `video-chinh-1080p.mp4`; `P90a1f6f2f24c43608030c50b-V1`, manifest **v1**, script **v2**, 1920×1080/25 fps, **17,14 giây**, 8 cue |
| Shorts | `shorts-1080x1920.mp4`; `SHORT-73ea57f516ff5ddb72843280`, v1, 1080×1920/25 fps, **16,84 giây**, 10 cue |
| Lời đọc | 66 từ, giữ “Hình cần dễ nhìn”; dùng giọng VieNeu được nhà phát hành cho phép đầu ra thương mại, không dùng Piper |
| Âm lượng | Main **-13,9 LUFS / -1,5 dBTP**; Shorts **-13,8 LUFS / -1,4 dBTP** |
| ASR độc lập | Whisper small và large-v3-turbo, CPU int8, không gợi transcript: **66/66 từ, khoảng cách từ 0**, trên cả hai MP4 cuối |
| Hình/phụ đề | Khung giữa từng cue và ba thumbnail được Codex xem; chữ gọn trong khung, không cue một từ, không tràn/chồng; kiểm đủ token phụ đề với narration |
| Sửa cảnh | **0 vòng** trên dự án mới; bốn take và video chính giữ byte trong lúc sửa nguồn dẫn/metadata; không tiêu thêm vòng dựng vì lỗi hồ sơ |
| Bản xem kèm | Hai SRT UTF-8, metadata JSON, `bia-shorts-9x16.jpg`, notice giọng và bộ license/card được chép cùng thư mục |
| Windows | **58/58** ca qua runtime Windows Python **3.12.14** của bài thử, có FFmpeg thật và chữ Việt |
| Source cuối | `make verify` đạt: **1.171 Studio + 273 Gateway + 478 core = 1.922 test**; 100% dòng riêng từng package, core 100% nhánh; Ruff/mypy và replay strict **28/28** đạt |
| Review thật | Hai bản đều **fact pass / quality pass / rights pass**; rights nhận đúng metadata riêng của từng video |
| Ngân sách | Main **970.857 / 1.500.000**, Shorts cuối **324.874 / 1.500.000** token; retry 0; usage tính cả cache backend báo |
| Ledger | **1.683.065** text token toàn bài thử, gồm history; 11 generation media = 4 TTS + 7 ảnh, không lỗi media; **11 khoản chưa có đơn giá**, không gọi USD 0 là miễn phí |
| Kết thúc | Worker đóng, queue/deferred **0/0**; **0 publish-event / 0 upload-session**, gate publish chờ chủ xưởng |

`result.json` giữ hai dự án được giao; `state-full.json` và SQLite giữ cả bản Shorts sơ bộ
`SHORT-9dc8ed4335048d949bc7cea0`. Shorts cuối dùng source đã sửa provenance; MP4 có SHA giống bản sơ bộ
vì thay hồ sơ quyền/credit không đổi âm thanh hay hình. Không xóa lịch sử hoặc gán quyền mới cho take Piper cũ.
Video chính SHA-256: `49e6b3693c470f6471dda25c00c9a49f7dbb854b30fa777660a782f9ee29166f`.
Shorts cuối: `d4529cdd4a2d9e24aa7d51aab955ae712aaaadf8f37120f3ced16ba2fe8f9dfe`.
`verification-summary.json` giữ số đo QC/decode và bốn nhận dạng độc lập; `visual-inspection.json`
giữ quan sát Codex trên contact sheet của chính các file cuối, cùng hash ảnh/video.

Các lỗi đã sửa trong mặc định, với regression đỏ trước sửa:

- Sửa lời giữ heading/claim_ids; chỉ nâng `script_version` khi narration của toàn bộ cảnh khớp kịch bản.
- Draft/final giữ quyền của các thành phần thực dùng. Gói quyền phân biệt bản ghi hiện tại/lịch sử bằng
  path, kind, scene và variant; các vai trò cùng dùng một clip không bị đánh nhầm là lịch sử.
- SRT Shorts giữ provenance của captions nguồn, không nhận license giọng từ MP4. Credit ghép nhiều notice
  chỉ bổ sung phần thiếu; không lặp đoạn giọng vì còn thiếu credit font.
- SEO/production nhận giọng cấu hình thật; rights-checker nhận metadata riêng của đúng video đang kiểm.
  Timing sidecar có audit producer và SHA của audio/JSON.
- Thumbnail tối đa bốn từ, không dấu câu hoặc chép lại cụm tiêu đề. Ba bản hiện tại: “XEM ĐÚNG FILE”,
  “LỖI Ở ĐÂU”, “ĐỪNG VỘI DUYỆT”; kích thước 1280×720 và hash của file khớp bản ghi mới nhất.
- Preflight không khuyên thêm chapter khi thời lượng đo dưới 30 giây; vẫn kiểm chapter nếu đã khai.
- Video đủ tư liệu và ba review đạt đang chờ chủ xưởng không bị coi là sản xuất treo sau sáu giờ.
  Replay chỉ bỏ pause của thông báo idle lỗi thời khi gói hiện tại vẫn đạt; lỗi review/budget vẫn giữ chặn.

Đã gặp chính thông báo idle cũ khi tiếp tục bài thử. Event gốc được giữ; gate thông báo sai của video chính
được invalidate cùng bằng chứng tại `obsolete-idle-hold-P90a1f6f2f24c43608030c50b-V1.json`, không duyệt
escalation bằng chữ ký người hoặc mở gate publish. Thông báo và dự án sơ bộ vẫn giữ trong lịch sử.
Thiết kế và cách replay được ghi tại [ADR-0019](adr/0019-current-export-traceability.md).

Nguồn C3 đã đổi sang [hướng dẫn phụ đề của University of Arizona](https://accessibility.arizona.edu/documents-media/captioning),
đúng phần tên riêng và con số. C2 ghi rõ việc vừa nghe vừa đọc là suy luận biên tập từ yêu cầu đồng bộ;
các chỉ dẫn xem hình/ghi lỗi không còn được trình bày như chuẩn ngành có nguồn. Script v2 giữ nguyên narration;
`source-correction-applied.json` ghi liên kết kịch bản và hash không đổi. Review fact thật kiểm ba claim;
research/blackboard cũ vẫn là lịch sử, không âm thầm sửa nguồn cũ.

Runtime VieNeu dùng manifest ổn định `output/runtime/vieneu/runtime-artifacts.json` và dependency lock;
đã bổ sung notice/SHA Apache-2.0 của sea-g2p 0.10.0 vào [hồ sơ quyền](rights/vieneu-commercial-voice-evidence.json).
Quyền model/preset/đầu ra dựa vào tuyên bố cụ thể của nhà phát hành; không mô tả như kiểm toán độc lập
corpus huấn luyện. Hồ sơ vận hành mới chỉ cho phép xem cục bộ và không chứa phê duyệt phát hành.

Lượt kiểm môi trường đã giữ cả thất bại: một lần ASR đọc thiếu weights trên ổ C dù hash khớp; cache được
chép và kiểm hash thành file thường tại `/home/liend/.cache/xstudio-asr`, không tải/đổi model. Một lần VAD
nhận Shorts chỉ 12 từ cũng được giữ. Lượt cuối nhận toàn file bằng cả hai model, tắt VAD, bù khoảng trống
timestamp khi giải mã PCM và kiểm thời lượng trước nhận: bốn kết quả đều khớp 66 từ, không sửa MP4 hoặc
gợi chữ. `final-speech-validation.json` và log `completion-speech-wholefile.log` là bằng chứng cuối;
các kết quả không đạt trước đó vẫn còn để đối chiếu.

Các log TDD và kiểm tra nằm trong `output/real-validation/logs/`: `completion-own-metadata-red.log`,
`completion-idle-red.log`, `completion-idle-green.log`, `completion-last-green.log`,
`completion-verify.log`, `completion-windows-312.log`, `completion-resume.log`, `completion-independent.log`.
Lượt trước có lỗi thiếu bộ nhớ/đọc danh mục và WSL interop; không tính các lần đó là đạt. Lần probe Python
3.14 dùng nhầm dependency native 3.12 cũng bị loại; kết quả Windows nói trên dùng đúng runtime 3.12.

ASR, QC và Codex xem frame là bằng chứng kỹ thuật bổ sung; `inspection.human_required` vẫn đúng cho bước
chủ xưởng tự xem/nghe. Không tạo phê duyệt người, không upload hoặc lên lịch YouTube. Gate publish vẫn chờ.

## Thay Piper bằng giọng có quyền thương mại và xuất lại (2026-10-17)

Đã thay toàn bộ bốn take Piper bằng **VieNeu-TTS v3 Turbo, preset Ngọc Huyền** (nữ, miền Bắc,
phong cách tự nhiên). Cấu hình `media.yaml` hiện tại dùng command provider mới, license `licensed`;
runtime riêng ghim `vieneu==3.8.3`, chạy ONNX/CPU. Không nhân bản từ mẫu giọng ngoài.

[Model card của tác giả, revision đã ghim](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo/blob/61b85e3d937fbbacb387714180e8182823512523/README.md)
xác nhận Apache-2.0 cho model và preset đi kèm, cho phép rõ âm thanh đầu ra dùng trong nội dung thương mại/
kiếm tiền, và xác nhận sự đồng ý của người cung cấp giọng. Đây là căn cứ khác với nhãn dataset của Piper.
Hồ sơ tại [bằng chứng quyền VieNeu](rights/vieneu-commercial-voice-evidence.json) giữ revision, SHA card,
preset và manifest runtime. Notice Apache và card được lưu cùng source và gói xem. Quyền này dựa trên
tuyên bố cấp phép cụ thể của nhà phát hành; không mô tả như một cuộc kiểm toán độc lập toàn bộ dữ liệu huấn luyện.
Quyền giọng không thay phê duyệt phát hành toàn bộ video của chủ xưởng.

“Hình cần rõ” được đổi thành **“Hình cần dễ nhìn”**. Phần nghe/chữ dùng lại thuật ngữ chính xác
“Nghe lời đọc và xem phụ đề cùng lúc”, câu kết là “Khi hình ảnh, lời đọc và phụ đề đều đúng thì mới duyệt”.
Giữ nguyên bốn ảnh; từ manifest v3 sang v4 chỉ thay bốn audio, dựng lại phụ đề và tạo Shorts mới.
Không ghi đè hai MP4 của lượt Piper trước; đã đối chiếu lại SHA của cả hai file cũ.

| Hạng mục | Kết quả bản mới |
|---|---|
| Thư mục xem | `output/real-validation/commercial-voice-20261017-064838/` |
| Video chính | `video-chinh-1080p.mp4`, nguồn `Pb37ab606a82a432780bac795-V1` **v4; 1920×1080, 17,14 giây**, 8 cue |
| Shorts | `shorts-1080x1920.mp4`, nguồn `SHORT-ab04587292557013ae1db57c` **v1; 1080×1920, 16,84 giây**, 10 cue |
| Model thật | Piper được thay bằng VieNeu; fact/SEO/editor/quality/rights vẫn chạy Claude CLI thật |
| Review | Cả hai **fact pass / quality pass / rights pass**, không giả verdict hoặc phê duyệt người |
| Nhận dạng file cuối | Whisper small **và** large-v3-turbo, không gợi transcript: **66/66 từ trên cả hai MP4**, khoảng cách từ bằng 0 |
| Hình/phụ đề | Codex đã xem frame giữa từng cue của hai file sau encode; chữ nằm gọn, bốn cảnh đúng thứ tự; không cue một từ/tràn/chồng |
| File/QC | Decode toàn file đạt; H.264/AAC stereo 48 kHz, 25 fps; không finding QC, black/silence span vượt ngưỡng |
| Âm lượng | Main **-13,9 LUFS / -1,5 dBTP**; Shorts **-13,8 LUFS / -1,4 dBTP** |
| Tư liệu | **4 take TTS sản xuất mới**, 0 ảnh mới; ảnh nguồn và thumbnail A/B giữ SHA; các probe chẩn đoán cục bộ được ghi riêng |
| Ngân sách | Main lũy kế **1.154.159 / 1.500.000**, Shorts mới **162.058 / 1.500.000** token; retry 0 |
| Ledger text | Thêm **382.571**, lũy kế sau phục hồi **1.823.984** token; đơn giá media chưa cấu hình, số 0 ước tính không nghĩa miễn phí |
| Source | **1.157 Studio + 273 Gateway + 478 core = 1.908 test**; 100% dòng cả ba package, core 100% nhánh, replay strict 28/28; Ruff/mypy đạt |
| Windows | **20/20** ca voice/timing/quyền Shorts đạt trên Python 3.14; test đọc sidecar được ghi rõ UTF-8 |
| Kết thúc | Worker đóng, queue/deferred = 0; hai gate đăng mới chờ chủ xưởng; **0 publish-event / 0 upload-session** |

Đã sửa một lỗi vận hành: Shorts gom quyền/credit của mọi take trong lịch sử, nên giọng Piper `unknown`
có thể làm bẩn bản đã thay tiếng. Bản trích nay lấy bản ghi mới nhất của reference hiện tại, final/captions,
tư liệu reflow và music cùng phiên bản. Ba regression đỏ trước sửa, sau sửa chứng minh loại credit cũ,
giữ CC BY hiện tại nếu đang dùng và vẫn giữ `unknown` khi tư liệu thực dùng chưa có quyền. Lịch sử không bị xóa.

Wrapper kiểm SHA model/codec/preset rồi chạy offline. Nó nhận dạng chính WAV đã lưu bằng small, thử large-v3-turbo
khi small không khớp, chỉ trả timing khi một lần nhận đúng toàn bộ token và mốc hợp lệ. Không thay chữ nhận sai
bằng narration. Các probe từng câu có khác biệt về dấu giữa hai model; đã giữ kết quả và nguồn mốc từ.
Kiểm độc lập trên **toàn bộ hai MP4 cuối** bằng cả hai model đều khớp 66 từ. ASR vẫn là kiểm bổ sung,
không tạo chứng nhận nghe hoặc phê duyệt đăng của người.

SEO thật của cả hai bản đã ghi giọng VieNeu/Ngọc Huyền, tác giả/Apache, có keyword/category/alt_titles;
không còn credit Piper/VAIS1000/Lessac trong metadata của bản mới. Không chạy thêm lượt SEO chỉ để đổi câu chữ.
Brief/voice/review cũ giữ để kiểm toán; assessment mới và audio nguồn xác định giọng thực dùng.
Các nit về thumbnail B chưa chọn, kiểu hook và con trỏ script_version cũ vẫn có trong receipt;
không ảnh hưởng kết quả sửa tiếng hoặc thay quyền giọng, chưa có A/B công khai.

Gói xem có hai MP4/SRT, `ghi-cong-va-quyen-su-dung.txt`, `licenses/`, `delivery.json`.
Receipt: `result.json`, `verification-summary.json`, `independent-validation.json`, `final-speech-validation.json`,
`selected-speech-candidates.json`, `provider-smoke-S4.recognition.json`, `runtime-artifacts.json`, `runtime-lock.txt`.
Workspace được phục hồi từ backup của lượt trước vào thư mục riêng; `source-backup-restore.json` giữ receipt.
Log trong `output/real-validation/logs/`: `commercial-voice-verify.log`, `commercial-voice-windows-final.log`,
`commercial-shorts-rights-red.log`, `commercial-shorts-rights-green.log`, `commercial-provider-smoke.log`,
`commercial-voice-live.log`, `commercial-independent-qc.log`, `commercial-final-speech-both.log`.

## Lượt trước: sửa cụm giọng khó và xác minh quyền Piper (2026-10-17)

Hai bản xem mới nằm tại `output/real-validation/piper-clarity-20261017-055352/`.
Lượt này phục hồi backup của bản Piper trước vào workspace riêng, sửa lời theo yêu cầu người vận hành,
chạy Piper và các bước fact/SEO/editor/quality/rights bằng model CLI thật. Không đăng hoặc lên lịch YouTube.

**Kết luận quyền: NOT_CLEARED đối với phát hành công khai/kiếm tiền bằng giọng
`vi_VN-vais1000-medium` hiện tại.** Đây là quyết định vận hành dựa trên hồ sơ đã tìm được,
không phải kết luận pháp lý rằng mọi đầu ra đều vi phạm. Hồ sơ đầy đủ tại
[đánh giá quyền 17/10](rights/piper-release-assessment-20261017.json).

- Giấy phép GPL của chương trình Piper không tự động áp cho mọi âm thanh sinh ra; xem
  [giải thích của GNU về đầu ra](https://www.gnu.org/licenses/gpl-faq.en.html#WhatCaseIsOutputGPL).
- CC BY 4.0 nằm ở mục Dataset của
  [model card VAIS1000](https://huggingface.co/rhasspy/piper-voices/blob/main/vi/vi_VN/vais1000/medium/MODEL_CARD).
  Card cũng ghi fine-tune từ `en_US-lessac-medium`; nhãn MIT của kho không đủ để kết luận quyền của giọng này.
- [Michael Hansen, tác giả Piper](https://github.com/rhasspy/piper/discussions/271), giải thích Piper không
  cấp thêm giấy phép cho model/checkpoint, người dùng phải đánh giá giấy phép gốc và lịch sử huấn luyện.
  [Giấy phép tư liệu Lessac](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html)
  là giấy phép nghiên cứu có hạn chế thương mại. Chưa tìm được văn bản bao trùm quyền phát hành của lineage này.

Đính chính cách gọi “giọng có giấy phép CC BY” ở lượt trước: CC BY là bằng chứng của dataset VAIS1000,
không phải giấy phép đầu ra đã được xác lập. Cấu hình hiện tại ghi `tts.license: unknown`, truyền
`rights_assessment` tới SEO/rights-checker. SEO thật đã sửa metadata của cả hai bản, ghi nguyên văn
“Bản xem cục bộ; quyền phát hành giọng Piper chưa được xác lập.” Các review quyền mới đều **block**;
không đổi verdict thành pass vì đây là bản xem riêng. Lịch sử cũ được giữ để đối chiếu.
Muốn phát hành cần giọng có hồ sơ quyền tương thích hoặc văn bản cho phép bao trùm lineage hiện tại.

Các cụm khó được đổi thành lời thuần Việt: “bản đã xuất”, “tên người”, “cảnh cần sửa”,
“chỉ chốt khi … đều đúng”. Câu đối chiếu tiếng/chữ giữ ý đồng thời: “Vừa nghe lời đọc, vừa xem phần chữ”.
Piper chạy `length_scale=1.55`, `noise_scale=0`, `noise_w_scale=0`, nghỉ giữa câu 0,18 giây;
không tăng tốc âm thanh sau sinh. Wrapper dời mốc từ theo đúng số mẫu âm của khoảng nghỉ.
Đã đối chiếu phoneme runtime hiện tại với bản legacy: không thấy hồi quy rộng giải thích các cụm khó.

| Hạng mục | Kết quả của hai bản xem mới |
|---|---|
| Video chính | `Pb37ab606a82a432780bac795-V1`, **v3, 1920×1080, 17,89 giây**, 8 cue |
| Shorts | `SHORT-f374900d20ad063d54365706`, **v1, 1080×1920, 17,72 giây**, 9 cue, 4 khoảng cảnh |
| Review thật | Cả hai **fact pass / quality pass / rights block**; các góp ý không chặn vẫn giữ trong receipt |
| Âm thanh | H.264/AAC stereo 48 kHz, 25 fps; main **-14,1**, Shorts **-14,2 LUFS**; true peak **-1,0 dBTP** |
| File/phụ đề | Decode toàn bộ đạt; không finding QC, đoạn đen hoặc im lặng vượt ngưỡng; ≤2 dòng, không tràn/chồng/cue một từ |
| Hình sau encode | Codex đã xem frame tại giữa mọi cue của cả hai MP4 và thumbnail A; bốn ảnh đúng thứ tự, chữ nằm gọn trong khung ngang/dọc |
| Sửa có chọn lọc | Giữ nguyên SHA bốn ảnh; từ v2 sang v3 chỉ thay audio S3, audio S1/S2/S4 giữ nguyên |
| Tạo media | Thêm **5 take TTS**, không sinh ảnh mới; Shorts dùng lại nguồn và đủ thumbnail A/B |
| Text token | Thêm **832.843** trong lượt này; ledger lũy kế sau phục hồi **1.441.413** |
| Ngân sách | Main lũy kế **933.646 / 1.500.000**, Shorts mới **252.641 / 1.500.000**; retry 0 |
| Giá media | Lũy kế 15 generation chưa có đơn giá; `estimated_usd=0` không nghĩa miễn phí |
| Source | **1.149 Studio + 273 Gateway + 478 core = 1.900 test**; 100% dòng cả ba package, core 100% nhánh, strict replay 28/28; Ruff/mypy đạt |
| Windows | **29** ca speech/timing/enrichment chạy native và đạt |
| Trạng thái cuối | Worker đóng, queue/deferred = 0; **0 publish-event / 0 upload-session**, không tạo phê duyệt đăng |

ASR small chạy CPU int8, không gợi transcript, đối chiếu trên đúng SHA của hai MP4 cuối:
mỗi bản khớp **63/64 từ**, chỉ còn “rõ” → “xõ”. Kiểm riêng WAV S2 cũng nhận như vậy;
không sửa phụ đề đúng để chạy theo ASR và không coi ASR là chứng nhận nghe/phát âm của người.
Các cụm lỗi cũ đã khớp; một từ còn khác vẫn cần được đối chiếu khi nghe bản xem. Model reviewer đọc
JSON/QC, không tự nghe tiếng. Không có phê duyệt nghe hoặc phát hành của người được tạo trong lượt này.

Giới hạn còn giữ rõ: quality reviewer muốn dùng thuật ngữ “phụ đề” chính xác hơn “phần chữ”;
thumbnail B chưa chọn vẫn có dấu phẩy; metadata có diễn giải rộng hơn lời đọc ở một vài gạch đầu dòng.
Brief/voice/dossier và asset cũ còn cách gọi CC BY lịch sử; assessment hiện tại và metadata mới đính chính
chúng, không viết lại event cũ. Nhãn `generated` của bản ghép cũng không chứng nhận quyền các thành phần;
audio `unknown` và review quyền block vẫn chi phối. Shorts cũ trong backup đã quá hạn review ở workspace
phục hồi nên giữ escalation riêng; `result.json` chọn đúng hai bản mới, `state-full.json`/SQLite giữ toàn bộ.

Receipt: `result.json`, `verification-summary.json`, `independent-validation.json`,
`post-metadata-validation.json`, `selective-repair-verification.json`, `speech-recognition-*.json`,
`speech-S2-supplement.json`, `metadata-rights-correction-*.json`, `rights-intake-*.json`.
Log trong `output/real-validation/logs/`: `piper-clarity-verify.log`, `piper-clarity-windows.log`,
`piper-clarity-live-final.log`, `piper-clarity-metadata-final.log`, `piper-clarity-independent-qc.log`,
`piper-clarity-final-asr.log`, `piper-clarity-S2-supplement.log`.

## Lượt trước: chuyển sang Piper và ghi nhận góp ý (2026-10-16)

Theo lựa chọn mới của người vận hành, chuyển giọng mặc định cục bộ sang Piper `vi_VN-vais1000-medium`.
[Hồ sơ giọng](rights/piper-vais1000-evidence.json) giữ model card upstream, nguồn và SHA-256 model/config,
CC BY 4.0 của VAIS1000, nhãn MIT của kho model và lineage Lessac. Runtime Piper GPL cài riêng, không vendored.
Nguồn dataset/lineage không được diễn giải thành một giấy chứng nhận quyền tổng quát.
Ghi công và thay đổi (tổng hợp, rút đuôi, chuẩn hóa) được giữ trong audio gốc/trim và metadata hai bản.

| Góp ý trước | Thay đổi trong quy trình |
|---|---|
| Metadata hứa tốc độ từ độ dài video | Hợp đồng metadata cấm dùng runtime làm thời gian hoàn thành thao tác; dữ liệu runtime đo riêng |
| Quyền Edge chưa rõ | Thay bằng giọng Piper cục bộ theo lựa chọn người vận hành; giữ nguyên review block của lượt Edge |
| Font thiếu bằng chứng | Đối chiếu font DejaVu theo SHA, giữ notice và nguồn upstream; font khác vẫn unknown |
| Shorts chỉ một scene, thiếu claim_ids | Giữ từng mục/cảnh/claim; clip_start_s xác định khoảng thật để QC và inspection lấy đủ mẫu |
| Poster pad, thiếu A/B/C | Dùng lại đủ bộ thumbnail nguồn 1280×720 đúng SHA, kèm spec và provenance |
| Tư liệu tái dùng biến mất khỏi gói editor | Chọn theo reference hiện tại của manifest, giữ asset từ version trước |
| Dấu ba chấm phụ đề không được đọc | Bỏ token chỉ có dấu; vẫn kiểm toàn bộ chữ/số trước khi phục hồi dấu câu |
| Quyền/credit mất khi trim hoặc tạo Shorts | Audio và bản trích giữ license/source/attribution; metadata tự thêm CC BY credit |

Giọng mới sinh mốc từ từ tensor duration của phoneme trong Piper. Sidecar phải khớp SHA audio và toàn bộ lời;
không khớp thì TTS báo lỗi. Nếu không ghép được đủ token, wrapper ghi rõ mốc câu thay vì nhận là mốc từ.
Mô hình ONNX gốc không bị sửa trên đĩa. Cấu hình thử mới không tăng tốc âm thanh sau sinh.

Lượt Edge `default-factory-20261016-231214` đã dừng sau rights block; `225835` giữ chẩn đoán timing/thời lượng.
Không thay verdict hoặc lịch sử ngân sách. Cả hai đều 0 publish/upload. Lượt Piper mới chạy độc lập tại
`output/real-validation/default-factory-20261016-233434/`; kết quả cuối đạt trong phạm vi xem cục bộ.
Đã xử lý hết sự kiện đang chờ, gồm SEO thật của Shorts: từ khóa, category và alt_titles có đủ, giữ ghi công;
không dừng lượt thử chỉ vì hai gate vừa mở. `result-at-gates.json` giữ snapshot trước bước hoàn tất metadata.

| Hạng mục | Kết quả Piper thực tế |
|---|---|
| Bản chính | `Pb37ab606a82a432780bac795-V1`, v1, 1920×1080, 18,84 giây |
| Shorts | `SHORT-676908fce551fe93f5476439`, v1, 1080×1920, 18,63 giây; 4 scene có clip_start_s |
| Model | Claude CLI và CLI-vector thật, Piper 1.8.0 chạy CPU, FFmpeg; không scripted text/review |
| Review | Cả hai fact/quality/rights pass cho scope private_local_validation, 0 vòng sửa |
| Hình/phụ đề | Đã xem frame thực ở từng cue của cả hai MP4; 8 cue mỗi bản, ≤2 dòng, không tràn/chồng hoặc cue một từ |
| Tiếng/QC | H.264/AAC stereo 48 kHz, 25 fps; -14,5 LUFS, true peak -1,0 dBTP; decode toàn file đạt, không finding QC |
| Lineage | Bốn khoảng cảnh/claim giữ đủ; inspection 4/4; thumbnail A/B nguồn giữ toàn bộ 1280×720, SHA khớp |
| Tạo media | 10 generation thành công: 4 take Piper + 4 ảnh cảnh + 2 thumbnail; Shorts dùng lại nguồn |
| Ngân sách | Main 353.444 / 1.500.000, Shorts 228.093 / 1.500.000 token; retry 0 |
| Sổ text/media | 608.570 text token ghi nhận; 10 generation chưa có đơn giá, estimated_usd=0 không nghĩa miễn phí |
| Kiểm thử source | 1.146 Studio + 273 Gateway + 478 core = 1.897; 100% dòng, core 100% nhánh, strict replay 28/28 |
| Windows | 44 ca voice/media + 20 ca cache; cả hai batch đạt |
| Backup/restore cuối | 87 file, 14.601.801 byte; đối chiếu 37 asset và mọi media reference, giữ cấu hình hình/phụ đề; restore dừng, automation manual |
| Trạng thái cuối | Queue/deferred = 0; hai gate publish pending, 0 publish-event / 0 upload-session; worker đã đóng |

`make verify` ban đầu phát hiện resolved voice bookkeeping làm cache TTS đổi khi chỉ sửa ảnh. Đã giữ log đỏ,
loại hai trường output khỏi input cache key, kiểm 38 regression đạt, rồi chạy lại full verify trên source cuối.
Lượt thật được pause/resume vào source cuối trước final/review; không thay history hoặc verdict.

Giới hạn chất lượng được giữ rõ: ASR không gợi transcript vẫn nhận khác các cụm như “tên riêng” thành
“tây xím”, “video” thành “vì yêu”. Kiểm lại từng cảnh từ WAV và thử nhịp 1,25/1,35 không giải quyết rõ.
Không đổi phụ đề đúng theo ASR, không chứng nhận đã nghe bằng người; **giọng Piper cần nghe chốt trước phát hành**.
Các model reviewer đọc JSON/QC, không tự nghe file. Receipt và so sánh nằm trong `speech-recognition-*.json`,
`speech-recognition-scenes.json`, `speech-pace-comparison/`.

Về quyền: reviewer đã nhận card/CC BY/credit và cho qua bản xem cục bộ. Dữ liệu tiền huấn luyện Lessac
có [trang giấy phép nghiên cứu](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html);
không coi đó là giấy phép đầu ra hoặc kết luận quyền thương mại cho model fine-tune. Xem
[ghi chú phát hành](rights/piper-public-release-note.json). Public/commercial release chưa được chốt.
Ghi công CC BY đầy đủ đã đối chiếu ở metadata hai bản; không suy quyền toàn bộ video từ nhãn generated.

Góp ý nhẹ mới vẫn giữ trong review receipt: thumbnail B có dấu phẩy trái style kênh (A đang được chọn),
main có dòng ghi công ngắn trùng credit đầy đủ; thumbnail thiết kế ghi manifest_version 0, đã đối chiếu
đúng project và SHA của bản nguồn. Chưa chạy A/B công khai. Cảnh báo thiếu chapter do format=long của bài
thử dưới 20 giây không yêu cầu thêm chapter. Hai contact sheet đã xem bằng Codex, không giả phê duyệt người dùng.

Log: `logs/piper-verify-final.log`, `piper-windows.log`, `piper-windows-cache.log`,
`piper-live-model-final.log`, `piper-live-model-drained.log`, `piper-independent-qc.log`,
`piper-asr-final.log`, `piper-asr-scenes-final.log`, `piper-asr-pace-final.log`, `piper-backup-final.log`.
Backup giữ media/metadata và profile hình/phụ đề. Khi phục hồi để tạo video mới, cấu hình lại provider và runtime/model Piper; lệnh executable và credential nằm ngoài gói an toàn.
Receipt cuối: `result.json`, `post-metadata-validation.json`, `independent-validation.json`,
`backup-restore-validation.json`, `test-verification.json`, `verification-summary.json`.

## Chất lượng mặc định và dự án mới bằng model thật

Đã đưa các cải thiện vào renderer/factory thường dùng, theo [ADR-0017](adr/0017-default-quality-and-native-shorts.md).
Lượt đạt mới: `output/real-validation/default-factory-20261016-223325/`; chạy bằng
`scripts/live-defaults-smoke.py`, không dùng script xuất riêng của bài thử cũ.

- Cảnh ảnh mặc định dàn khung editorial ở độ phân giải xuất, lấy tiêu đề/heading và ảnh của dự án hiện tại.
  Phụ đề ASS có vùng an toàn riêng cho ngang/dọc, giữ dấu câu khi khớp toàn bộ token, cân hai dòng và dùng
  chiều rộng 38/26 ký tự. Giới hạn cue không để từ kết câu đứng riêng; mốc từ vẫn lấy từ provider.
- Giữ take TTS gốc, chỉ rút đuôi sau từ cuối + 0,28 giây khi có timing hợp lệ; không tăng tốc lời đọc.
  Khung `scene_layout` có media asset/checksum riêng, kế thừa quyền và nguồn của ảnh. Cache có recipe,
  checksum nguồn, font và nội dung, với ID ledger ổn định khi resume; không tạo outbox render dở dang.
- Shorts mặc định `reflow`: dùng ảnh/giọng gốc đúng checksum, dàn lại theo 9:16, tạo phụ đề dọc từ mốc từ.
  Kiểm thử chứng minh thay tư liệu nguồn sẽ bị từ chối, idempotency giữ nguyên clip, nguồn không bị ghi đè.
  Luồng giữ/cắt khung cũ vẫn phải được chọn rõ khi thiếu tư liệu hoặc dùng footage.
- Backup giữ các tùy chọn chất lượng mới. Hai lượt Windows phát hiện lỗi đổi tên thư mục do khóa tạm;
  regression mô phỏng lỗi chia sẻ 32 và quyền 5, thử lại tối đa sáu lần (tổng đợi 1,55 giây). Lỗi kéo dài
  vẫn dừng, không ghi đè đích. Cả tạo backup và restore dùng cùng cơ chế; lỗi quyền khác không bị che.

Lượt chẩn đoán `default-factory-20261016-221200` bắt được thiếu ledger khung trình bày và cue một từ.
Đã viết/chạy regression đỏ trước khi sửa, giữ nguyên lịch sử review lỗi và tạm dừng lượt đó.
Lượt đạt dưới đây tạo một dự án mới sau sửa, không thay kết quả review của lượt lỗi.

| Hạng mục | Kết quả thực tế |
|---|---|
| Bài mới | **Kiểm tra video đã xuất trước khi duyệt**, bốn cảnh do model tạo mới |
| Backend | Claude CLI thật (Haiku/Sonnet/Opus 5.5 ghi trong audit), CLI-vector thật, Edge HoaiMy, FFmpeg |
| Bản chính | `Pbb0c0ef9d9de4aedb6623e0f-V1` v1; **1920×1080, 25,09 giây**, 9 cue |
| Shorts | `SHORT-676908fce551fe93f5476439` v1; **1080×1920, 24,65 giây**, 10 cue, `layout=reflow` |
| Review thật | Cả hai đều **fact/quality/rights pass** trong phạm vi cục bộ; chưa có phê duyệt đăng |
| Âm thanh | H.264/AAC stereo 48 kHz, 25 fps; **-14,2 / -14,1 LUFS**, true peak **-2,0 dBTP** |
| Kiểm file độc lập | Decode toàn bộ hai MP4 đạt; không đoạn đen/im lặng vượt ngưỡng, không finding QC |
| Phụ đề | Không chồng cue; tối đa hai dòng; đúng chiều rộng và bề rộng font đo được; không cue một từ |
| Tư liệu | 4 take TTS + 7 ảnh gồm thumbnail, **11 generation thành công**, không generation mới cho Shorts |
| Ngân sách quản lý | Bản chính **387.189 / 1.500.000**, Shorts **168.078 / 1.500.000** token, retry 0 |
| Sổ dùng text | **621.431 token** ghi nhận, gồm thêm 64.349 token intake quyền và 1.815 token cấp channel |
| Giá media | 11 generation chưa có đơn giá; `estimated_usd=0` trong ledger **không có nghĩa miễn phí** |
| Backup/restore thật | **90 file, 15.829.977 byte**; đối chiếu **31 asset** và mọi media reference trong manifest |
| Cấu hình phục hồi | Giữ editorial, 38/26 ký tự, trim; phục hồi dừng và automation manual |
| YouTube | **0 publish-event, 0 upload-session**; cả hai `PUB-*` vẫn pending, worker thử đã đóng |

Đã xem contact sheet lấy từ **từng cue của MP4 sau encode**, xác nhận bốn hình đúng thứ tự, chữ/heading
không tràn khung và CTA cuối còn đủ. Nhận dạng tiếng cục bộ bằng faster-whisper-small CPU int8 trên cả hai
file, không có transcript gợi ý, giữ kết quả trong `speech-recognition-*.json`. ASR vẫn có khác biệt như
“Lệch đâu” → “Lịch đâu”; đây là kiểm tra bổ sung, không phải xác nhận nghe/phát âm của người.

Model review vẫn có góp ý không chặn bản thử: loại lời hứa “trong 30 giây” khỏi metadata/A-B trước khi đăng;
khai rõ điều khoản Edge/font và quyền tên cho phát hành công khai; Shorts hiện là một container clip trong
manifest nên inspection tự động chỉ lấy một mẫu, poster còn pad ngang và claim_ids chưa nối về từng mục.
Contact sheet độc lập phủ cả bốn cảnh nhưng không thay phê duyệt phát hành. Các góp ý được giữ trong receipt,
không đổi `pass` thành nhận định “đã đủ mọi điều kiện đăng”.

`make verify` source cuối đạt **1.127 Studio + 273 Gateway + 478 core = 1.878 test**;
coverage dòng **100%** cả ba package, core **100% nhánh**, strict replay **28/28**, Ruff/mypy đạt.
Windows native **33/33**, gồm backup/restore và dựng video/Shorts thật từ fixture. Browser smoke desktop/mobile
đạt, gồm mặc định reflow và ẩn điều chỉnh crop khi không dùng cover. Log:
`logs/default-verify-passed.log`, `default-windows-passed.log`, `default-ui-final.log`,
`default-live-model-fresh.log`, `default-independent-qc.log`, `default-backup-live-passed.log`.
Receipt: `result.json`, `independent-validation.json`, `backup-restore-validation.json`, `verification-summary.json`.

## Rà soát hình, tiếng và phụ đề; xuất lại để xem

Theo yêu cầu tiếp theo của người vận hành, tạo hai **bản xem cục bộ** tại
`output/real-validation/quality-review-20261016-final/`. Không thay file cũ, không ghi phê duyệt mới
cho các bản này trong factory, không gọi adapter đăng hoặc lên lịch.

- Hình cũ chỉ 360p; Shorts thu nhỏ cả khung ngang nên chữ/hình nhỏ và nhiều khoảng trống. Bản mới dựng
  HTML/CSS/SVG gốc ở đúng **1920×1080** và **1080×1920**, không phóng to ảnh 360p. Sáu trạng thái hình đổi
  theo mốc từ của hook/bốn bước/CTA; Shorts có bố cục dọc riêng. Đã xem các frame thật sau encode.
- Âm thanh tái dùng HoaiMy stock đã lưu, lấy S2 từ take trước khi thêm câu thừa “Tiếp theo, Ba”. Chỉ bỏ
  khoảng lặng cuối mỗi take, giữ 0,28 giây sau từ cuối và cân âm về mục tiêu -14 LUFS; không tăng tốc giọng.
  Bản ngang và dọc dùng cùng track; PCM giải mã từ hai MP4 có cùng SHA-256.
- Lỗi phụ đề: renderer trước đây lấy text của word boundaries nên mất dấu câu, đồng thời bỏ qua cấu hình
  chiều rộng khi có timing. Hai regression đỏ tái hiện đúng lỗi. Code khôi phục dấu câu từ narration khi
  toàn bộ token khớp, giữ mốc từ đo được, tôn trọng chiều rộng và ngắt câu; không gán transcript khác vào audio.
  Bản xuất có **9 cue**, tối đa hai dòng, giữ đủ lời và dấu câu, không chồng cue hay đứng lẻ một từ ở dòng cuối.
  Có ngắt dòng biên tập riêng cho các cụm “bản gốc”, “thư mục”, “phụ đề”. Đã đo bề rộng chữ bằng font thật.
- Cả hai MP4 dài **25,8 giây**, 25 fps, H.264/AAC stereo 48 kHz; giải mã toàn file đạt. QC đo **-14,2 LUFS**,
  true peak **-4,5 dBTP**, **0 sample clipping**, không đoạn đen/im lặng vượt ngưỡng, không finding QC.
  Chi tiết: `result.json`, `final-qc.json`, từng thư mục `captions.srt`/`captions.ass` và `frame-*.jpg`.
- Nhận dạng giọng nói chạy cục bộ bằng faster-whisper-small CPU int8, không gửi transcript gợi ý; xem
  `speech-recognition-check.json`. Nội dung chính và thứ tự bốn bước/CTA được nhận dạng; ASR vẫn có khác biệt
  như “thư mục” → “thư 1”, “sao” → “sau”. Đây là kiểm tra bổ sung, chưa phải xác nhận nghe/phát âm của người.
  Không thay phụ đề chuẩn bằng các từ ASR khác biệt. Công cụ/model kiểm tiếng nằm trong cache thử riêng,
  không thêm dependency vào dự án hoặc cài pipeline sinh video GPU.

`make verify` trên source này đạt **1.120 Studio + 273 Gateway + 478 core = 1.871 test**;
coverage dòng **100%** cả ba package, core **100% nhánh**, strict replay **28/28**, Ruff/mypy đạt.
Windows native chạy riêng file speech timing đạt **6/6**. Log: `logs/quality-export-verify.log`,
`logs/quality-export-windows.log`, `logs/quality-final-qc-passed.log`, `logs/quality-final-speech.log`.
Script tái xuất: `scripts/review-quality-export.py`; kiểm tra cú pháp và Ruff riêng cho script cũng đạt.

## Chạy lại sau bổ sung ngân sách và hồ sơ quyền

Yêu cầu tiếp theo của người vận hành: xử lý ngân sách/hồ sơ quyền, chạy trọn luồng model thật,
chưa đăng YouTube. Lượt xác minh cuối dùng workspace `output/real-validation/factory-20261016-191549/`.

- Đặt trước **1.500.000 token/video** cho lượt thử mới, giữ số sử dụng thực tế và Supervisor.
  Ngân sách/số đo của các lượt cũ không bị viết lại. Đây là trần token, không phải giá tiền media hay hóa đơn CLI.
- [Hồ sơ quyền](rights/local-validation.json) ghi chính xác yêu cầu người vận hành, quyền dùng tên dự án
  cho bản kiểm thử cục bộ, các giới hạn và yêu cầu provenance cho giọng đọc/ảnh/nhạc. Không tạo giấy chứng nhận
  sở hữu nhãn hiệu, không giả license của bên thứ ba, không có phê duyệt đăng YouTube.
- Dossier được lưu trong yêu cầu dự án bằng `rights_dossier`, gói review gắn với video/channel và event/actor
  người vận hành. Reviewer nhận đầy đủ script, manifest/prompt, thumbnail spec và thời lượng mục tiêu.
  Gọi **rights-checker thật** để ghi hồ sơ vào namespace `rights` từ chính final mới, đúng chủ namespace;
  không dùng phê duyệt thuộc video tham chiếu khác. Dossier và receipt được buộc bằng SHA-256.
- Gom các review lỗi của cùng script/final thành một lượt sửa, tổng hợp lý do và giữ checkpoint sau restart.
  Hai regression mới kiểm dossier theo dự án và cơ chế gom review; không tự tăng budget hoặc xóa usage.
- Kế hoạch có risk tag `brand`, nên policy tự động giữ lại đúng thiết kế. Sau khi đọc kế hoạch cụ thể,
  ghi receipt `policy:studio` có chữ ký cho đúng **một plan**, dựa trên yêu cầu chạy thử hiện tại của người vận hành.
  Không ký dưới actor human và không phê duyệt gate publish. Xem `plan-authorization.json`.

Lượt `factory-20261016-171201` phát hiện gói quyền cũ thiếu lời đọc/prompt và tham chiếu khác video; giữ nguyên
làm bằng chứng chẩn đoán. Lượt `factory-20261016-191223` gặp Codex HTTP 503 / model at capacity ở bước plan.
Lượt cuối lọc backend bằng `STUDIO_LLM_BACKENDS=claude-code-cli` riêng cho process, không đổi `llm.yaml`;
Claude thực hiện cả model text/review và CLI-vector. Edge TTS/FFmpeg cũng chạy thật.

`make verify` trên source cuối (gồm sửa provenance Shorts) đạt **1.118 Studio + 273 Gateway + 478 core = 1.869 test**,
coverage dòng **100%** cả ba package, core **100% nhánh**; strict replay **28/28**, Ruff/mypy đạt.
Windows native **44 test đạt**, một ca symlink không chọn. Log giữ trong
`output/real-validation/logs/rights-budget-shorts-verify.log` và `rights-budget-shorts-windows.log`.
Tóm tắt máy đọc được: `rights-budget-verification-summary.json`. `git diff --check`, kiểm cú pháp Python và
Ruff cho hai script smoke cũng đạt. Gateway có một cảnh báo runpy trong test main guard; không có test lỗi.

Kết quả cuối của model thật: **đạt trong phạm vi thử cục bộ**. Receipt ở
`factory-20261016-191549/final-live-result.json`; snapshot ở `final-state.json`.

| Hạng mục | Kết quả lượt cuối |
|---|---|
| Model | CLI thật báo `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`; không dùng scripted text/review |
| Bản chính | `Pa993160c66a0444faf84430b-V1`, manifest v2 sau sửa S2 từ giao diện; fact/quality/rights đều `pass` |
| Sửa riêng cảnh | SHA-256 audio, ảnh và timing S1 vẫn trùng receipt v1; chỉ narration S2 được đổi |
| Shorts | `SHORT-a789bfcd0b7be46fd10c9a0a`, ba review riêng đều `pass`; nguồn v2, SRT căn 0,138–27,262 giây |
| Ngân sách bản chính | **509.500 / 1.500.000 token** (33,97%), gồm lượt sửa cảnh và review mới |
| Ngân sách Shorts đạt | **113.613 / 1.500.000 token** (7,57%); không sinh lại giọng hoặc ảnh nguồn |
| Lịch sử chi phí | Đoạn Shorts lỗi dùng 274.790 token, giữ nguyên; 11.463 token cấp channel, tổng workspace 909.366 |
| Media | Bản chính 28,37 giây, 640×360; Shorts 27,16 giây, 360×640; cả hai H.264/AAC, stereo 48 kHz, giải mã toàn file đạt |
| Backup/restore qua UI | 64 file, 6.566.068 byte; restore vào thư mục mới, cả ba dự án đọc được, tất cả media reference mở được, xưởng dừng |
| UI | Desktop/mobile không lỗi JavaScript, không tràn ngang ở 390 px; settings hiển thị tài nguyên và tạo backup thật |
| YouTube | **0 publish-event, 0 upload-session**; hai gate `PUB-*` của bản chính/Shorts mới vẫn pending |

Gói thử đầu tiên của Shorts (`SHORT-56460c22bb0562aeff4d123a`) bị rights giữ lại vì producer chỉ ghi
`derived`, chưa nối được poster về clip và nguồn. Giữ dự án này **paused** cùng gate escalation làm lịch sử,
không xóa finding hoặc giả phê duyệt. Regression mới tái hiện lỗi với cả fake media và FFmpeg thật.
Sửa provenance cho từng output: FFmpeg cắt clip, code căn/rebase SRT, FFmpeg lấy frame 0,1 giây và Pillow pad
poster. Audit ghi full SHA-256 video/phụ đề nguồn, version nguồn và output; gói rights mang manifest và asset
nguồn đúng checksum. Có kiểm tương thích audit cũ. Review lỗi của đoạn do người dùng chọn giữ nguyên đoạn
trích thay vì tự gọi writer tạo lời đọc khác. Sau sửa, tạo Shorts mới qua giao diện và chạy lại đủ ba reviewer thật.

Xem `console-result.json`, `final-media-probes.json`, `shorts-rights-context.json`, `console-backup/`,
`console-restored/` và log `rights-budget-live-console-fixed.log`. Poster/frame giây 20 cũng đã được xem để
đối chiếu hình thay đổi; trạng thái inspection vẫn `human_required`, không giả một phê duyệt của người dùng.
Sau restore còn đối chiếu lại dossier và chuỗi quyền: version nguồn vẫn là v2, cả **6 asset nguồn** được rebase
vào workspace phục hồi và SHA-256 khớp; xem `restore-rights-check.json`.

Token là tổng backend báo, bao gồm input được cache; không đồng nhất với giá hóa đơn CLI. Ledger giữ **11
generation chưa biết giá**, nên số ước tính USD bằng 0 không được hiểu là miễn phí. Chưa có giá media do người
vận hành cấu hình. Các review có cảnh báo không chặn về metadata/độ phân giải và trường hook/CTA của clip.
Hồ sơ quyền chỉ bao phủ render/review cục bộ; chưa xác minh quyền phát hành công khai hoặc điều khoản thương mại
Edge TTS. Không có adapter tự động xem hình/nghe tiếng; model review đọc JSON, provenance và QC thật.

## Kết quả đã đo

| Luồng | Kết quả thực tế | Bằng chứng cục bộ |
|---|---|---|
| Edge native | HoaiMy: 11,136 giây, 34 word boundaries; timing `provider` | `output/real-validation/edge-live.json`, `edge-live.mp3` |
| Đổi giọng/tốc độ | Mapping `onyx` → NamMinh, pace fast → +15%; audio 3,216 giây, 11 boundaries | `edge-alternate.json`, `edge-alternate.mp3` |
| Provider command hiện tại | CLI trong runtime đã nâng lên 7.3.1, sinh MP3 thật thành công | `configured-command-voice.mp3` |
| Dựng/sửa cảnh | Hai cảnh với giọng Edge thật và ảnh đồ họa fixture tự tạo; sửa narration S1, giữ nguyên checksum audio/hình/timing của S2 đã khóa | `media-20261016-143318/result.json` |
| Final và phụ đề | MP4 14,376 giây, 640×360, 25 fps, H.264/AAC stereo 48 kHz; chữ Việt burn-in và SRT theo timing dịch vụ | `media-20261016-143318/media/LIVE/final_v2.mp4` |
| QC thực tế | Scan hoàn tất, LUFS -13,8, true peak -1 dB, không phát hiện đoạn đen/im lặng; không finding | `media-20261016-143318/qc.json` |
| Contact sheet | Hai cảnh lấy từ asset thật, report gắn manifest v2 và checksum; trạng thái `human_required` | `media-20261016-143318/media/LIVE/review_v2.jpg` |
| Shorts | Trích theo cue thành khoảng 5,05 giây; container 5,08 giây, 360×640, H.264/AAC, có SRT/poster | `media-20261016-143318/media/SHORT/` |
| Cache speech website | Audio thật 7,344 giây, 23 boundaries; gọi lại không tăng generation; phá file rồi gọi lại tăng đúng một generation và phục hồi được | `media-20261016-143318/result.json` |
| Ledger | LIVE: 3 lần TTS (2 ban đầu + 1 sửa); WEB-LIVE: 2 lần (ban đầu + phục hồi). Giá chưa cấu hình nên ghi chi phí chưa biết | `media-20261016-143318/result.json` |
| Dọn cache thật | Preview rồi xóa 3 MKV checkpoint, thu hồi 1.189.413 byte; checksum của toàn bộ 29 file còn lại giữ nguyên | `media-20261016-143318/cleanup-result.json` |
| Backup/restore | Gói 26 file, 1.566.919 byte; phục hồi đọc được ý tưởng, toàn bộ media reference còn mở được, xưởng dừng | `media-20261016-143318/backup`, `restored/` |
| Môi trường GPU | RTX A1000 Laptop, 6.144 MiB VRAM, driver 596.71; chưa có torch/diffusers/transformers hoặc ComfyUI đang phục vụ | `gpu-environment.json` |
| CUDA | Nhận RTX A1000 6 GB, cấp phát 1 MiB, ghi và đọc lại byte đúng; mọi CUDA return code bằng 0 | `cuda.json` |
| NVENC | GPU thật encode H.264 640×360, 25 fps, 10 giây; decode toàn file đạt | `gpu-nvenc.mp4`, `gpu-nvenc-probe.json` |
| Windows native | Python 3.12.14 win32/x64; 41 test đạt, một ca symlink không chọn; dùng FFmpeg/ffprobe Windows thật, bao gồm regression thumbnail sau rework | `windows-native/`, `logs/windows-tests.log` |
| Windows Unicode/render | Fixture 12 giây với phụ đề Việt, filename `Kiểm tra phụ đề Việt.mp4`; ffprobe và decode đạt | `windows-native/result.json` |
| Windows browser | Chromium Windows + backend Python Windows; import/conflict ideas, upload/library, scene edit, Shorts với gate riêng, resources, backup/restore, mobile đều đạt | `windows-ui-result.json`, `windows-ui/` |
| UI → media thật | Chromium gửi sửa narration đến Edge/FFmpeg, tạo final v2, giữ checksum cảnh còn lại; tạo Shorts thật, hai gate riêng còn pending; resources, backup/restore, mobile không lỗi JS | `console-media-20261016-1516/console-result.json`, `media-verification.json` |

Media fixture ở trên dùng **giọng đọc thật**, ảnh minh họa do code tạo để kiểm pipeline; không coi đó là
phim AI hoặc đánh giá chất lượng sáng tạo. CUDA/NVENC đạt không chứng minh một model diffusion chạy được trên 6 GB VRAM.
Windows local đã được chạy; workflow trên GitHub Actions chưa được dispatch vì source chưa được đẩy lên remote.
Lượt `console-media-20261016-1516` dùng model text/review **scripted fixture** để kiểm riêng đường UI → media;
Edge, CLI ảnh và FFmpeg đều thật. Nội dung và kết quả review fixture không phải bằng chứng chất lượng sáng tạo.

## Regression trên source cuối

Baseline trước bổ sung dossier/rework: `make verify` chạy với FFmpeg/ffprobe trên PATH, đạt **1.114 Studio + 273 Gateway + 478 core = 1.865 test**,
không skip. Coverage dòng của cả ba package đạt **100%**, core đồng thời đạt **100% nhánh**.
Ruff/mypy cả ba package đạt; eval replay strict **28/28**. `node --check` và `git diff --check` đạt.
Test Windows native chạy lại sau sửa Edge voice/pace và thumbnail đạt 41 ca; fixture symlink được loại khỏi lượt Windows.
Log lưu tại `output/real-validation/logs/verify.log`, `logs/windows-tests.log`;
tóm tắt máy đọc được tại `output/real-validation/verification-summary.json`.

Ba script smoke bổ sung để chạy riêng khi cần:

- `scripts/live-media-smoke.py`: Edge/FFmpeg, sửa cảnh, cache hỏng, Shorts và backup/restore; không gọi model text/ảnh.
- `scripts/live-upgrades-smoke.py`: các model CLI/provider hiện có, DB/output riêng; hỗ trợ `--resume ROOT`;
  ngân sách token đặt trước bằng `--budget-tokens`, mặc định 500.000 cho fixture mới.
- `scripts/live-console-smoke.py ROOT`: Playwright thao tác dự án trong workspace thử, sửa cảnh,
  tạo Shorts, kiểm tài nguyên và backup/restore; không quyết định gate publish.
  Cờ `--create-scripted-fixture` tạo DB mới với text/review fixture nhưng vẫn chạy Edge, CLI ảnh và FFmpeg thật;
  `console-result.json` ghi rõ `model_mode` và `media_mode`.

## Lỗi tìm được và sửa

1. Edge TTS 7.2.8 đang cài bị dịch vụ trả HTTP 403. Nâng dependency và lock lên **7.3.1**, đồng thời nâng CLI
   trong runtime X-Studio; các lần gọi lại qua native adapter và command đều nhận audio thật.
   Nguồn phiên bản: [edge-tts 7.3.1](https://pypi.org/project/edge-tts/7.3.1/).
2. Native Edge adapter bỏ qua `tts.voices` và pace của manifest. Test đỏ tái hiện đúng lỗi; sửa dùng chung
   `pick_voice`/`PACE_SPEED`. `tts.rate` khai rõ vẫn được ưu tiên. Kiểm tra lại với NamMinh fast bằng dịch vụ thật.
3. Hai fixture Shorts dùng encoding mặc định Windows cp1252. Đọc/ghi fixture tiếng Việt được đặt UTF-8 rõ ràng;
   bộ Windows chạy lại đạt. Pipeline media đã dùng UTF-8 trước sửa này.
4. Sau một lượt rework, package review chứa cả thumbnail A/B cũ và mới cùng đường dẫn, checksum khác nhau;
   model thật đã block vì không xác định được thumbnail hiện hành. Regression đỏ tái hiện bốn record trong package.
   Sửa projection chỉ lấy record mới nhất mỗi biến thể, đính kèm spec và phạm vi version của thumbnail.
   Event log vẫn giữ lịch sử. Kiểm lại trên chính media thật: hai thumbnail hiện hành, checksum đúng với file.
   Phát lại checkpoint v2 đến sau inspection/QC rồi gọi quality reviewer thật bằng package đã sửa:
   **verdict `pass`, không finding**, model `gpt-5.6-sol`, backend báo 22.877 token.
   Bằng chứng `factory-20261016-143722/thumbnail-live-review.json`; kết quả thử này không publish vào DB gốc
   và không quyết định gate escalation. Đây vẫn là review JSON, không phải model trực tiếp xem/nghe media.

Binary Windows lấy từ [bản phát hành của nhà build](https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2),
kiểm SHA-256 `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba` trước khi chạy.
Không sửa PATH hệ thống hoặc cài model/GPU stack toàn máy; dependency Windows nằm trong vùng kiểm thử riêng.

## Dự án dùng model thật

`scripts/live-upgrades-smoke.py` dùng cấu hình CLI hiện có, Edge native, ảnh CLI-vector và FFmpeg,
DB/output riêng; chỉ dừng tại gate publish, không quyết định đăng công khai.

- Fixture đầu tiên `factory-20261016-142952`: các agent thực đã tạo brief, research, script, fact review,
  manifest và metadata. Supervisor ghi **166.779/150.000 token**, mở escalation và hoãn render đúng chính sách.
  Gate đó được giữ nguyên; không sửa event log hoặc giả chữ ký người duyệt để vượt qua.
- Fixture `factory-20261016-143722` có ngân sách thử 500.000 token. Lượt đầu gặp timeout CLI 900 giây,
  lưu deferred/checkpoint để tiếp tục. Tại thời điểm chẩn đoán WSL được cấp khoảng 4 GB RAM và chỉ còn khoảng
  110 MB khả dụng; lượt coverage chạy song song được dừng để giảm tải. Script thử tiếp từ checkpoint với timeout
  lời gọi CLI 120 giây; các timeout này chỉ áp dụng fixture, không đổi cấu hình sản xuất.
- Lượt tiếp tục đã tạo **hai final thật** qua Edge + ảnh CLI-vector + FFmpeg. Final v2 dài **25,58 giây**,
  640×360/25 fps, H.264/AAC 48 kHz stereo; scan hoàn tất, LUFS -13,7, true peak -1 dB, không đoạn đen/im lặng,
  không finding QC. Final v1 bị quality agent yêu cầu sửa lời hứa tiêu đề; v2 phát hiện lỗi thumbnail kể trên.
  Rights agent riêng yêu cầu hồ sơ sử dụng tên “X Studio”; đây là kết luận agent trên dữ liệu JSON được cung cấp.
  Tổng token backend báo **531.454/500.000**, gate escalation còn pending. Kết quả cuối **`needs_attention`**,
  không đạt toàn bộ review sáng tạo, không mở/duyệt publish và không upload.
  Sau sửa thumbnail, lượt quality độc lập trên checkpoint v2 đã pass như ghi ở trên;
  yêu cầu của rights agent và escalation trong workspace gốc vẫn còn để người vận hành xử lý.

Kết quả model, token và trạng thái cuối giữ trong `state.json`/`result.json` của từng fixture;
không suy diễn đã có video khi mới có manifest, hoặc đã review hình/tiếng bằng model khi chỉ có JSON review.

## Phần chưa chạy theo lựa chọn của người vận hành

- Upload YouTube thật: chưa có OAuth token/client trên máy; người vận hành chọn chạy các phần khác trước.
  Các ca restart/chunk cuối/session 404 hiện là test giao thức; không gắn nhãn upload live.
- Sinh video AI bằng GPU: chưa có ComfyUI/workflow/model; người vận hành chọn kiểm công cụ hiện có trước.
- Adapter review đa phương thức tự động: `production.review_command` chưa được cấu hình. Contact sheet/QC đã chạy
  với media thật, nhưng không thay thế một lượt adapter xem hình/nghe tiếng thật.
