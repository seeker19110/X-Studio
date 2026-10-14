from itertools import pairwise

from studio.timeline import Cue, caption_pages, srt, stamp


def test_pages_preserve_vietnamese_words_and_scene_boundaries():
    text = "Đồng hành cùng bạn học mỗi ngày. Kiến thức mở ra cơ hội mới cho tất cả mọi người."
    cues = [Cue("S1", 1, 12, text), Cue("S2", 12.05, 14, "Cảnh tiếp theo.")]
    pages = caption_pages(cues, max_chars=24, max_lines=2)
    assert len(pages) > 2
    assert " ".join(p.text.replace("\n", " ") for p in pages) == text + " Cảnh tiếp theo."
    assert all(len(p.text.splitlines()) <= 2 for p in pages)
    assert all(len(line) <= 24 for p in pages for line in p.text.splitlines())
    assert pages[0].start == 1 and pages[-2].end == 12 and pages[-1].start == 12.05
    assert all(a.end <= b.start for a, b in pairwise(pages))
    assert "1000" not in stamp(59.9999) and stamp(59.9999) == "00:01:00,000"


def test_pages_handle_empty_tiny_and_unbroken_text_without_losing_content():
    assert caption_pages([Cue("S0", 0, 0, ""), Cue("S1", 0, 0, "skip")]) == []
    word = "x" * 80
    pages = caption_pages([Cue("S1", 0, .1, word)], max_chars=20)
    assert "".join(p.text.replace("\n", "") for p in pages) == word
    assert pages[-1].end == .1
    # Millisecond allocation must never create zero-length cues, even for tiny scenes.
    pages = caption_pages([Cue("S1", 0, .002, "one two three four five six")], max_chars=3)
    assert all(p.end > p.start for p in pages)


def test_srt_numbers_are_contiguous_after_empty_cues():
    assert srt([Cue("S1", 0, 1, ""), Cue("S2", 1, 2, "visible")]).startswith("1\n")


def test_pages_use_real_word_times_when_they_match_the_narration():
    """Mốc từng từ từ TTS (ElevenLabs /with-timestamps, edge WordBoundary): trang phụ đề bắt đầu đúng lúc từ đầu
    được đọc và hết đúng lúc từ cuối dứt, không chia theo độ dài chữ."""
    text = "một hai ba bốn năm sáu"
    words = [(0.1, 0.3, "một"), (0.4, 0.6, "hai"), (0.7, 0.9, "ba"), (2.0, 2.2, "bốn"), (2.3, 2.5, "năm"), (2.6, 3.4, "sáu")]
    pages = caption_pages([Cue("S1", 10, 14, text)], max_chars=7, max_lines=1, words={"S1": words})
    assert [p.text for p in pages] == ["một hai", "ba bốn", "năm sáu"]
    assert [(p.start, p.end) for p in pages] == [(10.1, 10.6), (10.7, 12.2), (12.3, 13.4)]
    # lời đọc khác mốc (provider chuẩn hoá chữ số) → quay về chia theo tỷ lệ, không gán sai từ
    fallback = caption_pages([Cue("S1", 10, 14, text)], max_chars=7, max_lines=1, words={"S1": words[:-1]})
    assert fallback == caption_pages([Cue("S1", 10, 14, text)], max_chars=7, max_lines=1)
    # mốc vượt cuối cảnh thì kẹp lại; từ dài hơn một dòng vẫn đứng riêng
    long = caption_pages([Cue("S2", 0, 1, "siêudàiquá ngắn")], max_chars=4, max_lines=1,
                         words={"S2": [(0.0, 0.5, "siêudàiquá"), (0.6, 9.0, "ngắn")]})
    assert [(p.text, p.start, p.end) for p in long] == [("siêudàiquá", 0.0, 0.5), ("ngắn", 0.6, 1.0)]


def test_word_times_match_narration_ignoring_punctuation_and_case():
    """edge-tts thật (14/10/2026) trả WordBoundary đã bỏ dấu câu: "bạn" cho "bạn,". Khớp theo từ đã chuẩn hoá, còn
    chữ trên phụ đề vẫn là lời đọc gốc có dấu câu."""
    text = "Xin chào các bạn, hôm nay."
    words = [(0.125, 0.312, "Xin"), (0.312, 0.537, "chào"), (0.537, 0.712, "các"), (0.713, 1.062, "bạn"),
             (1.312, 1.488, "hôm"), (1.488, 1.7, "NAY")]
    pages = caption_pages([Cue("S1", 0, 2, text)], max_chars=14, max_lines=1, words={"S1": words})
    assert [p.text for p in pages] == ["Xin chào các", "bạn, hôm nay."]
    assert [(p.start, p.end) for p in pages] == [(0.125, 0.712), (0.713, 1.7)]


def test_karaoke_ass_highlights_each_word_at_its_real_time():
    """Phụ đề karaoke đốt cứng (ASS `\\k`): mỗi trang một Dialogue, mỗi từ đổi màu đúng lúc được đọc. Cảnh không có mốc
    vẫn có phụ đề thường (không thẻ `\\k`) để không mất chữ."""
    from studio.timeline import karaoke_ass
    words = [(0.1, 0.3, "Xin"), (0.4, 0.6, "chào"), (0.6, 1.0, "bạn")]
    cues = [Cue("S1", 10, 12, "Xin chào bạn."), Cue("S2", 12, 14, "Cảnh không có mốc.")]
    ass = karaoke_ass(cues, {"S1": words}, "1920x1080", max_chars=42)
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass and "Style: Karaoke,DejaVu Sans,59," in ass
    lines = [x for x in ass.splitlines() if x.startswith("Dialogue:")]
    assert lines[0] == r"Dialogue: 0,0:00:10.10,0:00:11.00,Karaoke,,0,0,0,,{\k30}Xin {\k20}chào {\k40}bạn."
    assert lines[1].startswith("Dialogue: 0,0:00:12.00,0:00:14.00,Karaoke,,0,0,0,,") and r"\k" not in lines[1]
    assert lines[1].endswith("Cảnh không có mốc.")
    # chữ đặc biệt của ASS không phá dòng; xuống dòng thành \N
    odd = karaoke_ass([Cue("S1", 0, 1, "a{b} c")], {"S1": [(0, .5, "a{b}"), (.5, 1, "c")]}, "1080x1920", max_chars=4)
    assert r"{\k50}a(b)\N{\k50}c" in odd and "PlayResX: 1080" in odd
    assert karaoke_ass([], {}, "1280x720").count("Dialogue:") == 0
    # cảnh không lời hoặc khoảng rỗng không sinh dòng nào
    assert karaoke_ass([Cue("S0", 0, 1, "  "), Cue("S1", 2, 2, "chữ")], {}, "1280x720").count("Dialogue:") == 0
