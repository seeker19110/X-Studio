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
