"""Các nhánh còn hở sau khi đo coverage: chúng chạy được mà không cần ffmpeg/ffprobe thật trên PATH,
nên không bị `skipif` bỏ qua trong CI tối giản."""
import subprocess
import urllib.error
from pathlib import Path

import pytest

from studio import media, qc
from studio.media import FFmpegAssembler, MediaConfig, MediaError


def _stub_which(monkeypatch, mapping):
    monkeypatch.setattr(media.shutil, "which", lambda b: mapping.get(b))


# ---------- chọn provider ----------

def test_require_fake_tu_choi_provider_video_la():
    with pytest.raises(MediaError, match="provider lạ `veo`"):
        media._require_fake("video", {"provider": "veo"}, object())


def test_make_media_video_provider_la_bao_loi():
    with pytest.raises(MediaError, match="video: provider lạ"):
        media.make_media(MediaConfig(video={"provider": "veo"}))


# ---------- font ----------

def test_find_font_uu_tien_khai_bao_trong_media_yaml(tmp_path, monkeypatch):
    f = tmp_path / "co-dau.ttf"; f.write_bytes(b"ttf")
    monkeypatch.setattr(media, "FONT_CANDIDATES", ())
    assert media.find_font({"font": str(f)}) == f


def test_find_font_roi_ve_font_he_thong_va_None_khi_khong_co(tmp_path, monkeypatch):
    sys_font = tmp_path / "he-thong.ttf"; sys_font.write_bytes(b"ttf")
    monkeypatch.setattr(media, "FONT_CANDIDATES", (str(tmp_path / "khong-co.ttf"), str(sys_font)))
    assert media.find_font({"font": ""}) == sys_font
    assert media.find_font(None) == sys_font
    monkeypatch.setattr(media, "FONT_CANDIDATES", (str(tmp_path / "khong-co.ttf"),))
    assert media.find_font({}) is None


# ---------- probe_duration ----------

def test_probe_duration_None_khi_khong_co_ffprobe(monkeypatch, tmp_path):
    _stub_which(monkeypatch, {})
    assert media.probe_duration(tmp_path / "a.mp3") is None


def test_probe_duration_nuot_loi_cua_ffprobe(monkeypatch, tmp_path):
    _stub_which(monkeypatch, {"ffprobe": "/usr/bin/ffprobe"})

    def boom(*a, **k): raise subprocess.TimeoutExpired("ffprobe", 30)
    monkeypatch.setattr(media.subprocess, "run", boom)
    assert media.probe_duration(tmp_path / "a.mp3") is None


def test_probe_duration_nuot_stdout_khong_phai_so(monkeypatch, tmp_path):
    _stub_which(monkeypatch, {"ffprobe": "/usr/bin/ffprobe"})
    monkeypatch.setattr(media.subprocess, "run",
                        lambda argv, *a, **k: subprocess.CompletedProcess(argv, 0, "N/A", ""))
    assert media.probe_duration(tmp_path / "a.mp3") is None


# ---------- tải ảnh ----------

def test_download_bien_loi_mang_thanh_MediaError(monkeypatch):
    monkeypatch.setattr(media, "check_url", lambda u: u)

    def boom(*a, **k): raise urllib.error.URLError("mạng chết")
    monkeypatch.setattr(media.urllib.request, "urlopen", boom)
    with pytest.raises(MediaError, match="lỗi tải ảnh"):
        media._download("https://vi.du/anh.png")


def test_safe_map_giu_nguyen_placeholder_la():
    assert "{a}-{la}".format_map(media._SafeMap(a="x")) == "x-{la}"


# ---------- scale ----------

def test_scale_contain_them_vien_den(monkeypatch):
    _stub_which(monkeypatch, {"ffmpeg": "/usr/bin/ffmpeg"})
    vf = FFmpegAssembler(fit="contain")._scale("1280", "720")
    assert "force_original_aspect_ratio=decrease" in vf and "pad=1280:720" in vf


# ---------- finish_thumbnail ----------

def _assembler_ghi_file(monkeypatch, out: Path, sizes: list[int]):
    """FFmpegAssembler với `subprocess.run` giả: mỗi lần chạy ghi ra `out` với kích thước lấy từ `sizes`."""
    _stub_which(monkeypatch, {"ffmpeg": "/usr/bin/ffmpeg"})
    runs: list[list[str]] = []

    def fake_run(argv, *a, **k):
        runs.append(argv)
        out.write_bytes(b"\xff" * sizes[min(len(runs) - 1, len(sizes) - 1)])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    return FFmpegAssembler(), runs


def test_finish_thumbnail_ve_chu_phu_va_don_thu_muc_tam(tmp_path, monkeypatch):
    font = tmp_path / "f.ttf"; font.write_bytes(b"ttf")
    src = tmp_path / "nen.png"; src.write_bytes(b"png")
    out = tmp_path / "ra" / "thumb.jpg"
    a, runs = _assembler_ghi_file(monkeypatch, out, [1000])
    a.font = font
    r = a.finish_thumbnail(src, out, text="Bốn từ ngắn gọn")
    assert r.provider == "ffmpeg" and r.model == "mjpeg" and r.path == out
    vf = runs[0][runs[0].index("-vf") + 1]
    assert "drawtext=textfile=t.txt:fontfile=f.ttf" in vf
    assert "chữ=" in r.notes and "KB" in r.notes and "VƯỢT" not in r.notes
    assert len(runs) == 1  # đạt ngay ở q=3, không thử tiếp
    assert list(out.parent.glob(".studio-*")) == []  # thư mục tạm đã xoá


def test_finish_thumbnail_ha_chat_luong_roi_bao_vuot_gioi_han(tmp_path, monkeypatch):
    src = tmp_path / "nen.png"; src.write_bytes(b"png")
    out = tmp_path / "thumb.jpg"
    a, runs = _assembler_ghi_file(monkeypatch, out, [9_000_000])
    a.font = None
    monkeypatch.setattr(media, "find_font", lambda video=None: None)
    r = a.finish_thumbnail(src, out, text="Một hai ba bốn năm sáu bảy tám chín mười mười một", max_bytes=2_000_000)
    assert len(runs) == 3  # thử cả q=3, 6, 9 mà vẫn quá nặng
    assert "CẮT" in r.notes and "KHÔNG phủ được chữ" in r.notes and "VƯỢT 2 MB" in r.notes


def test_finish_thumbnail_khong_chu_thi_khong_drawtext(tmp_path, monkeypatch):
    src = tmp_path / "nen.png"; src.write_bytes(b"png")
    out = tmp_path / "thumb.jpg"
    a, runs = _assembler_ghi_file(monkeypatch, out, [1000])
    r = a.finish_thumbnail(src, out, text="   ")
    assert "drawtext" not in runs[0][runs[0].index("-vf") + 1] and "chữ=" not in r.notes


# ---------- qc ----------

def test_qc_thumbnail_khong_doc_duoc_thi_canh_bao(tmp_path, monkeypatch):
    monkeypatch.setattr(qc, "probe", lambda p: None)
    m: dict = {}
    out = qc._qc_thumbnail(tmp_path / "thumb.jpg", m)
    assert len(out) == 1 and out[0].level == "warn" and out[0].location == "thumbnail" and m == {}
