"""Lớp MEDIA trung lập provider (ADR-0003): giọng đọc (TTS), ảnh cảnh, ghép video.

Ba interface nhỏ (`TTS`, `ImageGen`, `VideoAssembler`), mỗi kênh chọn provider độc lập trong `media.yaml` hoặc
biến môi trường `STUDIO_MEDIA_*`. `fake` sinh file giữ chỗ hợp lệ để chạy offline/test; `openai` gọi endpoint
OpenAI-compatible (`/audio/speech`, `/images/generations`) nên dùng được với OpenAI hay bất kỳ server tương thích;
`ffmpeg` ghép ảnh + audio thành MP4 bằng ffmpeg trên PATH. Thêm provider = thêm một class, không chạm renderer.

Mọi kết quả là `MediaResult(path, provider, model, duration_s)` — renderer đóng gói thành `media-assets` có checksum
và provenance. Không có model text nào gọi được lớp này: chỉ code gọi.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "media.yaml"
WORDS_PER_SECOND = 2.5  # ~150 từ/phút: ước lượng thời lượng khi provider không trả về


class MediaError(Exception): ...


@dataclass
class MediaResult:
    path: Path
    provider: str
    model: str
    duration_s: float | None = None


class TTS(Protocol):
    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult: ...


class ImageGen(Protocol):
    def generate(self, prompt: str, size: str, out: Path) -> MediaResult: ...


class VideoAssembler(Protocol):
    def assemble(self, segments: list[tuple[Path, Path, float]], out: Path, fps: int, resolution: str) -> MediaResult: ...


# ---------- cấu hình ----------

@dataclass
class MediaConfig:
    tts: dict[str, Any] = field(default_factory=lambda: {"provider": "fake", "model": "fake-tts", "voice": "neutral"})
    image: dict[str, Any] = field(default_factory=lambda: {"provider": "fake", "model": "fake-image", "size": "1792x1024"})
    video: dict[str, Any] = field(default_factory=lambda: {"provider": "fake", "fps": 30, "resolution": "1920x1080"})
    platform: dict[str, Any] = field(default_factory=lambda: {"provider": "fake"})  # adapter nền tảng (ADR-0008): fake | youtube
    output_dir: Path = field(default_factory=lambda: ROOT / "output")
    api_key: str | None = None


def load_media_config(path: Path | None = None) -> MediaConfig:
    cfg = MediaConfig()
    p = path or CONFIG_FILE
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k in ("tts", "image", "video", "platform"):
            getattr(cfg, k).update(data.get(k) or {})
        if data.get("output_dir"): cfg.output_dir = ROOT / str(data["output_dir"])
    env = os.environ
    for k in ("tts", "image", "video"):
        v = env.get(f"STUDIO_MEDIA_{k.upper()}_PROVIDER")
        if v: getattr(cfg, k)["provider"] = v
    if env.get("STUDIO_PLATFORM"): cfg.platform["provider"] = env["STUDIO_PLATFORM"]
    if env.get("STUDIO_MEDIA_BASE_URL"):
        cfg.tts["base_url"] = cfg.image["base_url"] = env["STUDIO_MEDIA_BASE_URL"]
    if env.get("STUDIO_MEDIA_OUTPUT_DIR"): cfg.output_dir = Path(env["STUDIO_MEDIA_OUTPUT_DIR"])
    cfg.api_key = env.get("STUDIO_MEDIA_API_KEY") or env.get("STUDIO_LLM_API_KEY") or env.get("OPENAI_API_KEY")
    return cfg


@dataclass
class MediaSuite:
    tts: TTS
    image: ImageGen
    video: VideoAssembler
    cfg: MediaConfig

    @property
    def names(self) -> dict[str, str]:
        return {k: str(getattr(self.cfg, k).get("provider")) for k in ("tts", "image", "video")}


def make_media(cfg: MediaConfig | None = None) -> MediaSuite:
    cfg = cfg or load_media_config()
    tts: TTS = OpenAITTS(cfg) if cfg.tts.get("provider") == "openai" else _require_fake("tts", cfg.tts, FakeTTS(cfg))
    img: ImageGen = OpenAIImage(cfg) if cfg.image.get("provider") == "openai" else _require_fake("image", cfg.image, FakeImage(cfg))
    vp = cfg.video.get("provider")
    vid: VideoAssembler = FFmpegAssembler() if vp == "ffmpeg" else _require_fake("video", cfg.video, FakeVideo())
    return MediaSuite(tts=tts, image=img, video=vid, cfg=cfg)


def _require_fake(kind: str, section: dict[str, Any], fake: Any) -> Any:
    if section.get("provider", "fake") != "fake":
        raise MediaError(f"{kind}: provider lạ `{section.get('provider')}`")
    return fake


def estimate_duration(text: str) -> float:
    return round(max(1.0, len(text.split()) / WORDS_PER_SECOND), 2)


# ---------- provider giả (offline) ----------

_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """PNG đơn sắc hợp lệ (để ffmpeg thật cũng ghép được ảnh giả)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + tag + data + zlib.crc32(tag + data).to_bytes(4, "big")
    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


class FakeTTS:
    def __init__(self, cfg: MediaConfig | None = None):
        self.cfg = cfg or MediaConfig()

    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        # WAV im lặng đúng thời lượng ước lượng để ffmpeg thật cũng chạy được với asset giả
        dur = estimate_duration(text); rate = 8000; n = int(dur * rate)
        data = b"\x00" * n
        hdr = (b"RIFF" + (36 + n).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little")
               + (1).to_bytes(2, "little") + rate.to_bytes(4, "little") + rate.to_bytes(4, "little") + (1).to_bytes(2, "little")
               + (8).to_bytes(2, "little") + b"data" + n.to_bytes(4, "little"))
        out.write_bytes(hdr + data)
        return MediaResult(out, "fake", "fake-tts", dur)


class FakeImage:
    def __init__(self, cfg: MediaConfig | None = None):
        self.cfg = cfg or MediaConfig()

    def generate(self, prompt: str, size: str, out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        w, h = (int(x) for x in size.lower().split("x"))
        shade = (zlib.crc32(prompt.encode("utf-8")) % 156) + 60
        out.write_bytes(_solid_png(min(w, 64), min(h, 64), (shade, 90, 160)))
        return MediaResult(out, "fake", "fake-image")


class FakeVideo:
    def assemble(self, segments: list[tuple[Path, Path, float]], out: Path, fps: int, resolution: str) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = [{"image": str(i), "audio": str(a), "duration_s": d} for i, a, d in segments]
        out.write_text(json.dumps({"fake_mp4": True, "fps": fps, "resolution": resolution, "segments": manifest},
                                  ensure_ascii=False), encoding="utf-8")
        return MediaResult(out, "fake", "fake-video", round(sum(d for _, _, d in segments), 2))


# ---------- provider OpenAI-compatible ----------

class _HTTP:
    def __init__(self, section: dict[str, Any], api_key: str | None, timeout: float = 300.0):
        self.base_url = str(section.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key, self.timeout = api_key, timeout

    def post(self, path: str, body: dict[str, Any]) -> bytes:
        req = urllib.request.Request(f"{self.base_url}{path}", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise MediaError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
        except urllib.error.URLError as e:
            raise MediaError(f"lỗi mạng: {e.reason}") from e


class OpenAITTS:
    """POST /audio/speech {model, voice, input} → mp3."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; self.http = _HTTP(cfg.tts, cfg.api_key)
        self.model = str(cfg.tts.get("model") or "gpt-4o-mini-tts")

    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        body = {"model": self.model, "voice": voice.get("voice_id") or self.cfg.tts.get("voice", "alloy"), "input": text,
                "response_format": "mp3"}
        out.with_suffix(".mp3").write_bytes(self.http.post("/audio/speech", body))
        return MediaResult(out.with_suffix(".mp3"), "openai", self.model, estimate_duration(text))


class OpenAIImage:
    """POST /images/generations {model, prompt, size, n=1} → b64_json (hoặc url)."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; self.http = _HTTP(cfg.image, cfg.api_key)
        self.model = str(cfg.image.get("model") or "gpt-image-1")

    def generate(self, prompt: str, size: str, out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(self.http.post("/images/generations", {"model": self.model, "prompt": prompt, "size": size, "n": 1}))
        item = (data.get("data") or [{}])[0]
        if item.get("b64_json"):
            out.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=120) as r: out.write_bytes(r.read())
        else:
            raise MediaError("phản hồi ảnh không có b64_json/url")
        return MediaResult(out, "openai", self.model)


# ---------- ghép video bằng ffmpeg ----------

class FFmpegAssembler:
    def __init__(self, binary: str = "ffmpeg"):
        found = shutil.which(binary)
        if not found:
            raise MediaError("không tìm thấy ffmpeg trên PATH (đổi video.provider=fake để chạy offline)")
        self.binary = found

    def _run(self, args: list[str]) -> None:
        r = subprocess.run([self.binary, "-y", "-loglevel", "error", *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise MediaError(f"ffmpeg lỗi: {r.stderr[-400:]}")

    def assemble(self, segments: list[tuple[Path, Path, float]], out: Path, fps: int, resolution: str) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        w, h = resolution.lower().split("x")
        parts: list[Path] = []
        for i, (img, audio, dur) in enumerate(segments):
            seg = out.parent / f"{out.stem}_seg{i:03d}.mp4"
            self._run(["-loop", "1", "-framerate", str(fps), "-i", str(img), "-i", str(audio), "-t", f"{dur:.2f}",
                       "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
                       "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac", "-ar", "44100", "-shortest", str(seg)])
            parts.append(seg)
        lst = out.parent / f"{out.stem}_concat.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        self._run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
        return MediaResult(out, "ffmpeg", "libx264", round(sum(d for _, _, d in segments), 2))
