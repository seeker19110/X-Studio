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
