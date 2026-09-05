"""Dòng thời gian (phụ đề, chapter) và QC bằng code trên file thật.

Phần tính toán chạy offline; phần đo file có hai lớp: giả lập ffprobe/ffmpeg bằng monkeypatch (chạy ở mọi máy) và
một ca dựng video thật rồi đo lại (bỏ qua khi máy không có ffmpeg).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studio import qc
from studio.bus import InMemoryBus
from studio.events import Chapter, Scene, SceneManifest
from studio.media import MediaConfig, make_media
from studio.qc import qc_video
from studio.renderer import Renderer
from studio.timeline import chapter_time, parse_time, snap_chapters, srt, stamp, timeline


def _manifest(durs=(4.0, 6.0, 5.0), vid="V1"):
    return SceneManifest(video_id=vid, scenes=[
        Scene(scene_id=f"S{i + 1}", order=i, narration=f"Câu của cảnh {i + 1}.", visual_prompt="p", duration_s=d)
        for i, d in enumerate(durs)])


# ---------- dòng thời gian ----------

def test_timeline_matches_how_the_assembler_actually_joins_scenes():
    """Mốc cảnh = tổng (thời lượng + đệm) của các cảnh trước, trừ đi mỗi mối nối một lần chuyển cảnh."""
    cues = timeline(_manifest(), pad=0.35, transition=0.3)
    assert [c.scene_id for c in cues] == ["S1", "S2", "S3"]
    assert [c.start for c in cues] == [0.0, 4.05, 10.1]   # 4+0.35-0.3 ; +6+0.35-0.3
    assert [c.end for c in cues] == [4.0, 10.05, 15.1]
    # không đệm, không chuyển cảnh (provider fake): cảnh nối đuôi nhau
    assert [c.start for c in timeline(_manifest())] == [0.0, 4.0, 10.0]
    # thứ tự dựng do editor chốt được tôn trọng
    assert [c.scene_id for c in timeline(_manifest(), order=["S3", "S1", "S2"])] == ["S3", "S1", "S2"]
    assert timeline(_manifest(), order=["S9"]) == []


def test_srt_is_generated_from_narration_without_speech_recognition():
    text = srt(timeline(_manifest((2.0, 3.0)), pad=0.35, transition=0.3))
    assert text.splitlines()[:3] == ["1", "00:00:00,000 --> 00:00:02,000", "Câu của cảnh 1."]
    assert "2\n00:00:02,050 --> 00:00:05,050\nCâu của cảnh 2." in text
    m = _manifest((2.0,)); m.scenes[0].narration = "   "
    assert srt(timeline(m)) == ""
    assert stamp(3661.5) == "01:01:01,500" and stamp(-5) == "00:00:00,000"


def test_chapter_time_and_parse_roundtrip():
    assert chapter_time(0) == "00:00" and chapter_time(75) == "01:15" and chapter_time(3725) == "1:02:05"
    assert parse_time("00:00") == 0 and parse_time("1:02:05") == 3725 and parse_time("rác") is None


def test_snap_chapters_puts_labels_on_real_scene_starts():
    """seo-optimizer viết nhãn khi chưa có video nên mốc là số đoán; code nắn về đầu cảnh gần nhất."""
    cues = timeline(_manifest((12.0, 15.0, 20.0)), pad=0.35, transition=0.3)  # đầu cảnh: 0 ; 12.05 ; 27.1
    got = snap_chapters([Chapter(time="00:07", label="Mở"), Chapter(time="00:10", label="Giữa"),
                         Chapter(time="00:31", label="Cuối")], cues)
    assert [(c.time, c.label) for c in got] == [("00:00", "Mở"), ("00:12", "Giữa"), ("00:27", "Cuối")]
    # mốc trùng cảnh hoặc quá gần mốc trước (< 10 giây, nền tảng không hiện) bị bỏ, thay vì đẩy lên một danh sách sai
    ngắn = timeline(_manifest((4.0, 6.0, 5.0)), pad=0.35, transition=0.3)      # đầu cảnh: 0 ; 4.05 ; 10.1
    dày = snap_chapters([Chapter(time="00:00", label="A"), Chapter(time="00:04", label="B"),
                         Chapter(time="00:10", label="C")], ngắn)
    assert [c.time for c in dày] == ["00:00", "00:10"]
    assert snap_chapters([], cues) == [] and snap_chapters([Chapter(time="00:00", label="A")], []) != []
    assert snap_chapters([Chapter(time="00:00", label="A"), Chapter(time="xx", label="B")], cues)[0].label == "A"


def test_renderer_writes_captions_next_to_the_final_video(tmp_path):
    bus = InMemoryBus(); r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    m = _manifest((2.0, 3.0), vid="V7"); r.render(m); r.finalize(m)
    caps = [e.payload for e in bus.replay("media-assets", "V7") if e.payload["kind"] == "captions"]
    assert len(caps) == 1 and caps[0]["path"].endswith("captions_v1.srt")
    assert caps[0]["provenance"]["generated_by"] == "studio:srt-from-manifest" and caps[0]["checksum"]
    body = Path(caps[0]["path"]).read_text(encoding="utf-8")
    assert "Câu của cảnh 1." in body and "-->" in body


# ---------- QC: không đo được, và đọc kết quả đo ----------

def test_qc_says_it_cannot_measure_instead_of_guessing(tmp_path, monkeypatch):
    monkeypatch.setattr(qc.shutil, "which", lambda b: None)
    rep = qc_video(tmp_path / "khong-co.mp4")
    assert rep.available is False and rep.blocked is False and rep.checklist() == ["qc:không đo được (thiếu ffprobe)"]
    assert rep.as_dict()["metrics"]["file"].endswith("khong-co.mp4")


def _fake_tools(monkeypatch, probe_json: dict, ffmpeg_err: str = "", probe_code: int = 0):
    monkeypatch.setattr(qc.shutil, "which", lambda b: f"/usr/bin/{b}")

    def run(argv, *a, **k):
        if "ffprobe" in argv[0]:
            return subprocess.CompletedProcess(argv, probe_code, json.dumps(probe_json), "")
        return subprocess.CompletedProcess(argv, 0, "", ffmpeg_err)

    monkeypatch.setattr(qc.subprocess, "run", run)


_OK_PROBE = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "r_frame_rate": "30/1"},
                         {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": "2"}],
             "format": {"duration": "20.00", "size": "1048576"}}


def test_qc_reads_measurements_and_flags_only_real_problems(tmp_path, monkeypatch):
    f = tmp_path / "final.mp4"; f.write_bytes(b"x")
    cap = tmp_path / "c.srt"; cap.write_text("1\n", encoding="utf-8")
    _fake_tools(monkeypatch, _OK_PROBE, "  I:         -14.1 LUFS\n  Peak:       -1.2 dBFS\n")
    rep = qc_video(f, expected_duration_s=20.0, expected_resolution="1920x1080", expected_fps=30, captions=cap)
    assert rep.available and not rep.blocked and rep.findings == []
    assert rep.metrics["lufs"] == -14.1 and rep.metrics["resolution"] == "1920x1080" and rep.metrics["has_audio"]
    assert rep.metrics["fps"] == 30.0 and rep.metrics["duration_drift"] == 0.0
    assert rep.checklist()[0].startswith("qc:1920x1080 30.0fps 20.0s -14.1 LUFS audio=có")


def test_qc_blocks_a_broken_render_and_warns_on_soft_problems(tmp_path, monkeypatch):
    f = tmp_path / "final.mp4"; f.write_bytes(b"x")
    broken = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920, "r_frame_rate": "24/1"}],
              "format": {"duration": "12.00", "size": "100"}}
    _fake_tools(monkeypatch, broken, "  I:         -9.0 LUFS\n  Peak:       0.4 dBFS\n"
                                     "black_start:3 black_end:4.2 black_duration:1.2\n"
                                     "silence_end: 9 | silence_duration: 2.5\n")
    rep = qc_video(f, expected_duration_s=20.0, expected_resolution="1920x1080", expected_fps=30)
    levels = [(x.level, x.location) for x in rep.findings]
    assert rep.blocked and ("block", "final_video") in levels
    texts = " | ".join(x.text for x in rep.findings)
    assert "KHÔNG có luồng âm thanh" in texts and "lệch 40%" in texts and "khác khung yêu cầu" in texts
    assert "24.0 fps khác 30 fps" in texts and "-9.0 LUFS" in texts and "0.4 dBFS" in texts
    assert "1 đoạn hình đen" in texts and "1 khoảng lặng" in texts and "không có phụ đề" in texts
    assert rep.metrics["black_spans"] == [1.2] and rep.metrics["silence_spans"] == [2.5]


def test_qc_checks_the_thumbnail_against_platform_limits(tmp_path, monkeypatch):
    f = tmp_path / "final.mp4"; f.write_bytes(b"x")
    thumb = tmp_path / "A.jpg"; thumb.write_bytes(b"y")
    probe = dict(_OK_PROBE)
    calls = {"n": 0}
    monkeypatch.setattr(qc.shutil, "which", lambda b: f"/usr/bin/{b}")

    def run(argv, *a, **k):
        if "ffprobe" in argv[0]:
            calls["n"] += 1
            if calls["n"] == 1: return subprocess.CompletedProcess(argv, 0, json.dumps(probe), "")
            big = {"streams": [{"codec_type": "video", "width": 640, "height": 360, "r_frame_rate": "0/0"}],
                   "format": {"duration": "0", "size": str(3_000_000)}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(big), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(qc.subprocess, "run", run)
    rep = qc_video(f, thumbnail=thumb)
    texts = " | ".join(x.text for x in rep.findings)
    assert rep.blocked and "3.0 MB vượt giới hạn" in texts and "640x360 khác chuẩn 1280x720" in texts
    assert rep.metrics["thumbnail"] == {"resolution": "640x360", "bytes": 3_000_000}


def test_qc_survives_unreadable_output(tmp_path, monkeypatch):
    f = tmp_path / "final.mp4"; f.write_bytes(b"x")
    _fake_tools(monkeypatch, {}, probe_code=1)
    assert qc_video(f).available is False
    monkeypatch.setattr(qc.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(qc.subprocess, "run",
                        lambda argv, *a, **k: subprocess.CompletedProcess(argv, 0, "{khong-phai-json", ""))
    assert qc_video(f).available is False
    monkeypatch.setattr(qc.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("ffprobe chết")))
    assert qc_video(f).available is False


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="cần ffmpeg + ffprobe")
def test_qc_measures_a_real_render_end_to_end(tmp_path):
    """Dựng thật rồi đo lại: khung, thời lượng, âm lượng chuẩn hoá, phụ đề — đúng những gì reviewer không tự thấy được."""
    cfg = MediaConfig(output_dir=tmp_path, video={"provider": "ffmpeg", "fps": 24, "resolution": "640x360"})
    bus = InMemoryBus(); r = Renderer(bus, make_media(cfg), tmp_path)
    m = _manifest((2.0, 2.0), vid="V9"); r.render(m)
    final = r.finalize(m)
    caps = next(e.payload["path"] for e in bus.replay("media-assets", "V9") if e.payload["kind"] == "captions")
    rep = qc_video(Path(final.path), expected_duration_s=final.duration_s or 0.0, expected_resolution="640x360",
                   expected_fps=24, captions=Path(caps))
    assert rep.available and rep.metrics["resolution"] == "640x360" and rep.metrics["has_audio"]
    assert rep.metrics["duration_drift"] < 0.05 and rep.metrics["sample_rate"] == 48000
    assert not rep.blocked and all(f.location != "captions" for f in rep.findings)
    assert any("qc:640x360 24.0fps" in line for line in rep.checklist())


# ---------- QC từng cảnh: dữ liệu thật cho editor ----------

def _ffmpeg_out(monkeypatch, image_err: str = "", audio_err: str = "", duration: float | None = 3.0):
    monkeypatch.setattr(qc.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(qc, "probe_duration", lambda p: duration)

    def run(argv, *a, **k):
        err = image_err if "-vf" in argv else audio_err
        return subprocess.CompletedProcess(argv, 0, "", err)

    monkeypatch.setattr(qc.subprocess, "run", run)


_GOOD_IMG = ("lavfi.signalstats.YAVG=125.5\nlavfi.signalstats.YLOW=16\nlavfi.signalstats.YHIGH=235\n")


def test_scene_qc_is_silent_when_the_scene_is_healthy(tmp_path, monkeypatch):
    img = tmp_path / "S1.png"; img.write_bytes(b"x"); aud = tmp_path / "S1.wav"; aud.write_bytes(b"y")
    _ffmpeg_out(monkeypatch, _GOOD_IMG, "")
    r = qc.qc_scene(img, aud, "một hai ba bốn năm sáu bảy tám", 3.0)
    assert r["findings"] == []
    assert r["metrics"] == {"image": {"yavg": 125.5, "ylow": 16.0, "yhigh": 235.0, "contrast": 219.0},
                            "audio": {"duration_s": 3.0, "silence_s": 0.0}}


def test_scene_qc_catches_dark_flat_images_and_broken_narration(tmp_path, monkeypatch):
    """Đúng những thứ editor được yêu cầu bắt ("ảnh tối", "đọc vấp") mà trước đây nó không có cách nào thấy."""
    img = tmp_path / "S2.png"; img.write_bytes(b"x"); aud = tmp_path / "S2.wav"; aud.write_bytes(b"y")
    dark = "lavfi.signalstats.YAVG=25\nlavfi.signalstats.YLOW=25\nlavfi.signalstats.YHIGH=25\n"
    _ffmpeg_out(monkeypatch, dark, "silence_start: 0 | silence_end: 3 | silence_duration: 3.0")
    r = qc.qc_scene(img, aud, " ".join(["từ"] * 25), 3.0)
    text = " | ".join(f["text"] for f in r["findings"])
    assert "ảnh gần như đen (độ sáng 25.0/255)" in text and "tương phản 0.0" in text
    assert "giọng đọc gần như im lặng (3.0s/3.0s)" in text and "TTS nuốt phần cuối" in text
    assert next(f["level"] for f in r["findings"] if f["location"] == "scene_audio") == "block"
    # ảnh cháy sáng
    _ffmpeg_out(monkeypatch, "lavfi.signalstats.YAVG=240\nlavfi.signalstats.YLOW=200\nlavfi.signalstats.YHIGH=255\n")
    assert "ảnh gần như trắng" in " ".join(f["text"] for f in qc.qc_scene(img, aud, "một hai ba", 3.0)["findings"])


def test_scene_qc_flags_audio_that_does_not_match_the_manifest(tmp_path, monkeypatch):
    img = tmp_path / "S3.png"; img.write_bytes(b"x"); aud = tmp_path / "S3.wav"; aud.write_bytes(b"y")
    _ffmpeg_out(monkeypatch, _GOOD_IMG, "", duration=0.6)
    r = qc.qc_scene(img, aud, "một hai ba bốn năm", 3.0)
    assert "thời lượng thật 0.6s lệch manifest (3.0s)" in " ".join(f["text"] for f in r["findings"])
    _ffmpeg_out(monkeypatch, _GOOD_IMG, "", duration=0.0)
    assert any(f["level"] == "block" and "rỗng" in f["text"] for f in qc.qc_scene(img, aud, "x", 0)["findings"])


def test_scene_qc_reports_missing_files_and_skips_without_ffmpeg(tmp_path, monkeypatch):
    _ffmpeg_out(monkeypatch)
    r = qc.qc_scene(None, tmp_path / "khong-co.wav", "x", 1.0)
    assert [(f["level"], f["location"]) for f in r["findings"]] == [("block", "scene_image"), ("block", "scene_audio")]
    m = _manifest((2.0,))
    assert qc.qc_scenes(m) != {}                       # có ffmpeg (giả) → có đo
    monkeypatch.setattr(qc.shutil, "which", lambda b: None)
    assert qc.qc_scenes(m) == {}                        # không có ffmpeg → rỗng, editor vẫn chạy như cũ
    assert qc.measure_image(tmp_path / "x.png") == {} and qc.measure_silence(tmp_path / "x.wav") == 0.0


def test_editor_receives_scene_measurements_in_its_payload(tmp_path):
    """Nối dây: editor nhận `scene_qc` cùng manifest và asset, nên nó quyết định sửa cảnh nào bằng số đo."""
    from studio.events import Envelope
    from studio.fakes import make_scripted_client
    from studio.media import MediaConfig, make_media
    from studio.orchestrator import Orchestrator, _with_manifest_assets

    bus = InMemoryBus()
    o = Orchestrator(bus, make_scripted_client(), media=make_media(MediaConfig(output_dir=tmp_path)), out_dir=tmp_path)
    m = _manifest((2.0, 3.0), vid="CH1-V1")
    o.renderer.render(m)
    draft = next(e for e in bus.replay("media-assets", "CH1-V1") if e.payload["kind"] == "draft_video")
    extra = _with_manifest_assets(Envelope(topic="media-assets", key="CH1-V1", actor="renderer", payload=draft.payload), o)
    assert set(extra) == {"manifest", "scene_assets", "scene_qc", "repair_rounds_used", "repair_rounds_max"}
    if shutil.which("ffmpeg"):
        assert set(extra["scene_qc"]) == {"S1", "S2"}
        assert "metrics" in extra["scene_qc"]["S1"] and "findings" in extra["scene_qc"]["S1"]
    else:
        assert extra["scene_qc"] == {}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")
def test_scene_qc_measures_real_files(tmp_path):
    """Đo file thật: ảnh gần đen và giọng đọc im lặng phải bị bắt, cảnh lành lặn thì không báo gì."""
    def lavfi(spec: str, out: Path, *args: str) -> Path:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", spec, *args, str(out)], check=True)
        return out

    dark = lavfi("color=c=0x0a0a0a:s=320x180:d=1", tmp_path / "dark.png", "-frames:v", "1")
    good = lavfi("testsrc=size=320x180:d=1:rate=1", tmp_path / "good.png", "-frames:v", "1")
    silent = lavfi("anullsrc=r=24000:cl=mono:d=3", tmp_path / "silent.wav", "-ar", "24000")
    speech = lavfi("anoisesrc=d=3:c=pink:a=0.2,lowpass=f=3400", tmp_path / "speech.wav", "-ar", "24000")
    assert qc.qc_scene(good, speech, "một hai ba bốn năm sáu bảy tám", 3.0)["findings"] == []
    xấu = qc.qc_scene(dark, silent, "một hai ba bốn năm sáu bảy tám", 3.0)
    text = " | ".join(f["text"] for f in xấu["findings"])
    assert "ảnh gần như đen" in text and "giọng đọc gần như im lặng" in text
    assert xấu["metrics"]["image"]["yavg"] < 40 and xấu["metrics"]["audio"]["silence_s"] >= 2.9
