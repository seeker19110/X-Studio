"""Lớp MEDIA trung lập provider (ADR-0003): giọng đọc (TTS), ảnh cảnh, ghép video.

Ba interface nhỏ (`TTS`, `ImageGen`, `VideoAssembler`), mỗi kênh chọn provider độc lập trong `media.yaml` hoặc
biến môi trường `STUDIO_MEDIA_*`. `fake` sinh file giữ chỗ hợp lệ để chạy offline/test.

- TTS: `openai` (`/audio/speech`, mọi server OpenAI-compatible), `gemini` (Gemini TTS: PCM → WAV, thời lượng đo từ số mẫu),
  `elevenlabs`, `azure` (Azure Speech, giọng vi-VN-HoaiMyNeural/NamMinhNeural), `google` (Cloud Text-to-Speech, vi-VN-Neural2),
  `command` (lệnh cục bộ: Piper, Kokoro, edge-tts… — self-hosted, không tốn tiền).
- Ảnh: `openai` (`/images/generations`), `gemini` (Gemini Image `gemini-*-image` hoặc Imagen `imagen-*` theo tên model),
  `stability` (Stable Image core/sd3/ultra), `replicate` (Flux, SDXL… qua predictions có `Prefer: wait`).
- Video: `ffmpeg` ghép ảnh + audio thành MP4 bằng ffmpeg trên PATH; cũng là nơi hoàn thiện thumbnail (thu về 1280x720,
  phủ chữ bằng `drawtext`, xuất JPEG ≤ 2 MB) — model ảnh không bao giờ được yêu cầu vẽ chữ (nó viết sai dấu tiếng Việt).

Ba quy tắc khung hình và thời lượng nằm ở đây vì chúng quyết định video có xem được hay không:
`image_size` chọn kích thước HỢP LỆ THEO MODEL (gpt-image-1 không nhận 1792x1024 — sai là HTTP 400 ngay lượt render thật
đầu tiên); `frame_size` đổi khung theo `aspect` của manifest (9:16 cho Shorts, không phải 1920x1080 kèm hai dải đen);
assembler không cắt theo thời lượng ước lượng mà để `-shortest` chạy hết giọng đọc, nên câu không bị cụt giữa chừng.

Thêm provider = thêm một class + một dòng trong `TTS_PROVIDERS` / `IMAGE_PROVIDERS`, không chạm renderer. Tất cả dùng
`urllib` thuần, không SDK. Khóa API: `<kênh>.api_key` hoặc `<kênh>.api_key_env` trong media.yaml → biến môi trường quen thuộc
của provider (GEMINI_API_KEY, ELEVENLABS_API_KEY, AZURE_SPEECH_KEY, GOOGLE_API_KEY, STABILITY_API_KEY, REPLICATE_API_TOKEN)
→ STUDIO_MEDIA_API_KEY. Giọng: `voice_id` trong manifest là tên của provider nào không ai biết trước, nên `tts.voices:
{alloy: vi-VN-HoaiMyNeural}` dịch sang provider đang dùng; `pace` (slow|medium|fast) map sang tham số tốc độ tương ứng.

Mọi kết quả là `MediaResult(path, provider, model, duration_s)` — renderer đóng gói thành `media-assets` có checksum
và provenance. Thời lượng audio đo từ file thật (WAV: header; khác: ffprobe nếu có), chỉ ước lượng theo số từ khi không đo được.
Không có model text nào gọi được lớp này: chỉ code gọi.
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import yaml

from .tools import ToolError, check_url

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "media.yaml"
WORDS_PER_SECOND = 2.5  # ~150 từ/phút: ước lượng thời lượng khi không đo được từ file
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
PACE_SPEED = {"slow": 0.9, "medium": 1.0, "fast": 1.15}  # pace của manifest → hệ số tốc độ (openai speed, google speakingRate)
LANG_CODES = {"vi": "vi-VN", "en": "en-US", "ja": "ja-JP", "ko": "ko-KR", "zh": "cmn-CN", "fr": "fr-FR", "de": "de-DE",
              "es": "es-ES", "th": "th-TH", "id": "id-ID"}
ASPECTS = {"1:1": 1.0, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 1.5, "2:3": 2 / 3}
# Kích thước ảnh HỢP LỆ theo model: gửi sai là HTTP 400. gpt-image-1 không nhận 1792x1024 (đó là của dall-e-3).
MODEL_SIZES: dict[str, dict[str, str]] = {
    "gpt-image-1": {"16:9": "1536x1024", "9:16": "1024x1536", "1:1": "1024x1024"},
    "dall-e-3": {"16:9": "1792x1024", "9:16": "1024x1792", "1:1": "1024x1024"},
    "dall-e-2": {"16:9": "1024x1024", "9:16": "1024x1024", "1:1": "1024x1024"},
}
DEFAULT_SIZES = {"16:9": "1536x1024", "9:16": "1024x1536", "1:1": "1024x1024"}
THUMBNAIL_SIZE = "1280x720"      # YouTube: 1280x720, ≤ 2 MB, JPG/PNG
THUMBNAIL_MAX_BYTES = 2_000_000
# Ngắt mẫu bằng hình (skill retention-storytelling): mỗi cảnh một kiểu chuyển động, xoay vòng để hai cảnh liền nhau khác nhau.
MOTIONS = ("zoom_in", "pan_right", "zoom_out", "pan_left")
MOTION_ZOOM = 1.12               # biên độ zoom/pan: đủ thấy là có chuyển động, không đủ để thấy méo
TARGET_LUFS = -14.0              # mức âm lượng nền tảng phát lại (YouTube/Spotify chuẩn hoá về đây)
TRUE_PEAK_DB = -1.0
# Font đậm có dấu tiếng Việt để phủ chữ thumbnail; `video.font` trong media.yaml thắng danh sách này.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


class MediaError(Exception): ...


@dataclass
class MediaResult:
    path: Path
    provider: str
    model: str
    duration_s: float | None = None
    notes: str = ""  # điều code đã làm/không làm được với file (phủ chữ, thu nhỏ, nén) → renderer ghi vào audit


class TTS(Protocol):
    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult: ...


class ImageGen(Protocol):
    def generate(self, prompt: str, size: str, out: Path) -> MediaResult: ...


class VideoAssembler(Protocol):
    # Renderer cần đúng hai con số này để tính mốc thời gian từng cảnh (phụ đề, chapter) khớp với bản dựng thật.
    tail_pad_s: float
    transition_s: float

    def assemble(self, segments: list[tuple[Path, Path, float]], out: Path, fps: int, resolution: str) -> MediaResult: ...
    def finish_thumbnail(self, src: Path, out: Path, text: str = "", size: str = THUMBNAIL_SIZE,
                         max_bytes: int = THUMBNAIL_MAX_BYTES) -> MediaResult: ...


# ---------- cấu hình ----------

@dataclass
class MediaConfig:
    tts: dict[str, Any] = field(default_factory=lambda: {"provider": "fake", "model": "fake-tts", "voice": "neutral"})
    # không đặt `size` mặc định: `image_size()` chọn theo model + tỷ lệ khung của manifest
    image: dict[str, Any] = field(default_factory=lambda: {"provider": "fake", "model": "fake-image"})
    video: dict[str, Any] = field(default_factory=lambda: {"provider": "fake", "fps": 30, "resolution": "1920x1080"})
    platform: dict[str, Any] = field(default_factory=lambda: {"provider": "fake"})  # adapter nền tảng (ADR-0008): fake | youtube
    # gate.approvers: [human:owner, ...] — ai được duyệt (env STUDIO_GATE_APPROVERS thắng)
    gate: dict[str, Any] = field(default_factory=dict)
    output_dir: Path = field(default_factory=lambda: ROOT / "output")
    upload_dir: Path | None = None  # nơi người dùng đặt file thay thế cho `replace_asset`; None = <output_dir>/uploads
    api_key: str | None = None


def load_media_config(path: Path | None = None) -> MediaConfig:
    cfg = MediaConfig()
    p = path or CONFIG_FILE
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k in ("tts", "image", "video", "platform", "gate"):
            getattr(cfg, k).update(data.get(k) or {})
        if data.get("output_dir"): cfg.output_dir = ROOT / str(data["output_dir"])
        if data.get("upload_dir"): cfg.upload_dir = ROOT / str(data["upload_dir"])
    env = os.environ
    for k in ("tts", "image", "video"):
        v = env.get(f"STUDIO_MEDIA_{k.upper()}_PROVIDER")
        if v: getattr(cfg, k)["provider"] = v
    if env.get("STUDIO_PLATFORM"): cfg.platform["provider"] = env["STUDIO_PLATFORM"]
    if env.get("STUDIO_MEDIA_BASE_URL"):  # chỉ cho server OpenAI-compatible; provider khác có endpoint riêng
        for section in (cfg.tts, cfg.image):
            if section.get("provider") == "openai": section["base_url"] = env["STUDIO_MEDIA_BASE_URL"]
    if env.get("STUDIO_MEDIA_OUTPUT_DIR"): cfg.output_dir = Path(env["STUDIO_MEDIA_OUTPUT_DIR"])
    if env.get("STUDIO_MEDIA_UPLOAD_DIR"): cfg.upload_dir = Path(env["STUDIO_MEDIA_UPLOAD_DIR"])
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
    tts: TTS = _pick("tts", cfg.tts, TTS_PROVIDERS, cfg, lambda: FakeTTS(cfg))
    img: ImageGen = _pick("image", cfg.image, IMAGE_PROVIDERS, cfg, lambda: FakeImage(cfg))
    v = cfg.video
    vid: VideoAssembler = (FFmpegAssembler(binary=str(v.get("binary") or "ffmpeg"), fit=str(v.get("fit") or "cover"),
                                           tail_pad_s=float(v.get("tail_pad_s", 0.35)), font=v.get("font"),
                                           motion=str(v.get("motion") or "auto"), transition_s=float(v.get("transition_s", 0.3)),
                                           loudness_lufs=v.get("loudness_lufs", TARGET_LUFS))
                           if v.get("provider") == "ffmpeg" else _require_fake("video", v, FakeVideo()))
    return MediaSuite(tts=tts, image=img, video=vid, cfg=cfg)


def _pick(kind: str, section: dict[str, Any], table: dict[str, Callable[[MediaConfig], Any]], cfg: MediaConfig,
          fake: Callable[[], Any]) -> Any:
    prov = str(section.get("provider") or "fake")
    if prov == "fake": return fake()
    cls = table.get(prov)
    if cls is None:
        raise MediaError(f"{kind}: provider lạ `{prov}` (có: fake, {', '.join(table)})")
    return cls(cfg)


def _require_fake(kind: str, section: dict[str, Any], fake: Any) -> Any:
    if section.get("provider", "fake") != "fake":
        raise MediaError(f"{kind}: provider lạ `{section.get('provider')}`")
    return fake


def image_size(cfg: MediaConfig, aspect: str = "16:9") -> str:
    """Kích thước gửi cho provider ảnh. Model OpenAI chỉ nhận một tập kích thước cố định nên `image.size` khai sai
    (vd. 1792x1024 cho gpt-image-1) được thay bằng kích thước hợp lệ cùng tỷ lệ, thay vì để lượt render thật nhận HTTP 400.
    Provider nhận tỷ lệ thay vì pixel (gemini, stability, replicate) thì giữ nguyên cấu hình — `aspect_of` quy đổi sau."""
    want = str(cfg.image.get("size") or "")
    model = str(cfg.image.get("model") or "")
    table = next((v for k, v in MODEL_SIZES.items() if model.startswith(k)), None)
    if table is None: return want or DEFAULT_SIZES.get(aspect, DEFAULT_SIZES["16:9"])
    if want in table.values(): return want
    return table.get(aspect, table["16:9"])


def frame_size(video: dict[str, Any], aspect: str = "16:9") -> str:
    """Khung video theo `aspect` của manifest: short 9:16 hoán đổi chiều của `video.resolution` (1920x1080 → 1080x1920),
    nếu không ảnh dọc bị pad thành khung ngang với hai dải đen."""
    try:
        w, h = (int(x) for x in str(video.get("resolution") or "1920x1080").lower().split("x"))
    except ValueError:
        w, h = 1920, 1080
    if (aspect == "9:16") != (h > w): w, h = h, w
    return f"{w}x{h}"


def find_font(video: dict[str, Any] | None = None) -> Path | None:
    """Font phủ chữ thumbnail: `video.font` trong media.yaml, hoặc font đậm có dấu tiếng Việt sẵn trên máy."""
    for c in [str((video or {}).get("font") or ""), *FONT_CANDIDATES]:
        if c and Path(c).is_file(): return Path(c)
    return None


MAX_OVERLAY_LINES = 3  # hơn 3 dòng thì thumbnail không còn đọc được ở 120 px (skill thumbnail-design: ≤ 4 từ)


def wrap_overlay(text: str, max_chars: int = 16) -> list[str]:
    """Chữ phủ thumbnail: viết hoa (quy tắc skill thumbnail-design), ngắt dòng theo số ký tự. Trả về TẤT CẢ các dòng —
    người gọi cắt còn `MAX_OVERLAY_LINES` và ghi lại là đã cắt, không âm thầm nuốt chữ."""
    lines: list[str] = []; cur = ""
    for w in text.strip().upper().split():
        if cur and len(cur) + 1 + len(w) > max_chars: lines.append(cur); cur = w
        else: cur = f"{cur} {w}".strip()
    if cur: lines.append(cur)
    return lines


def estimate_duration(text: str) -> float:
    return round(max(1.0, len(text.split()) / WORDS_PER_SECOND), 2)


def motion_for(index: int, mode: str = "auto") -> str:
    """Kiểu chuyển động của cảnh thứ `index`. `auto` xoay vòng nên không hai cảnh liền nhau cùng kiểu; `none` để ảnh tĩnh."""
    if mode in MOTIONS: return mode
    if mode != "auto": return "static"
    return MOTIONS[index % len(MOTIONS)]


def probe_duration(path: Path) -> float | None:
    """Thời lượng thật của file theo ffprobe; None khi không có ffprobe hoặc file không đọc được."""
    probe = shutil.which("ffprobe")
    if not probe: return None
    try:
        r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return round(float(r.stdout.strip()), 3) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def audio_duration(path: Path, text: str) -> float:
    """Thời lượng THẬT của file audio: WAV đọc header (stdlib), định dạng khác hỏi ffprobe nếu có trên PATH;
    không đo được thì ước lượng theo số từ. Thời lượng đúng là điều kiện để ffmpeg không cắt giữa câu (`-t`)."""
    try:
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as w:
                if w.getframerate() > 0: return round(w.getnframes() / w.getframerate(), 2)
        probe = shutil.which("ffprobe")
        if probe:
            r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if r.returncode == 0 and r.stdout.strip(): return round(float(r.stdout.strip()), 2)
    except (OSError, ValueError, EOFError, wave.Error, subprocess.SubprocessError):
        pass
    return estimate_duration(text)


def write_wav(out: Path, pcm: bytes, rate: int, channels: int = 1, sample_width: int = 2) -> None:
    with wave.open(str(out), "wb") as w:
        w.setnchannels(channels); w.setsampwidth(sample_width); w.setframerate(rate); w.writeframes(pcm)


# ---------- tiện ích chung cho provider ----------

def section_api_key(section: dict[str, Any], cfg: MediaConfig, *env_names: str) -> str | None:
    """Khóa API của một kênh: `api_key` → `api_key_env` → biến môi trường quen thuộc của provider → STUDIO_MEDIA_API_KEY."""
    if section.get("api_key"): return str(section["api_key"])
    env = os.environ
    if section.get("api_key_env") and env.get(str(section["api_key_env"])): return env[str(section["api_key_env"])]
    for n in env_names:
        if env.get(n): return env[n]
    return cfg.api_key


def require_key(kind: str, provider: str, key: str | None, hint: str) -> str:
    if not key:
        raise MediaError(f"{kind}: provider `{provider}` cần API key (đặt {hint}, hoặc {kind}.api_key / {kind}.api_key_env trong media.yaml)")
    return key


def pace_of(voice: dict[str, Any]) -> str:
    p = str(voice.get("pace") or "medium").lower()
    return p if p in PACE_SPEED else "medium"


def pick_voice(section: dict[str, Any], voice: dict[str, Any], default: str, trust_manifest: bool = True) -> str:
    """Giọng gửi cho provider. `tts.voices: {<voice_id manifest>: <giọng provider>}` dịch id của manifest; có bảng mà id
    không có trong bảng → `tts.voice`. Không có bảng: provider dùng tên giọng ngắn (openai, gemini) thì tin id của manifest
    (`trust_manifest`), provider có id riêng (elevenlabs, azure, google, command) thì chỉ dùng `tts.voice`."""
    vid = str(voice.get("voice_id") or "")
    table = section.get("voices") or {}
    if table: return str(table.get(vid) or section.get("voice") or default)
    if trust_manifest and vid: return vid
    return str(section.get("voice") or default)


def language_code(voice: dict[str, Any], section: dict[str, Any], voice_name: str = "") -> str:
    """`vi` → `vi-VN`; đã có vùng thì giữ; không có gì thì lấy từ tên giọng dạng `vi-VN-...`, mặc định vi-VN."""
    lang = str(voice.get("language") or section.get("language") or "")
    if not lang and re.match(r"^[a-z]{2,3}-[A-Z]{2}-", voice_name): return voice_name.split("-", 2)[0] + "-" + voice_name.split("-", 2)[1]
    return LANG_CODES.get(lang.lower(), lang or "vi-VN")


def aspect_of(size: str, allowed: dict[str, float] | None = None) -> str:
    """`1792x1024` → tỷ lệ gần nhất trong bảng cho phép (provider nhận aspectRatio, không nhận pixel)."""
    table = allowed or ASPECTS
    try:
        w, h = (int(x) for x in size.lower().split("x")); ratio = w / h
    except (ValueError, ZeroDivisionError):
        return "16:9"
    return min(table, key=lambda k: abs(table[k] - ratio))


def _multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = "----studio-" + uuid4().hex
    buf = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    return buf + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def _download(url: str) -> bytes:
    """URL do server trả về = dữ liệu không tin cậy: cùng ranh giới với web_fetch (chặn IP riêng/loopback)."""
    try:
        safe = check_url(url)
    except ToolError as e:
        raise MediaError(f"URL ảnh bị chặn: {e}") from e
    try:
        with urllib.request.urlopen(safe, timeout=120) as r: return r.read()
    except urllib.error.URLError as e:
        raise MediaError(f"lỗi tải ảnh: {e.reason}") from e


class _SafeMap(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


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
    tail_pad_s = 0.0      # bản giả ghép thẳng, không đệm và không chuyển cảnh: mốc thời gian = tổng thời lượng cảnh
    transition_s = 0.0

    def assemble(self, segments: list[tuple[Path, Path, float]], out: Path, fps: int, resolution: str) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        manifest = [{"image": str(i), "audio": str(a), "duration_s": d} for i, a, d in segments]
        out.write_text(json.dumps({"fake_mp4": True, "fps": fps, "resolution": resolution, "segments": manifest},
                                  ensure_ascii=False), encoding="utf-8")
        return MediaResult(out, "fake", "fake-video", round(sum(d for _, _, d in segments), 2))

    def finish_thumbnail(self, src: Path, out: Path, text: str = "", size: str = THUMBNAIL_SIZE,
                         max_bytes: int = THUMBNAIL_MAX_BYTES) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)
        return MediaResult(out, "fake", "fake-video", notes="fake: giữ nguyên ảnh, không thu về 1280x720, không phủ chữ")


# ---------- HTTP chung ----------

class _HTTP:
    """urllib thuần. Khóa gửi trong `key_header` (Authorization: Bearer … mặc định; x-goog-api-key, xi-api-key,
    Ocp-Apim-Subscription-Key cho provider khác). Lỗi HTTP/mạng → MediaError có mã và 300 ký tự đầu của thân."""
    def __init__(self, base_url: str, api_key: str | None, timeout: float = 300.0,
                 key_header: str = "Authorization", key_prefix: str = "Bearer "):
        self.base_url = base_url.rstrip("/")
        self.api_key, self.timeout, self.key_header, self.key_prefix = api_key, timeout, key_header, key_prefix

    def request(self, method: str, path_or_url: str, body: bytes | None, headers: dict[str, str] | None = None) -> bytes:
        url = path_or_url if path_or_url.startswith(("http://", "https://")) else f"{self.base_url}{path_or_url}"
        auth = {self.key_header: f"{self.key_prefix}{self.api_key}"} if self.api_key else {}
        req = urllib.request.Request(url, data=body, headers={**auth, **(headers or {})}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise MediaError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
        except urllib.error.URLError as e:
            raise MediaError(f"lỗi mạng: {e.reason}") from e

    def post(self, path: str, body: dict[str, Any]) -> bytes:
        return self.request("POST", path, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})

    def post_raw(self, path: str, body: bytes, headers: dict[str, str]) -> bytes:
        return self.request("POST", path, body, headers)

    def get(self, url: str) -> bytes:
        return self.request("GET", url, None)


# ---------- TTS: OpenAI-compatible ----------

class OpenAITTS:
    """POST /audio/speech {model, voice, input[, speed, instructions]} → mp3."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg
        self.http = _HTTP(str(cfg.tts.get("base_url") or "https://api.openai.com/v1"), section_api_key(cfg.tts, cfg))
        self.model = str(cfg.tts.get("model") or "gpt-4o-mini-tts")

    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        body: dict[str, Any] = {"model": self.model, "voice": pick_voice(self.cfg.tts, voice, "alloy"), "input": text,
                                "response_format": "mp3"}
        pace = pace_of(voice)
        if pace != "medium": body["speed"] = PACE_SPEED[pace]
        if self.cfg.tts.get("instructions"): body["instructions"] = str(self.cfg.tts["instructions"])
        p = out.with_suffix(".mp3"); p.write_bytes(self.http.post("/audio/speech", body))
        return MediaResult(p, "openai", self.model, audio_duration(p, text))


# ---------- TTS: Gemini ----------

def _gemini_inline(data: dict[str, Any]) -> dict[str, Any]:
    for c in data.get("candidates") or []:
        for part in (c.get("content") or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("inlineData"): return dict(part["inlineData"])
    why = data.get("promptFeedback") or (data.get("candidates") or [{}])[0].get("finishReason")
    raise MediaError("phản hồi Gemini không có inlineData" + (f" ({json.dumps(why, ensure_ascii=False)[:200]})" if why else ""))


GEMINI_PACE = {"slow": "Đọc chậm rãi, rõ từng chữ", "fast": "Đọc nhanh, dứt khoát"}


class GeminiTTS:
    """generateContent với responseModalities AUDIO → PCM 16-bit mono (audio/L16;rate=24000) → WAV.
    Thời lượng = số mẫu / rate: đo thật, không ước lượng. Phong cách/nhịp điều khiển bằng câu dẫn (theo tài liệu Gemini TTS):
    `tts.style` (vd. "Giọng kể chuyện ấm, tự nhiên") + pace slow/fast của manifest."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; s = cfg.tts
        key = require_key("tts", "gemini", section_api_key(s, cfg, "GEMINI_API_KEY", "GOOGLE_API_KEY"), "GEMINI_API_KEY")
        self.http = _HTTP(str(s.get("base_url") or GEMINI_BASE), key, key_header="x-goog-api-key", key_prefix="")
        self.model = str(s.get("model") or "gemini-2.5-flash-preview-tts")

    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        s = self.cfg.tts; pace = pace_of(voice)
        style = ([str(s["style"])] if s.get("style") else []) + ([GEMINI_PACE[pace]] if pace in GEMINI_PACE else [])
        prompt = f"{', '.join(style)}: {text}" if style else text
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": pick_voice(s, voice, "Kore")}}}}}
        part = _gemini_inline(json.loads(self.http.post(f"/models/{self.model}:generateContent", body)))
        raw = base64.b64decode(part.get("data") or ""); mime = str(part.get("mimeType") or "audio/L16;codec=pcm;rate=24000").lower()
        if not raw: raise MediaError("phản hồi Gemini TTS rỗng")
        if "l16" in mime or "pcm" in mime:
            m = re.search(r"rate=(\d+)", mime); rate = int(m.group(1)) if m else 24000
            p = out.with_suffix(".wav"); write_wav(p, raw, rate)
            return MediaResult(p, "gemini", self.model, round(len(raw) / (rate * 2), 2))
        p = out.with_suffix(".wav" if "wav" in mime else ".mp3"); p.write_bytes(raw)
        return MediaResult(p, "gemini", self.model, audio_duration(p, text))


# ---------- TTS: ElevenLabs ----------

class ElevenLabsTTS:
    """POST /text-to-speech/{voice_id}?output_format=mp3_44100_128 {text, model_id, voice_settings} → mp3.
    voice_id là id riêng của ElevenLabs: khai `tts.voice` hoặc bảng `tts.voices`."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; s = cfg.tts
        key = require_key("tts", "elevenlabs", section_api_key(s, cfg, "ELEVENLABS_API_KEY", "ELEVEN_API_KEY"), "ELEVENLABS_API_KEY")
        self.http = _HTTP(str(s.get("base_url") or "https://api.elevenlabs.io/v1"), key, key_header="xi-api-key", key_prefix="")
        self.model = str(s.get("model") or "eleven_multilingual_v2")

    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        s = self.cfg.tts; vid = pick_voice(s, voice, "", trust_manifest=False)
        if not vid: raise MediaError("elevenlabs: cần voice_id ElevenLabs (tts.voice hoặc bảng tts.voices)")
        settings: dict[str, Any] = dict(s.get("voice_settings") or {"stability": 0.5, "similarity_boost": 0.75})
        pace = pace_of(voice)
        if pace != "medium": settings["speed"] = {"slow": 0.9, "fast": 1.1}[pace]
        body = {"text": text, "model_id": self.model, "voice_settings": settings}
        fmt = str(s.get("output_format") or "mp3_44100_128")
        raw = self.http.post_raw(f"/text-to-speech/{urllib.parse.quote(vid)}?output_format={urllib.parse.quote(fmt)}",
                                 json.dumps(body).encode("utf-8"), {"Content-Type": "application/json", "Accept": "audio/mpeg"})
        p = out.with_suffix(".mp3"); p.write_bytes(raw)
        return MediaResult(p, "elevenlabs", self.model, audio_duration(p, text))


# ---------- TTS: Azure Speech ----------

AZURE_RATE = {"slow": "-10%", "fast": "+15%"}


class AzureTTS:
    """Azure AI Speech REST: POST https://{region}.tts.speech.microsoft.com/cognitiveservices/v1 với SSML → mp3.
    Giọng tiếng Việt: vi-VN-HoaiMyNeural (nữ), vi-VN-NamMinhNeural (nam). pace → <prosody rate>."""

    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; s = cfg.tts
        key = require_key("tts", "azure", section_api_key(s, cfg, "AZURE_SPEECH_KEY", "SPEECH_KEY"), "AZURE_SPEECH_KEY")
        region = str(s.get("region") or "southeastasia")
        self.http = _HTTP(str(s.get("base_url") or f"https://{region}.tts.speech.microsoft.com"), key,
                          key_header="Ocp-Apim-Subscription-Key", key_prefix="")
        self.fmt = str(s.get("output_format") or "audio-24khz-96kbitrate-mono-mp3"); self.model = "azure-speech"

    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        s = self.cfg.tts; name = pick_voice(s, voice, "vi-VN-HoaiMyNeural", trust_manifest=False)
        lang = language_code(voice, s, name); pace = pace_of(voice); inner = html.escape(text, quote=False)
        if pace in AZURE_RATE: inner = f'<prosody rate="{AZURE_RATE[pace]}">{inner}</prosody>'
        ssml = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{lang}">'
                f'<voice name="{html.escape(name)}">{inner}</voice></speak>')
        raw = self.http.post_raw("/cognitiveservices/v1", ssml.encode("utf-8"),
                                 {"Content-Type": "application/ssml+xml", "X-Microsoft-OutputFormat": self.fmt,
                                  "User-Agent": "studio-creators"})
        p = out.with_suffix(".wav" if "riff" in self.fmt else ".mp3"); p.write_bytes(raw)
        return MediaResult(p, "azure", name, audio_duration(p, text))


# ---------- TTS: Google Cloud Text-to-Speech ----------

class GoogleTTS:
    """POST https://texttospeech.googleapis.com/v1/text:synthesize → {audioContent: base64 mp3}.
    Giọng tiếng Việt: vi-VN-Neural2-A/D, vi-VN-Wavenet-A…D, vi-VN-Standard-A…D. pace → speakingRate."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; s = cfg.tts
        key = require_key("tts", "google", section_api_key(s, cfg, "GOOGLE_API_KEY", "GEMINI_API_KEY"), "GOOGLE_API_KEY")
        self.http = _HTTP(str(s.get("base_url") or "https://texttospeech.googleapis.com/v1"), key,
                          key_header="x-goog-api-key", key_prefix="")

    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        s = self.cfg.tts; name = pick_voice(s, voice, "vi-VN-Neural2-A", trust_manifest=False)
        body = {"input": {"text": text}, "voice": {"languageCode": language_code(voice, s, name), "name": name},
                "audioConfig": {"audioEncoding": "MP3", "speakingRate": PACE_SPEED[pace_of(voice)]}}
        data = json.loads(self.http.post("/text:synthesize", body))
        if not data.get("audioContent"): raise MediaError("phản hồi Google TTS không có audioContent")
        p = out.with_suffix(".mp3"); p.write_bytes(base64.b64decode(data["audioContent"]))
        return MediaResult(p, "google", name, audio_duration(p, text))


# ---------- TTS: lệnh cục bộ (Piper, Kokoro, edge-tts…) ----------

class CommandTTS:
    """`tts.command` là dòng lệnh có chỗ giữ {text} {out} {voice} {lang} {pace}; văn bản cũng đưa vào stdin. Không qua shell.
    Ví dụ: "piper -m vi_VN-vais1000-medium.onnx -f {out}" · "edge-tts --voice {voice} --text {text} --write-media {out}"
    (đặt `suffix: .mp3` cho edge-tts). File {out} phải có sau khi lệnh kết thúc; stderr của lệnh nằm trong lỗi."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; s = cfg.tts
        cmd = str(s.get("command") or "")
        if not cmd: raise MediaError('tts: provider `command` cần tts.command (vd. "piper -m vi.onnx -f {out}")')
        self.argv = shlex.split(cmd)
        self.model = str(s.get("model") or Path(self.argv[0]).name)
        self.suffix = str(s.get("suffix") or ".wav"); self.timeout = float(s.get("timeout_s") or 300)

    def synthesize(self, text: str, voice: dict[str, Any], out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        s = self.cfg.tts; p = out.with_suffix(self.suffix)
        fields = _SafeMap(text=text, out=str(p), voice=pick_voice(s, voice, "", trust_manifest=False),
                          lang=language_code(voice, s), pace=pace_of(voice))
        argv = [a.format_map(fields) for a in self.argv]
        # PYTHONIOENCODING: ta ghi stdin bằng UTF-8, nhưng tiến trình con giải mã theo bảng mã cục bộ của nó —
        # trên Windows là cp1252, nên "xin chào" tới lệnh TTS thành "xin ch?o". Lỗi này im lặng: lệnh vẫn thoát 0
        # và vẫn tạo ra file audio, chỉ có giọng đọc là sai. Cùng cách chữa như cầu MCP của software-company.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            r = subprocess.run(argv, input=text, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=env, timeout=self.timeout)
        except (OSError, subprocess.SubprocessError) as e:
            raise MediaError(f"lệnh TTS không chạy được: {e}") from e
        if r.returncode != 0: raise MediaError(f"lệnh TTS lỗi ({r.returncode}): {r.stderr[-400:]}")
        if not p.is_file() or p.stat().st_size == 0: raise MediaError(f"lệnh TTS không tạo file {p.name}")
        return MediaResult(p, "command", self.model, audio_duration(p, text))


# ---------- Ảnh: OpenAI-compatible ----------

class OpenAIImage:
    """POST /images/generations {model, prompt, size, n=1} → b64_json (hoặc url).
    Lưu ý kích thước theo model: gpt-image-1 nhận 1024x1024 | 1536x1024 | 1024x1536 | auto; dall-e-3 nhận 1792x1024 | 1024x1792."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg
        self.http = _HTTP(str(cfg.image.get("base_url") or "https://api.openai.com/v1"), section_api_key(cfg.image, cfg))
        self.model = str(cfg.image.get("model") or "gpt-image-1")

    def generate(self, prompt: str, size: str, out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(self.http.post("/images/generations", {"model": self.model, "prompt": prompt, "size": size, "n": 1}))
        item = (data.get("data") or [{}])[0]
        if item.get("b64_json"):
            out.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            out.write_bytes(_download(item["url"]))
        else:
            raise MediaError("phản hồi ảnh không có b64_json/url")
        return MediaResult(out, "openai", self.model)


# ---------- Ảnh: Gemini Image / Imagen ----------

class GeminiImage:
    """Model `imagen-*` → POST /models/{model}:predict {instances, parameters{aspectRatio}} → bytesBase64Encoded.
    Model khác (gemini-2.5-flash-image, gemini-*-image-*) → generateContent với responseModalities IMAGE + imageConfig.aspectRatio.
    Kích thước pixel của cấu hình được đổi sang tỷ lệ gần nhất; renderer/ffmpeg scale về khung video."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; s = cfg.image
        key = require_key("image", "gemini", section_api_key(s, cfg, "GEMINI_API_KEY", "GOOGLE_API_KEY"), "GEMINI_API_KEY")
        self.http = _HTTP(str(s.get("base_url") or GEMINI_BASE), key, key_header="x-goog-api-key", key_prefix="")
        self.model = str(s.get("model") or "gemini-2.5-flash-image")

    def generate(self, prompt: str, size: str, out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        aspect = aspect_of(size)
        if self.model.startswith("imagen"):
            data = json.loads(self.http.post(f"/models/{self.model}:predict", {
                "instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1, "aspectRatio": aspect}}))
            preds = data.get("predictions") or [{}]
            if not preds[0].get("bytesBase64Encoded"): raise MediaError("phản hồi Imagen không có bytesBase64Encoded")
            raw = base64.b64decode(preds[0]["bytesBase64Encoded"]); mime = str(preds[0].get("mimeType") or "image/png")
        else:
            modalities = list(self.cfg.image.get("response_modalities") or ["IMAGE"])
            part = _gemini_inline(json.loads(self.http.post(f"/models/{self.model}:generateContent", {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": modalities, "imageConfig": {"aspectRatio": aspect}}})))
            raw = base64.b64decode(part.get("data") or ""); mime = str(part.get("mimeType") or "image/png")
        if not raw: raise MediaError("phản hồi Gemini ảnh rỗng")
        p = out.with_suffix(".jpg") if "jpeg" in mime or "jpg" in mime else out
        p.write_bytes(raw)
        return MediaResult(p, "gemini", self.model)


# ---------- Ảnh: Stability AI ----------

STABILITY_ASPECTS = {"16:9": 16 / 9, "1:1": 1.0, "21:9": 21 / 9, "2:3": 2 / 3, "3:2": 1.5, "4:5": 0.8, "5:4": 1.25,
                     "9:16": 9 / 16, "9:21": 9 / 21}


class StabilityImage:
    """Stable Image v2beta: POST /stable-image/generate/{core|sd3|ultra} (multipart) với Accept: image/* → PNG.
    `image.model` = đoạn đường dẫn (core mặc định); `image.extra` (vd. {model: sd3.5-large, style_preset: photographic})
    được nối vào form."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; s = cfg.image
        key = require_key("image", "stability", section_api_key(s, cfg, "STABILITY_API_KEY"), "STABILITY_API_KEY")
        self.http = _HTTP(str(s.get("base_url") or "https://api.stability.ai/v2beta"), key)
        self.model = str(s.get("model") or "core")

    def generate(self, prompt: str, size: str, out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        fields = {"prompt": prompt, "aspect_ratio": aspect_of(size, STABILITY_ASPECTS), "output_format": "png"}
        if self.cfg.image.get("negative_prompt"): fields["negative_prompt"] = str(self.cfg.image["negative_prompt"])
        fields.update({k: str(v) for k, v in (self.cfg.image.get("extra") or {}).items()})
        body, ctype = _multipart(fields)
        raw = self.http.post_raw(f"/stable-image/generate/{self.model}", body, {"Content-Type": ctype, "Accept": "image/*"})
        if not raw: raise MediaError("phản hồi Stability rỗng")
        out.write_bytes(raw)
        return MediaResult(out, "stability", self.model)


# ---------- Ảnh: Replicate (Flux, SDXL…) ----------

class ReplicateImage:
    """POST /models/{owner}/{name}/predictions với Prefer: wait → prediction; chưa `succeeded` thì poll `urls.get`
    (poll_s × poll_max); output là URL → tải qua ranh giới URL. `image.input` nối thêm tham số riêng của model."""
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg; s = cfg.image
        key = require_key("image", "replicate", section_api_key(s, cfg, "REPLICATE_API_TOKEN"), "REPLICATE_API_TOKEN")
        self.http = _HTTP(str(s.get("base_url") or "https://api.replicate.com/v1"), key)
        self.model = str(s.get("model") or "black-forest-labs/flux-schnell")
        self.poll_s = float(s.get("poll_s", 2)); self.poll_max = int(s.get("poll_max", 60))

    def generate(self, prompt: str, size: str, out: Path) -> MediaResult:
        out.parent.mkdir(parents=True, exist_ok=True)
        inp = {"prompt": prompt, "aspect_ratio": aspect_of(size), "output_format": "png", **(self.cfg.image.get("input") or {})}
        pred = json.loads(self.http.post_raw(f"/models/{self.model}/predictions", json.dumps({"input": inp}).encode("utf-8"),
                                             {"Content-Type": "application/json", "Prefer": "wait=60"}))
        for _ in range(self.poll_max):
            if pred.get("status") in {"succeeded", "failed", "canceled"}: break
            get_url = (pred.get("urls") or {}).get("get")
            if not get_url: break
            time.sleep(self.poll_s); pred = json.loads(self.http.get(str(get_url)))
        if pred.get("status") != "succeeded":
            raise MediaError(f"replicate: {pred.get('status') or 'không rõ trạng thái'}: {str(pred.get('error') or '')[:300]}")
        output = pred.get("output"); url = output[0] if isinstance(output, list) and output else output
        if not isinstance(url, str) or not url: raise MediaError("replicate: output không có URL ảnh")
        out.write_bytes(_download(url))
        return MediaResult(out, "replicate", self.model)


TTS_PROVIDERS: dict[str, Callable[[MediaConfig], Any]] = {
    "openai": OpenAITTS, "gemini": GeminiTTS, "elevenlabs": ElevenLabsTTS, "azure": AzureTTS, "google": GoogleTTS,
    "command": CommandTTS,
}
IMAGE_PROVIDERS: dict[str, Callable[[MediaConfig], Any]] = {
    "openai": OpenAIImage, "gemini": GeminiImage, "stability": StabilityImage, "replicate": ReplicateImage,
}


# ---------- ghép video bằng ffmpeg ----------


class FFmpegAssembler:
    """Ghép từng cảnh (ảnh + giọng đọc) rồi nối lại. Hai điều quan trọng:

    - KHÔNG cắt theo thời lượng ước lượng: cảnh dài đúng bằng giọng đọc (`-shortest`) cộng `tail_pad_s` giây im lặng
      (`apad`). Trước đây `-t <ước lượng>` cắt cụt câu khi TTS đọc dài hơn số từ chia 2,5.
    - `fit=cover` (mặc định): ảnh lấp đầy khung, cắt mép — provider trả ảnh 3:2 hay 1:1 vẫn ra khung 16:9/9:16 sạch.
      `fit=contain` giữ trọn ảnh và chấp nhận viền đen.

    File trung gian (segment, danh sách concat) nằm trong thư mục tạm và bị xoá, không lẫn vào `output/<video_id>/`.
    """

    def __init__(self, binary: str = "ffmpeg", fit: str = "cover", tail_pad_s: float = 0.35, font: Any = None,
                 motion: str = "auto", transition_s: float = 0.3, loudness_lufs: float | None = TARGET_LUFS):
        found = shutil.which(binary)
        if not found:
            raise MediaError("không tìm thấy ffmpeg trên PATH (đổi video.provider=fake để chạy offline)")
        self.binary = found
        self.fit = fit if fit in {"cover", "contain"} else "cover"
        self.tail_pad_s = max(0.0, float(tail_pad_s))
        self.font = Path(str(font)) if font else None
        self.motion = str(motion or "auto")
        # Chuyển cảnh ăn vào phần đệm im lặng cuối cảnh: giữ ≤ tail_pad_s thì giọng đọc hai cảnh KHÔNG BAO GIỜ chồng lên nhau.
        self.transition_s = min(max(0.0, float(transition_s)), self.tail_pad_s)
        self.loudness_lufs = float(loudness_lufs) if loudness_lufs else None

    def _run(self, args: list[str], cwd: Path | None = None) -> None:
        # encoding cố định: `text=True` trần sẽ giải mã theo bảng mã hệ thống, mà trên Windows đó là
        # cp1252 — thông báo lỗi của ffmpeg có đường dẫn tiếng Việt sẽ thành ký tự rác, hoặc ném
        # UnicodeDecodeError che mất chính lỗi cần đọc. `errors="replace"` để lỗi ffmpeg luôn tới
        # được người dùng, kể cả khi ffmpeg trả ra byte không hợp lệ.
        r = subprocess.run([self.binary, "-y", "-loglevel", "error", *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", cwd=str(cwd) if cwd else None)
        if r.returncode != 0:
            raise MediaError(f"ffmpeg lỗi: {r.stderr[-400:]}")

    def _scale(self, w: str, h: str) -> str:
        if self.fit == "contain":
            return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"

    def _motion_filter(self, kind: str, w: int, h: int, dur: float, fps: int) -> str:
        """Chuyển động nhẹ trên ảnh tĩnh (Ken Burns). Pan làm bằng `crop` cửa sổ chạy trên ảnh đã phóng to (rẻ, chính xác);
        zoom phải dùng `zoompan` vì kích thước cửa sổ đổi theo thời gian. Cả hai luôn ra đúng khung `w`x`h`."""
        frames = max(1, round(dur * fps))
        big_w, big_h = int(w * MOTION_ZOOM), int(h * MOTION_ZOOM)
        cover = f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h}"
        if kind in {"pan_left", "pan_right"}:
            # t/D chạy 0→1; pan_left đi từ phải sang trái nên đảo chiều
            p = f"min(1\\,t/{dur:.3f})" if kind == "pan_right" else f"(1-min(1\\,t/{dur:.3f}))"
            return f"{cover},crop={w}:{h}:x='(iw-ow)*{p}':y='(ih-oh)/2'"
        if kind in {"zoom_in", "zoom_out"}:
            z = (f"min(1+{MOTION_ZOOM - 1:.3f}*on/{frames},{MOTION_ZOOM})" if kind == "zoom_in"
                 else f"max({MOTION_ZOOM}-{MOTION_ZOOM - 1:.3f}*on/{frames},1)")
            return (f"{cover},zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}")
        return self._scale(str(w), str(h))

    def assemble(self, segments: list[tuple[Path, Path, float]], out: Path, fps: int, resolution: str) -> MediaResult:
        """Một lệnh ffmpeg cho cả video: mỗi cảnh là ảnh có chuyển động + giọng đọc, nối bằng `xfade`/`acrossfade`,
        rồi chuẩn hoá âm lượng về -14 LUFS. Phần chồng lấn của chuyển cảnh nằm trọn trong đoạn im lặng đệm cuối cảnh
        (`transition_s ≤ tail_pad_s`), nên video và tiếng luôn khớp nhau và không câu nào bị chồng."""
        out.parent.mkdir(parents=True, exist_ok=True)
        w, h = (int(x) for x in resolution.lower().split("x"))
        durs = [round((probe_duration(a) or d) + self.tail_pad_s, 3) for _, a, d in segments]
        t = self.transition_s if len(segments) > 1 else 0.0
        args: list[str] = []
        for (img, audio, _), d in zip(segments, durs, strict=True):
            args += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", str(img), "-i", str(audio)]
        chains: list[str] = []
        for i, d in enumerate(durs):
            mo = motion_for(i, self.motion)
            chains.append(f"[{2 * i}:v]{self._motion_filter(mo, w, h, d, fps)},fps={fps},format=yuv420p,setsar=1[v{i}]")
            chains.append(f"[{2 * i + 1}:a]aresample=48000,apad=pad_dur={self.tail_pad_s},atrim=0:{d:.3f},asetpts=N/SR/TB[a{i}]")
        vlast, alast = "v0", "a0"
        if len(durs) == 1:
            pass
        elif t <= 0:
            chains.append("".join(f"[v{i}][a{i}]" for i in range(len(durs))) + f"concat=n={len(durs)}:v=1:a=1[vc][ac]")
            vlast, alast = "vc", "ac"
        else:
            acc = durs[0]
            for i in range(1, len(durs)):
                nv, na = f"vx{i}", f"ax{i}"
                chains.append(f"[{vlast}][v{i}]xfade=transition=fade:duration={t}:offset={max(0.0, acc - t):.3f}[{nv}]")
                chains.append(f"[{alast}][a{i}]acrossfade=d={t}:c1=tri:c2=tri[{na}]")
                vlast, alast = nv, na
                acc = acc + durs[i] - t
        aout = alast
        if self.loudness_lufs is not None:
            chains.append(f"[{alast}]loudnorm=I={self.loudness_lufs}:TP={TRUE_PEAK_DB}:LRA=11[aout]"); aout = "aout"
        self._run([*args, "-filter_complex", ";".join(chains), "-map", f"[{vlast}]", "-map", f"[{aout}]",
                   "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)])
        total = round(sum(durs) - t * (len(durs) - 1), 2)
        loud = f" {self.loudness_lufs} LUFS" if self.loudness_lufs is not None else ""
        return MediaResult(out, "ffmpeg", "libx264", total,
                           notes=f"fit={self.fit} pad={self.tail_pad_s}s chuyển cảnh={t}s chuyển động={self.motion}{loud}")

    def finish_thumbnail(self, src: Path, out: Path, text: str = "", size: str = THUMBNAIL_SIZE,
                         max_bytes: int = THUMBNAIL_MAX_BYTES) -> MediaResult:
        """Ảnh nền (không chữ) → thumbnail đúng chuẩn nền tảng: 1280x720, chữ phủ do CODE vẽ, JPEG ≤ 2 MB.

        Chữ đi qua `textfile=` và font được chép vào thư mục tạm dùng làm cwd, nên tên file trong filtergraph luôn là
        `t.txt`/`f.ttf`: không phải escape dấu `:`/`'`/`\\` của đường dẫn hay của chính chữ tiếng Việt."""
        out.parent.mkdir(parents=True, exist_ok=True)
        w, h = (int(x) for x in size.lower().split("x"))
        wrapped = wrap_overlay(text) if text.strip() else []
        lines = wrapped[:MAX_OVERLAY_LINES]
        font = self.font if self.font and self.font.is_file() else find_font()
        tmp = Path(tempfile.mkdtemp(prefix=".studio-", dir=out.parent))
        try:
            vf = self._scale(str(w), str(h))
            note = f"{size}"
            if len(wrapped) > len(lines): note += f" CẮT {len(wrapped) - len(lines)} dòng chữ phủ (quá dài)"
            if lines and font is not None:
                shutil.copyfile(font, tmp / "f.ttf")
                (tmp / "t.txt").write_text("\n".join(lines), encoding="utf-8")
                longest = max(len(x) for x in lines)
                # Bề rộng chữ hoa của font đậm ≈ 0,75 em: hệ số rộng rãi để chữ không tràn mép khung (không đo được
                # bề rộng thật vì không có thư viện font); chiều cao khối ≈ 1,45 × cỡ chữ mỗi dòng kể cả line_spacing.
                fs = int(min(h * 0.80 / (len(lines) * 1.45), w * 0.90 / (0.75 * longest)))
                fs = max(24, min(fs, int(h * 0.22)))
                # Khối chữ đặt ở 72% chiều cao (tránh góc phải dưới, nơi nền tảng đè thời lượng video), nhưng luôn
                # nằm trong khung: `\,` là dấu phẩy của biểu thức ffmpeg, không phải dấu ngăn filter.
                margin = max(12, h // 30)
                vf += (f",drawtext=textfile=t.txt:fontfile=f.ttf:fontsize={fs}:fontcolor=white:line_spacing={fs // 5}"
                       f":borderw={max(2, fs // 12)}:bordercolor=black@0.9"
                       rf":x=max({margin}\,(w-text_w)/2):y=max({margin}\,min((h*0.72)-text_h/2\,h-text_h-{margin}))")
                note += f" chữ={len(lines)} dòng, cỡ {fs}"
            elif lines:
                note += " KHÔNG phủ được chữ: không tìm thấy font (đặt video.font trong media.yaml)"
            for q in (3, 6, 9):
                self._run(["-i", str(src.resolve()), "-vf", vf, "-frames:v", "1", "-q:v", str(q), str(out.resolve())],
                          cwd=tmp)
                if out.stat().st_size <= max_bytes: break
            size_b = out.stat().st_size
            note += f", {size_b // 1024} KB" + (f" (VƯỢT {max_bytes // 1_000_000} MB)" if size_b > max_bytes else "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return MediaResult(out, "ffmpeg", "mjpeg", notes=note)
