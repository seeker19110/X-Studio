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
from studio.media import MediaConfig, MediaResult, make_media
from studio.qc import qc_video
from studio.renderer import Renderer
from studio.sandbox import SubprocessSandbox
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

    monkeypatch.setattr(qc, "qc_sandbox", lambda: SubprocessSandbox(runner=run))


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


@pytest.mark.parametrize("size_bytes, level, text", [
    # 3 MB: trên mục tiêu 2 MB của renderer (warn, nền tảng nhận ≤ 50 MB) nhưng không chặn đăng
    (3_000_000, "warn", "3.0 MB vượt mục tiêu 2 MB"),
    # 51 MB: YouTube từ chối → chặn
    (51_000_000, "block", "51.0 MB vượt giới hạn nền tảng 50 MB"),
])
def test_qc_checks_the_thumbnail_against_platform_limits(tmp_path, monkeypatch, size_bytes, level, text):
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
                   "format": {"duration": "0", "size": str(size_bytes)}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(big), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(qc, "qc_sandbox", lambda: SubprocessSandbox(runner=run))
    rep = qc_video(f, thumbnail=thumb)
    texts = " | ".join(x.text for x in rep.findings)
    assert text in texts and "640x360 khác chuẩn 1280x720" in texts
    assert rep.metrics["thumbnail"] == {"resolution": "640x360", "bytes": size_bytes}
    assert {x.level for x in rep.findings if x.location == "thumbnail"} == {level, "warn"}


def test_qc_survives_unreadable_output(tmp_path, monkeypatch):
    f = tmp_path / "final.mp4"; f.write_bytes(b"x")
    _fake_tools(monkeypatch, {}, probe_code=1)
    assert qc_video(f).available is False
    monkeypatch.setattr(qc.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(qc, "qc_sandbox", lambda: SubprocessSandbox(
        runner=lambda argv, *a, **k: subprocess.CompletedProcess(argv, 0, "{khong-phai-json", "")))
    assert qc_video(f).available is False
    monkeypatch.setattr(qc, "qc_sandbox", lambda: SubprocessSandbox(
        runner=lambda *a, **k: (_ for _ in ()).throw(OSError("ffprobe chết"))))
    assert qc_video(f).available is False


@pytest.mark.parametrize("payload", [[], "not an object", {"streams": [None]},
                                    {"streams": [{"codec_type": "video", "r_frame_rate": "bad/1"}]},
                                    {"format": {"duration": "nan"}}, {"format": {"duration": "Infinity"}},
                                    {"format": {"size": "bad"}}])
def test_qc_treats_malformed_probe_metrics_as_unavailable(tmp_path, monkeypatch, payload):
    path = tmp_path / "final.mp4"; path.write_bytes(b"media")
    _fake_tools(monkeypatch, payload)
    assert qc.probe(path) is None
    assert qc_video(path).available is False


def test_qc_blocks_audio_only_or_zero_duration_final(tmp_path, monkeypatch):
    path = tmp_path / "final.mp4"; path.write_bytes(b"media")
    _fake_tools(monkeypatch, {"streams": [{"codec_type": "audio"}], "format": {"duration": "0"}},
                "  I: -14.0 LUFS\n  Peak: -1.0 dBFS\n")
    report = qc_video(path)
    assert report.blocked
    assert any(f.level == "block" and f.location == "final_video" for f in report.findings)


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

    monkeypatch.setattr(qc, "qc_sandbox", lambda: SubprocessSandbox(runner=run))


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


@pytest.mark.parametrize("code,log", [
    (1, "  I: -14.0 LUFS\n  Peak: -1.0 dBFS\n"),
    (0, ""),
    (0, "  I: -14.0 LUFS\n"),
    (0, "x" * 1_000_000 + "\n  I: -14.0 LUFS\n  Peak: -1.0 dBFS\n"),
], ids=["failed-process", "empty", "missing-peak", "truncated"])
def test_qc_blocks_incomplete_scan_even_with_readable_probe(tmp_path, monkeypatch, code, log):
    video = tmp_path / "final.mp4"; video.write_bytes(b"video")
    _fake_tools(monkeypatch, _OK_PROBE)
    monkeypatch.setattr(qc, "_run", lambda args: (0, json.dumps(_OK_PROBE), "")
                        if "ffprobe" in args[0] else (code, "", log))
    report = qc_video(video)
    assert report.blocked
    assert report.metrics["scan_complete"] is False
    assert any(f.level == "block" and f.location == "qc_scan" for f in report.findings)


def test_qc_blocks_missing_ffmpeg(tmp_path, monkeypatch):
    video = tmp_path / "final.mp4"; video.write_bytes(b"video")
    _fake_tools(monkeypatch, _OK_PROBE)
    monkeypatch.setattr(qc.shutil, "which", lambda name: "/usr/bin/ffprobe" if name == "ffprobe" else None)
    assert qc_video(video).blocked


def test_qc_scan_uses_bounded_threads_and_quiet_loudness_logging(monkeypatch):
    commands = []
    monkeypatch.setattr(qc.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    def run(args):
        commands.append(args)
        return 0, "", "  I: -14.0 LUFS\n  Peak: -1.0 dBFS\n"
    monkeypatch.setattr(qc, "_run", run)
    assert qc.measure(Path("video.mp4"))["scan_complete"] is True
    command = commands[0]
    assert "framelog=verbose" in command[command.index("-af") + 1]
    assert command[command.index("-filter_threads") + 1] == "1"
    assert "-xerror" in command


# ---------- nguy cơ "slideshow": khung hình đứng yên quá lâu (docs/standards.md, ADR-0016) ----------

def test_qc_warns_when_the_picture_freezes_longer_than_the_static_limit(tmp_path, monkeypatch):
    f = tmp_path / "final.mp4"; f.write_bytes(b"x")
    cap = tmp_path / "c.srt"; cap.write_text("1\n", encoding="utf-8")
    log = ("  I:         -14.1 LUFS\n  Peak:       -1.2 dBFS\n"
           "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 2.5\n"
           "[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 9.25\n"
           "[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 11.75\n")
    _fake_tools(monkeypatch, _OK_PROBE, log)
    rep = qc_video(f, expected_duration_s=20.0, expected_resolution="1920x1080", expected_fps=30, captions=cap)
    assert rep.metrics["freeze_spans"] == [9.25]
    assert [(x.level, x.location) for x in rep.findings] == [("warn", "motion")]
    assert "đứng yên" in rep.findings[0].text and "9.25s" in rep.findings[0].text and not rep.blocked


def test_qc_closes_a_freeze_that_runs_to_the_end_of_the_video(tmp_path, monkeypatch):
    """ffmpeg chỉ in `freeze_start` khi hình đứng yên tới hết file — đoạn cuối phải được tính đến hết thời lượng,
    nếu không đúng loại slideshow tệ nhất (cảnh cuối đứng im) lại lọt."""
    f = tmp_path / "final.mp4"; f.write_bytes(b"x")
    _fake_tools(monkeypatch, _OK_PROBE, "  I: -14.0 LUFS\n  Peak: -1.0 dBFS\n"
                                        "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 8\n")
    rep = qc_video(f, expected_duration_s=20.0)
    assert rep.metrics["freeze_spans"] == [12.0]
    assert any(x.location == "motion" and x.level == "warn" for x in rep.findings)


def test_qc_freeze_check_can_be_turned_off_and_is_bounded_by_the_limit(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(qc.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    def run(args):
        commands.append(args)
        return 0, "", "  I: -14.0 LUFS\n  Peak: -1.0 dBFS\n"

    monkeypatch.setattr(qc, "_run", run)
    assert qc.measure(Path("video.mp4"), duration_s=30.0)["freeze_spans"] == []
    assert f"freezedetect=n=-60dB:d={qc.MAX_STATIC_S:g}" in commands[0][commands[0].index("-vf") + 1]
    monkeypatch.setattr(qc, "MAX_STATIC_S", 0.0)
    out = qc.measure(Path("video.mp4"))
    assert "freezedetect" not in commands[1][commands[1].index("-vf") + 1] and "freeze_spans" not in out


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="cần ffmpeg + ffprobe")
def test_qc_catches_a_real_still_image_slideshow_but_not_ken_burns(tmp_path, monkeypatch):
    """Dựng thật một cảnh dài: ảnh đứng im (`motion: none`) bị bắt, ảnh có chuyển động Ken Burns thì không.
    Ảnh của provider `fake` là một màu (Ken Burns trên nền phẳng đúng là không thấy chuyển động) nên cảnh dùng ảnh
    có họa tiết thật từ `testsrc`."""
    class Textured:
        def generate(self, prompt: str, size: str, out: Path) -> MediaResult:
            out.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180",
                            "-frames:v", "1", str(out)], check=True)
            return MediaResult(out, "test", "testsrc")

    monkeypatch.setattr(qc, "MAX_STATIC_S", 3.0)
    for motion, frozen in (("none", True), ("auto", False)):
        out = tmp_path / motion
        cfg = MediaConfig(output_dir=out, video={"provider": "ffmpeg", "fps": 12, "resolution": "320x180",
                                                 "motion": motion, "layout":"full_bleed", "burn_captions":False})
        suite = make_media(cfg); suite.image = Textured()
        bus = InMemoryBus(); r = Renderer(bus, suite, out)
        m = SceneManifest(video_id="VS", scenes=[Scene(
            scene_id="S1", order=0, narration=" ".join(["chữ"] * 12), visual_prompt="p", duration_s=4.8)])
        r.render(m); final = r.finalize(m)
        rep = qc_video(Path(final.path), expected_duration_s=final.duration_s or 0.0)
        assert bool(rep.metrics["freeze_spans"]) is frozen, (motion, rep.metrics)
        assert any(x.location == "motion" for x in rep.findings) is frozen


# ---------- ASR đối chiếu lời đọc (FFmpeg ≥ 8 filter `whisper`, ADR-0019) ----------

SRT_HEARD = ("1\n00:00:00,000 --> 00:00:03,500\nCâu của cảnh một.\n\n"
             "2\n00:00:04.500 --> 00:00:09.000\nhoàn toàn\nkhác hẳn rồi\n\n"
             "3\nkhông có mốc\n\n4\n00:00:12,000 --> 00:00:14,000\n\n")
NARRATION = [("S1", 0.0, 4.0, "Câu của cảnh một"), ("S2", 4.0, 10.0, "Câu của cảnh hai"), ("S3", 10.0, 15.0, "  ")]


def test_wer_and_srt_parsing():
    assert qc.wer("Xin chào các bạn", "xin chào, các bạn!") == 0.0
    assert qc.wer("a b c d", "a c d") == 0.25 and qc.wer("a b", "x y z w") == 1.0
    assert qc.wer("", "x") == 1.0 and qc.wer("", "") == 0.0
    assert qc.parse_srt(SRT_HEARD) == [(0.0, 3.5, "Câu của cảnh một."), (4.5, 9.0, "hoàn toàn khác hẳn rồi")]
    assert qc.narration_wer(qc.parse_srt(SRT_HEARD), NARRATION) == {"S1": 0.0, "S2": 1.0}


def _asr_tools(monkeypatch, tmp_path, *, filters: str = "  ... whisper           A->A       Transcribe audio\n",
               whisper_code: int = 0):
    model = tmp_path / "ggml-base.bin"; model.write_bytes(b"ggml")
    monkeypatch.setenv("STUDIO_QC_ASR_MODEL", str(model))
    monkeypatch.setenv("STUDIO_QC_ASR_LANGUAGE", "vi")
    monkeypatch.setattr(qc.shutil, "which", lambda b: f"/usr/bin/{b}")
    calls: list[list[str]] = []

    def run(argv, *a, **k):
        calls.append(argv)
        if "ffprobe" in argv[0]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(_OK_PROBE), "")
        if argv[-1] == "-filters":
            return subprocess.CompletedProcess(argv, 0, filters, "")
        if any(arg.startswith("whisper=") for arg in argv):
            return subprocess.CompletedProcess(argv, whisper_code, SRT_HEARD, "")
        return subprocess.CompletedProcess(argv, 0, "", "  I:         -14.1 LUFS\n  Peak:       -1.2 dBFS\n")

    monkeypatch.setattr(qc, "qc_sandbox", lambda: SubprocessSandbox(runner=run))
    return calls, model


def test_qc_asr_compares_narration_per_scene(tmp_path, monkeypatch):
    f = tmp_path / "final.mp4"; f.write_bytes(b"x")
    calls, model = _asr_tools(monkeypatch, tmp_path)
    rep = qc_video(f, narration=NARRATION)
    assert rep.metrics["asr"] == {"model": "ggml-base.bin", "language": "vi", "wer": 0.5, "segments": 2,
                                  "scenes": {"S1": 0.0, "S2": 1.0}}
    bad = [x for x in rep.findings if x.location == "narration"]
    assert [x.level for x in bad] == ["warn"] and "cảnh S2" in bad[0].text and "0%" in bad[0].text
    assert not rep.blocked
    assert any("asr_wer=0.5" in line for line in rep.checklist())
    argv = next(a for a in calls if any(arg.startswith("whisper=") for arg in a))
    spec = next(arg for arg in argv if arg.startswith("whisper="))
    assert f"model={model}" in spec and ":language=vi:" in spec and "format=srt" in spec
    assert spec.endswith("destination=pipe\\:1") and "-vn" in argv and argv[-2:] == ["null", "-"]


def test_qc_asr_is_silent_without_model_or_filter_and_warns_when_ffmpeg_fails(tmp_path, monkeypatch):
    f = tmp_path / "final.mp4"; f.write_bytes(b"x")
    monkeypatch.delenv("STUDIO_QC_ASR_MODEL", raising=False)
    _fake_tools(monkeypatch, _OK_PROBE, "  I:         -14.1 LUFS\n  Peak:       -1.2 dBFS\n")
    rep = qc_video(f, narration=NARRATION)
    assert "asr" not in rep.metrics and not [x for x in rep.findings if x.location == "narration"]
    # FFmpeg 6/7: không có filter whisper → im lặng, không phải warn
    calls, _ = _asr_tools(monkeypatch, tmp_path, filters="  ... silencedetect  A->A\n")
    rep = qc_video(f, narration=NARRATION)
    assert "asr" not in rep.metrics and not any(any(a.startswith("whisper=") for a in c) for c in calls)
    # model khai nhưng file không có → im lặng
    monkeypatch.setenv("STUDIO_QC_ASR_MODEL", str(tmp_path / "missing.bin"))
    assert "asr" not in qc_video(f, narration=NARRATION).metrics
    # filter có, lượt chạy lỗi → warn, không chặn
    _asr_tools(monkeypatch, tmp_path, whisper_code=1)
    rep = qc_video(f, narration=NARRATION)
    assert [x.text for x in rep.findings if x.location == "narration"] == ["ASR (ffmpeg whisper) thất bại; chưa đối chiếu lời đọc"]
    assert "asr" not in rep.metrics and not rep.blocked
    # không có lời đọc nào để so (narration rỗng) → không gọi ASR
    calls, _ = _asr_tools(monkeypatch, tmp_path)
    qc_video(f, narration=[])
    assert not any(a[-1] == "-filters" for a in calls)


def test_orchestrator_hands_narration_cues_to_qc(tmp_path, monkeypatch):
    from studio import orchestrator
    from studio.events import MediaAsset
    from studio.orchestrator import StepResult
    from test_orchestrator import _to_publish_gate
    bus, o = _to_publish_gate(tmp_path)
    vid = "CH1-V1"
    seen: dict = {}
    monkeypatch.setattr(orchestrator, "qc_video", lambda path, **kw: seen.update(kw) or qc.QCReport())
    m = SceneManifest.model_validate(bus.latest("scene-manifests", vid).payload)
    final = next(a for a in reversed([e.payload for e in bus.replay("media-assets", vid)]) if a["kind"] == "final_video")
    o._after_final(m, None, MediaAsset.model_validate(final), StepResult("e1", "media-assets", vid))
    cues = o.renderer.cues(m, None)
    assert seen["narration"] == [(c.scene_id, c.start, c.end, c.text) for c in cues] and len(cues) == len(m.scenes)
