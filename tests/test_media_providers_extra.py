"""Provider media mới — TTS: gemini, elevenlabs, azure, google, command; ảnh: gemini (Gemini Image / Imagen), stability,
replicate — qua urlopen giả, không chạm mạng, không cần key thật. Kèm tiện ích chung: dịch giọng, pace, mã ngôn ngữ,
tỷ lệ khung, khóa API theo kênh, đo thời lượng audio từ file."""
from __future__ import annotations

import base64
import io
import json
import shlex
import sys
import wave
from pathlib import Path
from typing import Any, ClassVar

import pytest

from studio import media
from studio.media import (
    AzureTTS,
    CommandTTS,
    EdgeTTS,
    ElevenLabsTTS,
    GeminiImage,
    GeminiTTS,
    GoogleTTS,
    MediaConfig,
    MediaError,
    OpenAITTS,
    ReplicateImage,
    StabilityImage,
    aspect_of,
    audio_duration,
    language_code,
    load_media_config,
    make_media,
    pace_of,
    pick_voice,
    require_key,
    section_api_key,
)

KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "AZURE_SPEECH_KEY", "SPEECH_KEY",
        "STABILITY_API_KEY", "REPLICATE_API_TOKEN", "STUDIO_MEDIA_API_KEY", "STUDIO_LLM_API_KEY", "OPENAI_API_KEY")


class _Resp:
    def __init__(self, data: bytes): self._data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._data


def _capture(monkeypatch, responses: list[bytes]) -> list[dict[str, Any]]:
    """urlopen giả: ghi lại từng request (url, method, header viết thường, body) và trả lần lượt các phản hồi."""
    calls: list[dict[str, Any]] = []; queue = list(responses)

    def fake_urlopen(req, timeout=None):
        if isinstance(req, str):
            calls.append({"url": req, "method": "GET", "headers": {}, "body": None})
        else:
            calls.append({"url": req.full_url, "method": req.get_method(), "body": req.data,
                          "headers": {k.lower(): v for k, v in req.header_items()}})
        return _Resp(queue.pop(0))

    monkeypatch.setattr("studio.media.urllib.request.urlopen", fake_urlopen)
    return calls


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # không có ffprobe → mp3 giả không đo được, về ước lượng: test xác định trên mọi máy (CI có ffmpeg/ffprobe)
    monkeypatch.setattr(media.shutil, "which", lambda b: None)
    for k in KEYS: monkeypatch.delenv(k, raising=False)


# ---------- tiện ích ----------

def test_pick_voice_rules():
    assert pick_voice({}, {"voice_id": "nova"}, "alloy") == "nova"                     # không bảng, tin manifest
    assert pick_voice({"voice": "coral"}, {}, "alloy") == "coral"                       # manifest không có id → tts.voice
    assert pick_voice({}, {}, "alloy") == "alloy"                                        # → mặc định provider
    table = {"voices": {"alloy": "vi-VN-HoaiMyNeural"}, "voice": "vi-VN-NamMinhNeural"}
    assert pick_voice(table, {"voice_id": "alloy"}, "x") == "vi-VN-HoaiMyNeural"        # bảng dịch
    assert pick_voice(table, {"voice_id": "onyx"}, "x") == "vi-VN-NamMinhNeural"        # id lạ → tts.voice
    assert pick_voice({"voice": "abc123"}, {"voice_id": "alloy"}, "", trust_manifest=False) == "abc123"  # id riêng của provider
    assert pick_voice({}, {"voice_id": "alloy"}, "Kore", trust_manifest=False) == "Kore"


def test_pace_language_and_aspect_helpers():
    assert pace_of({}) == "medium" and pace_of({"pace": "SLOW"}) == "slow" and pace_of({"pace": "turbo"}) == "medium"
    assert language_code({"language": "vi"}, {}) == "vi-VN" and language_code({"language": "en-GB"}, {}) == "en-GB"
    assert language_code({}, {"language": "ja"}) == "ja-JP"
    assert language_code({}, {}, "vi-VN-Neural2-A") == "vi-VN" and language_code({}, {}) == "vi-VN"
    assert aspect_of("1792x1024") == "16:9" and aspect_of("1536x1024") == "3:2" and aspect_of("1024x1792") == "9:16"
    assert aspect_of("1024x1024") == "1:1" and aspect_of("rác") == "16:9"
    assert aspect_of("2520x1080", media.STABILITY_ASPECTS) == "21:9"


def test_section_api_key_order_and_require(monkeypatch):
    cfg = MediaConfig(api_key="chung")
    assert section_api_key({"api_key": "rieng"}, cfg, "GEMINI_API_KEY") == "rieng"
    monkeypatch.setenv("MY_KEY", "tu-bien"); monkeypatch.setenv("GEMINI_API_KEY", "gem")
    assert section_api_key({"api_key_env": "MY_KEY"}, cfg, "GEMINI_API_KEY") == "tu-bien"
    assert section_api_key({}, cfg, "GEMINI_API_KEY") == "gem"
    monkeypatch.delenv("GEMINI_API_KEY")
    assert section_api_key({}, cfg, "GEMINI_API_KEY") == "chung"
    assert section_api_key({}, MediaConfig(), "GEMINI_API_KEY") is None
    with pytest.raises(MediaError, match="GEMINI_API_KEY"):
        require_key("tts", "gemini", None, "GEMINI_API_KEY")
    assert require_key("tts", "gemini", "k", "GEMINI_API_KEY") == "k"


def _wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(b"\x00" * int(seconds * rate * 2))
    return path


def test_audio_duration_wav_header_ffprobe_and_fallback(tmp_path, monkeypatch):
    import subprocess
    assert audio_duration(_wav(tmp_path / "a.wav", 1.5), "một hai ba") == 1.5
    mp3 = tmp_path / "b.mp3"; mp3.write_bytes(b"MP3?")
    assert audio_duration(mp3, "một hai ba bốn năm") == 2.0                                  # không ffprobe → 5 từ / 2.5
    monkeypatch.setattr(media.shutil, "which", lambda b: "/usr/bin/ffprobe")
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "3.2149\n", ""))
    assert audio_duration(mp3, "x") == 3.21
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "hỏng"))
    assert audio_duration(mp3, "một hai ba bốn năm") == 2.0
    (tmp_path / "c.wav").write_bytes(b"RIFFrac-hong")                                         # WAV hỏng → không ném
    assert audio_duration(tmp_path / "c.wav", "một hai ba bốn năm") == 2.0


def test_base_url_env_applies_only_to_openai_sections(tmp_path, monkeypatch):
    p = tmp_path / "media.yaml"
    p.write_text("tts:\n  provider: gemini\nimage:\n  provider: openai\n", encoding="utf-8")
    monkeypatch.setenv("STUDIO_MEDIA_BASE_URL", "http://127.0.0.1:1123/v1")
    cfg = load_media_config(p)
    assert "base_url" not in cfg.tts and cfg.image["base_url"] == "http://127.0.0.1:1123/v1"


def test_make_media_registry_unknown_and_missing_key():
    suite = make_media(MediaConfig(tts={"provider": "gemini", "api_key": "k"}, image={"provider": "stability", "api_key": "k"}))
    assert isinstance(suite.tts, GeminiTTS) and isinstance(suite.image, StabilityImage)
    assert suite.names == {"tts": "gemini", "image": "stability", "video": "fake"}
    with pytest.raises(MediaError, match=r"provider lạ `foo`.*gemini.*elevenlabs"):
        make_media(MediaConfig(tts={"provider": "foo"}))
    with pytest.raises(MediaError, match="REPLICATE_API_TOKEN"):
        make_media(MediaConfig(image={"provider": "replicate"}))
    with pytest.raises(MediaError, match="ELEVENLABS_API_KEY"):
        make_media(MediaConfig(tts={"provider": "elevenlabs"}))


# ---------- TTS ----------

def test_openai_tts_pace_instructions_and_voice_table(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, [b"MP3"])
    cfg = MediaConfig(api_key="sk", tts={"provider": "openai", "instructions": "giọng ấm", "voices": {"alloy": "coral"}})
    r = OpenAITTS(cfg).synthesize("xin chào", {"voice_id": "alloy", "pace": "slow"}, tmp_path / "a.wav")
    body = json.loads(calls[0]["body"])
    assert body["voice"] == "coral" and body["speed"] == 0.9 and body["instructions"] == "giọng ấm"
    assert r.path.suffix == ".mp3" and r.duration_s == 1.0


def test_gemini_tts_pcm_to_wav_measures_duration(tmp_path, monkeypatch):
    pcm = b"\x01\x00" * 24000  # 1 giây ở 24 kHz, 16-bit mono
    resp = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000",
                                                                  "data": base64.b64encode(pcm).decode()}}]}}]}
    calls = _capture(monkeypatch, [json.dumps(resp).encode()])
    cfg = MediaConfig(tts={"provider": "gemini", "api_key": "gk", "style": "Giọng kể chuyện ấm", "model": "gemini-2.5-flash-preview-tts"})
    r = GeminiTTS(cfg).synthesize("Xin chào Việt Nam", {"voice_id": "Puck", "pace": "slow"}, tmp_path / "S1.wav")
    assert r.provider == "gemini" and r.model == "gemini-2.5-flash-preview-tts" and r.duration_s == 1.0
    with wave.open(str(r.path), "rb") as w:
        assert w.getframerate() == 24000 and w.getnframes() == 24000 and w.getsampwidth() == 2
    c = calls[0]
    assert c["url"].endswith("/models/gemini-2.5-flash-preview-tts:generateContent") and c["headers"]["x-goog-api-key"] == "gk"
    body = json.loads(c["body"])
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Puck"
    assert body["contents"][0]["parts"][0]["text"] == "Giọng kể chuyện ấm, Đọc chậm rãi, rõ từng chữ: Xin chào Việt Nam"


def test_gemini_tts_non_pcm_mime_and_missing_inline(tmp_path, monkeypatch):
    ok = {"candidates": [{"content": {"parts": [{"text": "…"}, {"inlineData": {"mimeType": "audio/mp3", "data": base64.b64encode(b"MP3").decode()}}]}}]}
    _capture(monkeypatch, [json.dumps(ok).encode(), json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()])
    tts = GeminiTTS(MediaConfig(tts={"provider": "gemini", "api_key": "gk", "model": "gemini-2.5-pro-preview-tts"}))
    r = tts.synthesize("một hai ba bốn năm", {}, tmp_path / "a.wav")
    assert r.path.suffix == ".mp3" and r.path.read_bytes() == b"MP3" and r.duration_s == 2.0
    with pytest.raises(MediaError, match=r"không có inlineData.*SAFETY"):
        tts.synthesize("x", {}, tmp_path / "b.wav")


def _wav_bytes(seconds: float, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


def test_gemini_tts_38_uses_interactions_api_and_keeps_text_verbatim(tmp_path, monkeypatch):
    """Gemini 3.8 TTS (Interactions API): `text` là transcript đọc NGUYÊN VĂN — câu dẫn phong cách đi trong
    speech_metadata, không bao giờ nối vào text (3.8 sẽ đọc nó thành tiếng). Unary trả WAV 24 kHz."""
    resp = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "…"},
                                                           {"type": "audio", "data": base64.b64encode(_wav_bytes(1.0)).decode()}]}]}
    calls = _capture(monkeypatch, [json.dumps(resp).encode()])
    cfg = MediaConfig(tts={"provider": "gemini", "api_key": "gk", "style": "warm storyteller"})
    r = GeminiTTS(cfg).synthesize("Xin chào Việt Nam", {"voice_id": "Puck", "pace": "slow"}, tmp_path / "S1.wav")
    assert r.provider == "gemini" and r.model == "gemini-3.8-flash-lite-tts" and r.duration_s == 1.0 and r.path.suffix == ".wav"
    c = calls[0]
    assert c["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions" and c["headers"]["x-goog-api-key"] == "gk"
    body = json.loads(c["body"])
    assert body["model"] == "gemini-3.8-flash-lite-tts" and body["response_format"] == {"type": "audio"}
    assert body["generation_config"] == {"speech_config": [{"voice": "Puck"}]}
    block = body["input"][0]["content"][0]
    assert body["input"][0]["type"] == "user_input" and block["type"] == "text" and block["text"] == "Xin chào Việt Nam"
    assert block["annotations"] == [{"type": "speech_metadata", "style": "warm storyteller, speaking slowly and clearly"}]


def test_gemini_tts_38_pcm_fallback_no_style_and_missing_audio(tmp_path, monkeypatch):
    pcm = b"\x01\x00" * 12000  # 0,5 s PCM thô (streaming/l16) không có RIFF → bọc WAV
    ok = {"steps": [{"type": "model_output", "content": [{"type": "audio", "data": base64.b64encode(pcm).decode()}]}]}
    calls = _capture(monkeypatch, [json.dumps(ok).encode(), json.dumps({"steps": [{"type": "model_output", "content": []}],
                                                                          "error": {"message": "SAFETY"}}).encode()])
    tts = GeminiTTS(MediaConfig(tts={"provider": "gemini", "api_key": "gk", "model": "gemini-3.8-flash-tts"}))
    r = tts.synthesize("một hai", {"pace": "medium"}, tmp_path / "a.wav")
    assert r.duration_s == 0.5 and r.path.suffix == ".wav"
    with wave.open(str(r.path), "rb") as w:
        assert w.getframerate() == 24000 and w.getnframes() == 12000
    block = json.loads(calls[0]["body"])["input"][0]["content"][0]
    assert "annotations" not in block and json.loads(calls[0]["body"])["generation_config"] == {"speech_config": [{"voice": "Kore"}]}
    with pytest.raises(MediaError, match=r"không có audio.*SAFETY"):
        tts.synthesize("x", {}, tmp_path / "b.wav")


def test_elevenlabs_tts_uses_provider_voice_id_and_speed(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, [b"MP3"])
    cfg = MediaConfig(tts={"provider": "elevenlabs", "api_key": "xi", "voice": "abc123", "model": "eleven_flash_v2_5"})
    r = ElevenLabsTTS(cfg).synthesize("xin chào", {"voice_id": "alloy", "pace": "fast"}, tmp_path / "a.wav")
    c = calls[0]
    assert c["url"] == "https://api.elevenlabs.io/v1/text-to-speech/abc123?output_format=mp3_44100_128"
    assert c["headers"]["xi-api-key"] == "xi" and c["headers"]["accept"] == "audio/mpeg"
    body = json.loads(c["body"])
    assert body["model_id"] == "eleven_flash_v2_5" and body["voice_settings"]["speed"] == 1.1 and body["text"] == "xin chào"
    assert r.path.suffix == ".mp3" and r.provider == "elevenlabs"
    default = ElevenLabsTTS(MediaConfig(tts={"provider": "elevenlabs", "api_key": "xi"}))
    assert default.model == "eleven_flash_v2_5"  # eleven_multilingual_v2 KHÔNG có tiếng Việt (docs models, 14/10/2026)
    with pytest.raises(MediaError, match="cần voice_id"):
        default.synthesize("x", {"voice_id": "alloy"}, tmp_path / "b.wav")


def test_elevenlabs_timestamps_return_word_times(tmp_path, monkeypatch):
    """`tts.timestamps: true` → /with-timestamps: audio base64 + mốc từng KÝ TỰ → gộp thành mốc từng từ."""
    chars = list("xin  chào")
    starts = [0.0, 0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7]
    ends = [0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.9]
    payload = {"audio_base64": base64.b64encode(b"MP3").decode(),
               "alignment": {"characters": chars, "character_start_times_seconds": starts, "character_end_times_seconds": ends}}
    calls = _capture(monkeypatch, [json.dumps(payload).encode()])
    cfg = MediaConfig(tts={"provider": "elevenlabs", "api_key": "xi", "voice": "abc123", "timestamps": True})
    r = ElevenLabsTTS(cfg).synthesize("xin  chào", {"voice_id": "alloy"}, tmp_path / "a.wav")
    assert calls[0]["url"] == "https://api.elevenlabs.io/v1/text-to-speech/abc123/with-timestamps?output_format=mp3_44100_128"
    assert calls[0]["headers"]["accept"] == "application/json"
    assert r.path.read_bytes() == b"MP3" and r.words == [(0.0, 0.3, "xin"), (0.4, 0.9, "chào")]
    empty = {"characters": [], "character_start_times_seconds": [], "character_end_times_seconds": []}
    for bad in ({"alignment": None}, {"audio_base64": "***"}, {"audio_base64": "", "alignment": empty}):
        _capture(monkeypatch, [json.dumps(bad).encode()])
        with pytest.raises(MediaError, match="with-timestamps"):
            ElevenLabsTTS(cfg).synthesize("xin chào", {"voice_id": "alloy"}, tmp_path / "b.wav")
    _capture(monkeypatch, [json.dumps({"audio_base64": base64.b64encode(b"MP3").decode(), "alignment": None}).encode()])
    with pytest.raises(MediaError, match="with-timestamps"):
        ElevenLabsTTS(cfg).synthesize("xin chào", {"voice_id": "alloy"}, tmp_path / "c.wav")


class _Communicate:
    """edge_tts.Communicate giả: không mạng; ghi lại tham số, phát audio + WordBoundary (đơn vị 100 ns)."""
    seen: ClassVar[list[dict[str, Any]]] = []
    chunks: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, text, voice, **kw):
        type(self).seen.append({"text": text, "voice": voice, **kw})

    async def stream(self):
        for chunk in type(self).chunks:
            yield chunk


def test_edge_tts_native_returns_audio_and_word_times(tmp_path, monkeypatch):
    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", _Communicate)
    _Communicate.seen = []
    _Communicate.chunks = [{"type": "audio", "data": b"ID3"}, {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": "Xin"},
                           {"type": "audio", "data": b"MP3"}, {"type": "WordBoundary", "offset": 4_000_000, "duration": 3_000_000, "text": "chào"},
                           {"type": "SentenceBoundary"}]
    cfg = MediaConfig(tts={"provider": "edge", "voices": {"alloy": "vi-VN-NamMinhNeural"}})
    tts = make_media(cfg).tts
    assert isinstance(tts, EdgeTTS)
    r = tts.synthesize("Xin chào", {"voice_id": "alloy", "pace": "slow"}, tmp_path / "a.wav")
    assert r.path.suffix == ".mp3" and r.path.read_bytes() == b"ID3MP3" and r.provider == "edge"
    assert r.words == [(0.1, 0.3, "Xin"), (0.4, 0.7, "chào")] and r.model == "vi-VN-NamMinhNeural"
    assert _Communicate.seen[0] == {"text": "Xin chào", "voice": "vi-VN-NamMinhNeural", "rate": "-10%",
                                    "boundary": "WordBoundary", "connect_timeout": 15, "receive_timeout": 60}
    assert EdgeTTS(MediaConfig(tts={"provider": "edge"})).synthesize("x", {"voice_id": "alloy"}, tmp_path / "d.wav").model \
        == "vi-VN-HoaiMyNeural"
    _Communicate.chunks = [{"type": "WordBoundary", "offset": 0, "duration": 1, "text": "x"}]
    with pytest.raises(MediaError, match="edge-tts"):
        tts.synthesize("x", {"voice_id": "alloy"}, tmp_path / "b.wav")

    class Broken(_Communicate):
        async def stream(self):
            raise OSError("403 Sec-MS-GEC")
            yield  # pragma: no cover
    monkeypatch.setattr(edge_tts, "Communicate", Broken)
    with pytest.raises(MediaError, match=r"edge-tts.*403"):
        tts.synthesize("x", {"voice_id": "alloy"}, tmp_path / "c.wav")


def test_azure_tts_builds_ssml_with_voice_table_and_prosody(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, [b"MP3", b"MP3"])
    cfg = MediaConfig(tts={"provider": "azure", "api_key": "az", "voices": {"alloy": "vi-VN-NamMinhNeural"}, "voice": "vi-VN-HoaiMyNeural"})
    r = AzureTTS(cfg).synthesize("Lãi & lỗ <5%>", {"voice_id": "alloy", "pace": "slow", "language": "vi"}, tmp_path / "a.wav")
    c = calls[0]; ssml = c["body"].decode("utf-8")
    assert c["url"] == "https://southeastasia.tts.speech.microsoft.com/cognitiveservices/v1"
    assert c["headers"]["ocp-apim-subscription-key"] == "az" and c["headers"]["x-microsoft-outputformat"] == "audio-24khz-96kbitrate-mono-mp3"
    assert 'xml:lang="vi-VN"' in ssml and '<voice name="vi-VN-NamMinhNeural">' in ssml
    assert '<prosody rate="-10%">Lãi &amp; lỗ &lt;5%&gt;</prosody>' in ssml and r.model == "vi-VN-NamMinhNeural"
    # id lạ → tts.voice, pace medium → không prosody; region riêng
    cfg2 = MediaConfig(tts={"provider": "azure", "api_key": "az", "region": "eastasia", "voice": "vi-VN-HoaiMyNeural"})
    AzureTTS(cfg2).synthesize("Chào", {"voice_id": "nova"}, tmp_path / "b.wav")
    assert calls[1]["url"].startswith("https://eastasia.") and "<prosody" not in calls[1]["body"].decode() and "vi-VN-HoaiMyNeural" in calls[1]["body"].decode()


def test_google_tts_synthesize_and_missing_audio(tmp_path, monkeypatch):
    _capture(monkeypatch, [json.dumps({"audioContent": base64.b64encode(b"MP3").decode()}).encode(), b"{}"])
    calls = _capture(monkeypatch, [json.dumps({"audioContent": base64.b64encode(b"MP3").decode()}).encode(), b"{}"])
    tts = GoogleTTS(MediaConfig(tts={"provider": "google", "api_key": "gg", "voice": "vi-VN-Neural2-D"}))
    r = tts.synthesize("xin chào", {"voice_id": "alloy", "pace": "fast"}, tmp_path / "a.wav")
    c = calls[0]; body = json.loads(c["body"])
    assert c["url"] == "https://texttospeech.googleapis.com/v1/text:synthesize" and c["headers"]["x-goog-api-key"] == "gg"
    assert body["voice"] == {"languageCode": "vi-VN", "name": "vi-VN-Neural2-D"} and body["audioConfig"]["speakingRate"] == 1.15
    assert r.path.read_bytes() == b"MP3" and r.model == "vi-VN-Neural2-D"
    with pytest.raises(MediaError, match="audioContent"):
        tts.synthesize("x", {}, tmp_path / "b.wav")


def _script(tmp_path: Path, body: str) -> str:
    s = tmp_path / "tts_cmd.py"; s.write_text(body, encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(s))}"


def test_command_tts_runs_local_command_with_placeholders(tmp_path):
    cmd = _script(tmp_path, "import sys, wave\n"
                            "assert sys.stdin.read() == 'xin chào'\n"
                            "assert sys.argv[2] == 'vi-VN-HoaiMyNeural' and sys.argv[3] == 'vi-VN' and sys.argv[4] == 'slow'\n"
                            "w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)\n"
                            "w.writeframes(b'\\x00' * 8000); w.close()\n")
    cfg = MediaConfig(tts={"provider": "command", "command": cmd + " {out} {voice} {lang} {pace}", "voice": "vi-VN-HoaiMyNeural",
                           "model": "piper-vi"})
    r = CommandTTS(cfg).synthesize("xin chào", {"voice_id": "alloy", "pace": "slow", "language": "vi"}, tmp_path / "S1.wav")
    assert r.provider == "command" and r.model == "piper-vi" and r.path == tmp_path / "S1.wav" and r.duration_s == 0.5


def test_command_tts_errors(tmp_path):
    with pytest.raises(MediaError, match=r"cần tts\.command"):
        CommandTTS(MediaConfig(tts={"provider": "command"}))
    bad = _script(tmp_path, "import sys; sys.stderr.write('model khong ton tai'); sys.exit(3)\n")
    with pytest.raises(MediaError, match=r"lỗi \(3\): model khong ton tai"):
        CommandTTS(MediaConfig(tts={"provider": "command", "command": bad + " {out}"})).synthesize("x", {}, tmp_path / "a.wav")
    silent = _script(tmp_path, "import sys; sys.stdin.read()\n")
    with pytest.raises(MediaError, match="không tạo file"):
        CommandTTS(MediaConfig(tts={"provider": "command", "command": silent + " {out}"})).synthesize("x", {}, tmp_path / "b.wav")
    with pytest.raises(MediaError, match="không chạy được"):
        CommandTTS(MediaConfig(tts={"provider": "command", "command": "/khong/co/binary/nao {out}"})).synthesize("x", {}, tmp_path / "c.wav")


# ---------- Ảnh ----------

def test_gemini_image_generatecontent_png_and_jpeg(tmp_path, monkeypatch):
    png = b"\x89PNGabc"; jpg = b"\xff\xd8\xffjpg"
    r1 = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": base64.b64encode(png).decode()}}]}}]}
    r2 = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(jpg).decode()}}]}}]}
    calls = _capture(monkeypatch, [json.dumps(r1).encode(), json.dumps(r2).encode()])
    gen = GeminiImage(MediaConfig(image={"provider": "gemini", "api_key": "gk"}))
    a = gen.generate("bàn làm việc", "1792x1024", tmp_path / "S1.png")
    assert a.path == tmp_path / "S1.png" and a.path.read_bytes() == png and a.model == "gemini-3.1-flash-lite-image"
    body = json.loads(calls[0]["body"])
    assert calls[0]["url"].endswith("/models/gemini-3.1-flash-lite-image:generateContent") and calls[0]["headers"]["x-goog-api-key"] == "gk"
    assert body["generationConfig"] == {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}}
    assert body["contents"][0]["parts"][0]["text"] == "bàn làm việc"
    gen2 = GeminiImage(MediaConfig(image={"provider": "gemini", "api_key": "gk", "model": "gemini-nano-banana-2.1", "image_size": "2k"}))
    b = gen2.generate("sơ đồ", "1024x1792", tmp_path / "S2.png")
    assert b.path == tmp_path / "S2.jpg" and b.path.read_bytes() == jpg and b.model == "gemini-nano-banana-2.1"
    assert json.loads(calls[1]["body"])["generationConfig"]["imageConfig"] == {"aspectRatio": "9:16", "imageSize": "2K"}
    with pytest.raises(MediaError, match="image_size"):
        GeminiImage(MediaConfig(image={"provider": "gemini", "api_key": "gk", "image_size": "8K"}))


def test_gemini_image_imagen_predict_and_errors(tmp_path, monkeypatch):
    png = b"\x89PNGimagen"
    calls = _capture(monkeypatch, [json.dumps({"predictions": [{"bytesBase64Encoded": base64.b64encode(png).decode(), "mimeType": "image/png"}]}).encode(),
                                   json.dumps({"predictions": [{}]}).encode()])
    gen = GeminiImage(MediaConfig(image={"provider": "gemini", "api_key": "gk", "model": "imagen-4.0-generate-001"}))
    r = gen.generate("p", "1024x1024", tmp_path / "a.png")
    assert r.path.read_bytes() == png and r.model == "imagen-4.0-generate-001"
    assert calls[0]["url"].endswith("/models/imagen-4.0-generate-001:predict")
    assert json.loads(calls[0]["body"]) == {"instances": [{"prompt": "p"}], "parameters": {"sampleCount": 1, "aspectRatio": "1:1"}}
    with pytest.raises(MediaError, match="bytesBase64Encoded"):
        gen.generate("p", "1024x1024", tmp_path / "b.png")
    _capture(monkeypatch, [json.dumps({"candidates": [{"finishReason": "IMAGE_SAFETY", "content": {"parts": []}}]}).encode()])
    with pytest.raises(MediaError, match="IMAGE_SAFETY"):
        GeminiImage(MediaConfig(image={"provider": "gemini", "api_key": "gk"})).generate("p", "1024x1024", tmp_path / "c.png")


def test_stability_multipart_form_and_empty(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, [b"PNGBYTES", b""])
    cfg = MediaConfig(image={"provider": "stability", "api_key": "st", "model": "sd3", "negative_prompt": "text, logo",
                             "extra": {"model": "sd3.5-large"}})
    r = StabilityImage(cfg).generate("một cảnh", "1792x1024", tmp_path / "a.png")
    c = calls[0]; ctype = c["headers"]["content-type"]; body = c["body"].decode("utf-8")
    assert c["url"] == "https://api.stability.ai/v2beta/stable-image/generate/sd3" and c["headers"]["authorization"] == "Bearer st"
    assert c["headers"]["accept"] == "image/*" and ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=")[1]
    assert body.count(f"--{boundary}") == 6 and body.endswith(f"--{boundary}--\r\n")
    for k, v in (("prompt", "một cảnh"), ("aspect_ratio", "16:9"), ("output_format", "png"), ("negative_prompt", "text, logo"), ("model", "sd3.5-large")):
        assert f'name="{k}"\r\n\r\n{v}\r\n' in body
    assert r.path.read_bytes() == b"PNGBYTES" and r.provider == "stability" and r.model == "sd3"
    with pytest.raises(MediaError, match="rỗng"):
        StabilityImage(cfg).generate("p", "1024x1024", tmp_path / "b.png")


def test_replicate_waits_polls_and_downloads_through_url_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(media.time, "sleep", lambda s: None)
    monkeypatch.setattr("studio.media.check_url", lambda url: url)
    pending = {"status": "processing", "urls": {"get": "https://api.replicate.com/v1/predictions/p1"}}
    done = {"status": "succeeded", "output": ["https://replicate.delivery/x/out.png"]}
    calls = _capture(monkeypatch, [json.dumps(pending).encode(), json.dumps(done).encode(), b"PNGOUT"])
    cfg = MediaConfig(image={"provider": "replicate", "api_key": "rp", "input": {"num_inference_steps": 4}})
    r = ReplicateImage(cfg).generate("p", "1024x1792", tmp_path / "a.png")
    assert r.path.read_bytes() == b"PNGOUT" and r.model == "black-forest-labs/flux-schnell"
    assert calls[0]["url"] == "https://api.replicate.com/v1/models/black-forest-labs/flux-schnell/predictions"
    assert calls[0]["headers"]["prefer"] == "wait=60" and calls[0]["headers"]["authorization"] == "Bearer rp"
    assert json.loads(calls[0]["body"]) == {"input": {"prompt": "p", "aspect_ratio": "9:16", "output_format": "png", "num_inference_steps": 4}}
    assert calls[1]["method"] == "GET" and calls[1]["url"] == "https://api.replicate.com/v1/predictions/p1"
    assert calls[2]["url"] == "https://replicate.delivery/x/out.png"


def test_replicate_failed_and_no_output(tmp_path, monkeypatch):
    _capture(monkeypatch, [json.dumps({"status": "failed", "error": "NSFW"}).encode(), json.dumps({"status": "succeeded", "output": None}).encode()])
    gen = ReplicateImage(MediaConfig(image={"provider": "replicate", "api_key": "rp", "poll_max": 1}))
    with pytest.raises(MediaError, match="replicate: failed: NSFW"):
        gen.generate("p", "1024x1024", tmp_path / "a.png")
    with pytest.raises(MediaError, match="không có URL"):
        gen.generate("p", "1024x1024", tmp_path / "b.png")


def test_replicate_blocks_private_output_url(tmp_path, monkeypatch):
    _capture(monkeypatch, [json.dumps({"status": "succeeded", "output": "http://169.254.169.254/latest"}).encode()])
    with pytest.raises(MediaError, match="URL ảnh bị chặn"):
        ReplicateImage(MediaConfig(image={"provider": "replicate", "api_key": "rp"})).generate("p", "1024x1024", tmp_path / "a.png")


def test_command_tts_ep_stdin_utf8_du_bang_ma_cuc_bo_cua_con_khac(tmp_path, monkeypatch):
    """Tiến trình con giải mã stdin theo bảng mã cục bộ CỦA NÓ, không theo bảng mã ta ghi. Trên Windows
    mặc định là cp1252, nên "xin chào" tới lệnh TTS thành chữ hỏng — và hỏng im lặng: lệnh vẫn thoát 0,
    vẫn ra file audio, chỉ có giọng đọc là sai. Đặt PYTHONIOENCODING ở tiến trình CHA để dựng lại đúng
    cảnh đó trên MỌI hệ điều hành: bản sửa phải đè lên nó. Bỏ `env=` trong `synthesize` thì test này ĐỎ."""
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    cmd = _script(tmp_path, "import sys, wave\n"
                            "got = sys.stdin.read()\n"
                            "assert got == 'xin chào', repr(got)\n"
                            "w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)\n"
                            "w.writeframes(b'\\x00' * 8000); w.close()\n")
    cfg = MediaConfig(tts={"provider": "command", "command": cmd + " {out}"})
    r = CommandTTS(cfg).synthesize("xin chào", {}, tmp_path / "S1.wav")
    assert r.path.is_file()


def test_command_image_preserves_prompt_stdin_and_filters_credentials(tmp_path, monkeypatch):
    from studio.media import CommandImage, _solid_png
    from studio.sandbox import Result
    prompt = "ảnh tiếng Việt; $(touch should-not-exist)"
    monkeypatch.setenv("OPENAI_API_KEY", "never-forward-this")
    class Recorder:
        name = "test"
        def run(self, spec):
            assert spec.argv == ["paint", "11x7", str(tmp_path / "image.png")]
            assert spec.stdin == prompt and spec.env["PYTHONIOENCODING"] == "utf-8"
            assert "OPENAI_API_KEY" not in spec.env
            Path(spec.argv[-1]).write_bytes(_solid_png(11, 7, (25, 70, 90)))
            return Result(0, "", "", False, "test")
    cfg = MediaConfig(image={"provider": "command", "command": "paint {size} {out}", "model": "local-art"})
    result = CommandImage(cfg, sandbox=Recorder()).generate(prompt, "11x7", tmp_path / "image.png")
    assert result.provider == "command" and result.model == "local-art"
    assert result.path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert isinstance(make_media(cfg).image, CommandImage)


@pytest.mark.parametrize("mode,match", [("timeout", "quá"), ("exit", "lỗi"), ("missing", "PNG"),
                                         ("invalid", "PNG"), ("oserror", "không chạy")])
def test_command_image_rejects_failed_or_invalid_output(tmp_path, mode, match):
    from studio.media import CommandImage
    from studio.sandbox import Result
    class Recorder:
        name = "test"
        def run(self, spec):
            if mode == "oserror": raise OSError("missing executable")
            if mode == "invalid": Path(spec.argv[-1]).write_text("placeholder")
            return Result(1 if mode == "exit" else 0, "", "renderer failed", mode == "timeout", "test")
    image = CommandImage(MediaConfig(image={"provider": "command", "command": "paint {out}"}), sandbox=Recorder())
    with pytest.raises(MediaError, match=match): image.generate("p", "11x7", tmp_path / "out.png")


def test_command_image_requires_explicit_command():
    from studio.media import CommandImage
    with pytest.raises(MediaError, match=r"image\.command"):
        CommandImage(MediaConfig(image={"provider": "command"}))


def test_gpt_image_2_accepts_custom_sizes_within_the_api_rules():
    """gpt-image-2.x nhận WIDTHxHEIGHT tuỳ ý (bội 16, tỷ lệ 1:3…3:1, cạnh ≤ 3840): 16:9 thật thay vì cắt 1536x1024."""
    from studio.media import image_size
    cfg = MediaConfig(image={"provider": "openai", "model": "gpt-image-2.5-flare"})
    assert image_size(cfg, "16:9") == "1920x1088" and image_size(cfg, "9:16") == "1088x1920"
    assert image_size(cfg, "1:1") == "1024x1024"
    cfg.image["size"] = "2560x1440"
    assert image_size(cfg, "16:9") == "2560x1440"
    for bad in ("1920x1080", "4096x2304", "3840x1024", "abc"):   # không bội 16 / quá 3840 / tỷ lệ > 3:1 / sai dạng
        cfg.image["size"] = bad
        assert image_size(cfg, "16:9") == "1920x1088"
