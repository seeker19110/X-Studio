"""load_media_config đọc file yaml + STUDIO_MEDIA_BASE_URL, và provider OpenAI-compatible (TTS/ảnh) qua urlopen giả —
không chạm mạng, không cần API key thật."""
from __future__ import annotations

import base64
import json

import pytest

from studio.media import MediaConfig, MediaError, OpenAIImage, OpenAITTS, load_media_config


def test_load_media_config_reads_yaml_and_base_url_env(tmp_path, monkeypatch):
    p = tmp_path / "media.yaml"
    p.write_text(
        "tts:\n  provider: openai\n  model: tts-1\n"
        "image:\n  provider: openai\n"
        "video:\n  provider: ffmpeg\n  fps: 24\n"
        "platform:\n  provider: youtube\n"
        "gate:\n  approvers: [human:owner]\n"
        "output_dir: out1\n"
        "upload_dir: up1\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("STUDIO_MEDIA_BASE_URL", raising=False)
    cfg = load_media_config(p)
    assert cfg.tts["provider"] == "openai" and cfg.tts["model"] == "tts-1"
    assert cfg.video["fps"] == 24 and cfg.platform["provider"] == "youtube"
    assert cfg.gate["approvers"] == ["human:owner"]
    assert cfg.output_dir.name == "out1" and cfg.upload_dir.name == "up1"

    monkeypatch.setenv("STUDIO_MEDIA_BASE_URL", "https://compat.example/v1")
    cfg2 = load_media_config(p)
    assert cfg2.tts["base_url"] == "https://compat.example/v1" and cfg2.image["base_url"] == "https://compat.example/v1"


def test_load_media_config_missing_file_uses_defaults(tmp_path):
    cfg = load_media_config(tmp_path / "khong-ton-tai.yaml")
    assert cfg.tts["provider"] == "fake" and cfg.output_dir.name == "output"


class _Resp:
    def __init__(self, data: bytes):
        self._data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._data


def test_openai_tts_posts_and_writes_mp3(tmp_path, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data.decode())
        return _Resp(b"MP3DATA")

    monkeypatch.setattr("studio.media.urllib.request.urlopen", fake_urlopen)
    cfg = MediaConfig(api_key="sk", tts={"provider": "openai", "model": "tts-1", "base_url": "https://api.example.com/v1", "voice": "alloy"})
    tts = OpenAITTS(cfg)
    out = tts.synthesize("xin chào các bạn", {"voice_id": "nova"}, tmp_path / "a.wav")
    assert out.path == tmp_path / "a.mp3" and out.path.read_bytes() == b"MP3DATA"
    assert out.provider == "openai" and out.model == "tts-1" and out.duration_s > 0
    assert captured["body"] == {"model": "tts-1", "voice": "nova", "input": "xin chào các bạn", "response_format": "mp3"}
    assert captured["headers"]["Authorization"] == "Bearer sk"
    assert captured["url"] == "https://api.example.com/v1/audio/speech"


def test_openai_image_b64_json_written_to_disk(tmp_path, monkeypatch):
    png = base64.b64encode(b"\x89PNGxyz").decode()

    def fake_urlopen(req, timeout=None):
        return _Resp(json.dumps({"data": [{"b64_json": png}]}).encode())

    monkeypatch.setattr("studio.media.urllib.request.urlopen", fake_urlopen)
    cfg = MediaConfig(image={"provider": "openai", "model": "gpt-image-1", "base_url": "https://api.example.com/v1"})
    gen = OpenAIImage(cfg)
    out = gen.generate("một cảnh đẹp", "1024x1024", tmp_path / "b.png")
    assert out.path.read_bytes() == base64.b64decode(png) and out.provider == "openai" and out.model == "gpt-image-1"


def test_openai_image_url_download_success(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(url_or_req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(json.dumps({"data": [{"url": "https://img.example.com/a.png"}]}).encode())
        return _Resp(b"PNGBYTES")

    monkeypatch.setattr("studio.media.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("studio.media.check_url", lambda url: url)  # host thật ngoài phạm vi test này (đã có test riêng chặn 169.254)
    gen = OpenAIImage(MediaConfig(image={"provider": "openai", "base_url": "https://api.example.com/v1"}))
    out = gen.generate("p", "512x512", tmp_path / "d.png")
    assert out.path.read_bytes() == b"PNGBYTES" and calls["n"] == 2


def test_openai_image_no_data_raises_media_error(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _Resp(json.dumps({"data": [{}]}).encode())

    monkeypatch.setattr("studio.media.urllib.request.urlopen", fake_urlopen)
    gen = OpenAIImage(MediaConfig(image={"provider": "openai", "base_url": "https://api.example.com/v1"}))
    with pytest.raises(MediaError, match="không có b64_json"):
        gen.generate("p", "512x512", tmp_path / "c.png")


def test_http_post_http_error_and_network_error(tmp_path, monkeypatch):
    import types
    import urllib.error

    def raise_http(req, timeout=None):
        fp = types.SimpleNamespace(read=lambda: b"chi tiet loi")
        raise urllib.error.HTTPError("u", 400, "bad", {}, fp)

    monkeypatch.setattr("studio.media.urllib.request.urlopen", raise_http)
    tts = OpenAITTS(MediaConfig(tts={"provider": "openai", "base_url": "https://api.example.com/v1"}))
    with pytest.raises(MediaError, match="HTTP 400"):
        tts.synthesize("x", {}, tmp_path / "a.wav")

    def raise_url(req, timeout=None):
        raise urllib.error.URLError("mat mang")

    monkeypatch.setattr("studio.media.urllib.request.urlopen", raise_url)
    tts2 = OpenAITTS(MediaConfig(tts={"provider": "openai", "base_url": "https://api.example.com/v1"}))
    with pytest.raises(MediaError, match="lỗi mạng"):
        tts2.synthesize("x", {}, tmp_path / "a.wav")


def test_ffmpeg_assembler_run_raises_media_error_on_nonzero_exit(monkeypatch):
    import subprocess

    from studio import media
    monkeypatch.setattr(media.shutil, "which", lambda b: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "loi ffmpeg chi tiet"))
    asm = media.FFmpegAssembler()
    with pytest.raises(MediaError, match="ffmpeg lỗi"):
        asm._run(["-version"])


def test_ffmpeg_assembler_missing_binary_raises_clear_error(monkeypatch):
    from studio import media
    monkeypatch.setattr(media.shutil, "which", lambda b: None)
    with pytest.raises(MediaError, match="không tìm thấy ffmpeg"):
        media.FFmpegAssembler()
