"""QC bằng CODE trên file thật (không phải trên manifest).

Ba reviewer của khối chất lượng đọc JSON: họ không mở được file, không nghe được tiếng. Nên "hook ≤ 5 giây",
"thời lượng đúng", "không cảnh trống" trước đây không ai kiểm được trước gate publish. Module này đo file bằng
ffprobe/ffmpeg và trả về những con số đó, để quality-reviewer đọc trong `package` và người duyệt thấy trong checklist.

Không có ffprobe trên máy (chế độ `fake`, CI tối giản) thì báo cáo nói thẳng là không đo được, chứ không đoán bừa.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import Finding
from .media import (
    PLATFORM_THUMBNAIL_MAX_BYTES,
    TARGET_LUFS,
    THUMBNAIL_MAX_BYTES,
    THUMBNAIL_SIZE,
    TRUE_PEAK_DB,
    WORDS_PER_SECOND,
    load_media_config,
    probe_duration,
)
from .sandbox import RunSpec, Sandbox, clean_env, sandbox_from_config

DURATION_TOLERANCE = 0.10   # lệch > 10% so với manifest = dựng hỏng, không phải sai số làm tròn
LUFS_TOLERANCE = 1.5
BLACK_MIN_S = 0.5           # đoạn hình đen liên tục từng này giây trở lên là cảnh hỏng
SILENCE_MIN_S = 1.5         # khoảng lặng dài hơn đệm chuyển cảnh: khán giả tưởng video đứng
# Hình đứng yên hoàn toàn lâu hơn ngưỡng này = "slideshow" (docs/standards.md "không cảnh tĩnh > 8 s"; học từ
# "slideshow-risk check" của OpenMontage, ADR-0016). 0 = tắt. Ken Burns/footage luôn có thay đổi nên không bị bắt.
MAX_STATIC_S = max(0.0, float(os.environ.get("STUDIO_QC_MAX_STATIC_S", "8")))
QC_MAX_OUTPUT = 1_000_000
TIMEOUT_S = max(30, float(os.environ.get("STUDIO_QC_TIMEOUT_S", "300")))
# Đối chiếu lời đọc (ADR-0019): FFmpeg ≥ 8 có filter `whisper` (whisper.cpp). Chỉ bật khi chủ xưởng trỏ
# STUDIO_QC_ASR_MODEL vào file ggml; thiếu model hay ffmpeg cũ thì im lặng — không phải lỗi, không phải warn.
ASR_MODEL_ENV, ASR_LANGUAGE_ENV = "STUDIO_QC_ASR_MODEL", "STUDIO_QC_ASR_LANGUAGE"
ASR_WER_WARN = 0.35          # cảnh có > 35 % từ nghe sai/thiếu: nghi TTS đọc sai, bỏ đoạn hoặc nhạc át tiếng


@dataclass
class QCReport:
    available: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.level == "block" for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "metrics": self.metrics, "findings": [f.model_dump() for f in self.findings]}

    def checklist(self) -> list[str]:
        if not self.available: return ["qc:không đo được (thiếu ffprobe)"]
        m = self.metrics
        head = (f"qc:{m.get('resolution', '?')} {m.get('fps', '?')}fps {m.get('duration_s', '?')}s "
                f"{m.get('lufs', '?')} LUFS audio={'có' if m.get('has_audio') else 'KHÔNG'}")
        if "asr" in m: head += f" asr_wer={m['asr']['wer']}"
        return [head] + [f"qc:{f.level}:{f.location or '-'}:{f.text}" for f in self.findings]


_SANDBOX: Sandbox | None = None


def qc_sandbox() -> Sandbox:
    """Sandbox dùng chung cho mọi lượt đo (dựng một lần: `load_media_config` đọc YAML từ đĩa)."""
    global _SANDBOX
    if _SANDBOX is None:
        _SANDBOX = sandbox_from_config(load_media_config())
    return _SANDBOX


def _run(args: list[str], sandbox: Sandbox | None = None) -> tuple[int, str, str]:
    """QC chỉ ĐO file: `read_only=True` → backend container mount cwd `:ro`, không lượt đo nào ghi đè được bản dựng.

    `max_output` lớn vì stderr của ffmpeg là dữ liệu đo (ebur128, blackdetect) chứ không phải thông báo lỗi —
    cắt 6000 ký tự mặc định là mất chính con số cần đọc."""
    sb = sandbox if sandbox is not None else qc_sandbox()
    try:
        r = sb.run(RunSpec(argv=args, cwd=Path.cwd(), env=clean_env(), timeout=TIMEOUT_S, read_only=True,
                           max_output=QC_MAX_OUTPUT))
        if r.timed_out: return 1, "", f"quá {TIMEOUT_S}s"
        return int(r.exit_code or 0), r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def probe(path: Path) -> dict[str, Any] | None:
    """Thông số kỹ thuật của file theo ffprobe; None khi không có ffprobe hoặc file không đọc được."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not Path(path).is_file(): return None
    code, out, _ = _run([ffprobe, "-v", "error", "-show_entries",
                         "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels:format=duration,size",
                         "-of", "json", str(path)])
    if code != 0 or not out.strip(): return None
    try:
        data = json.loads(out)
        streams = data.get("streams") or []
        v: dict[str, Any] = next((s for s in streams if s.get("codec_type") == "video"), {})
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        fps = 0.0
        if v.get("r_frame_rate"):
            num, den = str(v["r_frame_rate"]).split("/")
            fps = round(float(num) / float(den), 2) if float(den) else 0.0
        fmt = data.get("format") or {}
        result: dict[str, Any] = {"duration_s": round(float(fmt.get("duration") or 0), 2), "bytes": int(fmt.get("size") or 0),
                  "width": int(v.get("width") or 0), "height": int(v.get("height") or 0), "fps": fps,
                  "video_codec": v.get("codec_name"), "has_audio": a is not None,
                  "audio_codec": (a or {}).get("codec_name"), "sample_rate": int((a or {}).get("sample_rate") or 0),
                  "channels": int((a or {}).get("channels") or 0)}
        if any(not math.isfinite(result[k]) or result[k] < 0
               for k in ("duration_s", "bytes", "width", "height", "fps", "sample_rate", "channels")):
            return None
        return result
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def _freeze_spans(log: str, duration_s: float) -> list[float]:
    """Các đoạn hình đứng yên theo log `freezedetect`. Đứng yên tới hết file thì ffmpeg chỉ in `freeze_start` —
    đoạn đó kéo tới `duration_s` (thời lượng ffprobe), không bị bỏ qua."""
    spans: list[float] = []; start: float | None = None
    for key, value in re.findall(r"lavfi\.freezedetect\.freeze_(start|duration):\s*(\d+(?:\.\d+)?)", log):
        if key == "start": start = float(value)
        elif start is not None: spans.append(round(float(value), 2)); start = None
    if start is not None and duration_s > start: spans.append(round(duration_s - start, 2))
    return spans


def measure(path: Path, duration_s: float = 0.0) -> dict[str, Any]:
    """Một lượt ffmpeg đo âm lượng (ebur128), hình đen (blackdetect), hình đứng yên (freezedetect) và khoảng lặng
    (silencedetect). `duration_s` (ffprobe) để khép đoạn đứng yên kéo tới cuối file."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg: return {"scan_complete": False, "scan_error": "thiếu ffmpeg"}
    vf = f"blackdetect=d={BLACK_MIN_S}:pic_th=0.98"
    if MAX_STATIC_S > 0: vf += f",freezedetect=n=-60dB:d={MAX_STATIC_S:g}"
    code, _, err = _run([ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", "-xerror",
                        "-threads", "1", "-filter_threads", "1", "-i", str(path),
                      "-vf", vf,
                      "-af", f"ebur128=peak=true:framelog=verbose,silencedetect=n=-50dB:d={SILENCE_MIN_S}",
                      "-threads", "1", "-f", "null", "-"])
    if code != 0:
        return {"scan_complete": False, "scan_error": f"ffmpeg thất bại (exit {code}): {err[-500:]}"}
    if len(err) >= QC_MAX_OUTPUT:
        return {"scan_complete": False, "scan_error": "log đo bị cắt; chưa xác minh toàn bộ video"}
    out: dict[str, Any] = {}
    lufs = re.findall(r"^\s*I:\s+(-?\d+(?:\.\d+)?) LUFS", err, re.MULTILINE)
    peak = re.findall(r"^\s*Peak:\s+(-?\d+(?:\.\d+)?) dBFS", err, re.MULTILINE)
    if lufs: out["lufs"] = float(lufs[-1])
    if peak: out["true_peak_db"] = float(peak[-1])
    out["scan_complete"] = bool(lufs and peak)
    if not out["scan_complete"]:
        out["scan_error"] = "thiếu tổng kết âm lượng/đỉnh âm thanh"
    out["black_spans"] = [round(float(x), 2) for x in re.findall(r"black_duration:(\d+(?:\.\d+)?)", err)]
    out["silence_spans"] = [round(float(x), 2) for x in re.findall(r"silence_duration:\s*(\d+(?:\.\d+)?)", err)]
    if MAX_STATIC_S > 0: out["freeze_spans"] = _freeze_spans(err, duration_s)
    return out


def qc_video(path: Path, expected_duration_s: float = 0.0, expected_resolution: str = "", expected_fps: int = 0,
             thumbnail: Path | None = None, captions: Path | None = None,
             narration: list[tuple[str, float, float, str]] | None = None) -> QCReport:
    """Đo bản dựng cuối và so với những gì manifest hứa. `block` = thứ không được phép lên nền tảng.
    `narration` = (scene_id, start, end, lời đọc) theo timeline để ASR đối chiếu khi có model whisper."""
    info = probe(Path(path))
    if info is None:
        return QCReport(available=False, metrics={"file": str(path)})
    m: dict[str, Any] = dict(info)
    m["resolution"] = f"{info['width']}x{info['height']}"
    m.update(measure(Path(path), info["duration_s"]))
    f: list[Finding] = []
    if info["width"] <= 0 or info["height"] <= 0 or info["duration_s"] <= 0:
        f.append(Finding(level="block", text="video cuối thiếu hình hoặc thời lượng dương", location="final_video"))
    if not m.get("scan_complete"):
        f.append(Finding(level="block", text=f"QC chưa đo đầy đủ: {m.get('scan_error', 'không có kết quả')}",
                         location="qc_scan"))
    if not info["has_audio"]:
        f.append(Finding(level="block", text="video cuối KHÔNG có luồng âm thanh", location="final_video"))
    if expected_duration_s > 0:
        lech = abs(info["duration_s"] - expected_duration_s) / expected_duration_s
        m["duration_expected_s"] = round(expected_duration_s, 2); m["duration_drift"] = round(lech, 3)
        if lech > DURATION_TOLERANCE:
            f.append(Finding(level="block", text=f"thời lượng {info['duration_s']}s lệch {lech:.0%} so với manifest "
                                                 f"({expected_duration_s:.2f}s)", location="final_video"))
    if expected_resolution and m["resolution"] != expected_resolution.lower():
        f.append(Finding(level="block", text=f"khung {m['resolution']} khác khung yêu cầu {expected_resolution}",
                         location="final_video"))
    if expected_fps and info["fps"] and abs(info["fps"] - expected_fps) > 0.5:
        f.append(Finding(level="warn", text=f"{info['fps']} fps khác {expected_fps} fps đã đặt", location="final_video"))
    if "lufs" in m and abs(m["lufs"] - TARGET_LUFS) > LUFS_TOLERANCE:
        f.append(Finding(level="warn", text=f"âm lượng {m['lufs']} LUFS lệch chuẩn {TARGET_LUFS} LUFS "
                                            f"(nền tảng sẽ tự chỉnh, nghe to/nhỏ bất thường)", location="audio"))
    if m.get("true_peak_db", -99) > TRUE_PEAK_DB + 0.5:
        f.append(Finding(level="warn", text=f"đỉnh {m['true_peak_db']} dBFS vượt {TRUE_PEAK_DB} dBTP (dễ méo tiếng)",
                         location="audio"))
    if m.get("black_spans"):
        f.append(Finding(level="warn", text=f"{len(m['black_spans'])} đoạn hình đen ≥ {BLACK_MIN_S}s "
                                            f"(dài nhất {max(m['black_spans'])}s)", location="final_video"))
    if m.get("freeze_spans"):
        f.append(Finding(level="warn", text=f"{len(m['freeze_spans'])} đoạn hình đứng yên ≥ {MAX_STATIC_S:g}s "
                                            f"(dài nhất {max(m['freeze_spans'])}s): nguy cơ slideshow, thêm chuyển "
                                            f"động/footage hoặc chia cảnh", location="motion"))
    if m.get("silence_spans"):
        f.append(Finding(level="warn", text=f"{len(m['silence_spans'])} khoảng lặng ≥ {SILENCE_MIN_S}s "
                                            f"(dài nhất {max(m['silence_spans'])}s)", location="audio"))
    if thumbnail is not None:
        f += _qc_thumbnail(Path(thumbnail), m)
    m["captions"] = str(captions) if captions and Path(captions).is_file() else None
    if captions is None or not Path(captions).is_file():
        f.append(Finding(level="warn", text="không có phụ đề đi kèm", location="captions"))
    if narration:
        f += _qc_narration(Path(path), narration, m)
    return QCReport(available=True, metrics=m, findings=f)


# ---------- ASR đối chiếu lời đọc ----------

def asr_model() -> Path | None:
    """File model whisper (ggml) khi QC lời đọc được bật và ffmpeg trên máy có filter `whisper`; None = bỏ qua."""
    model, ffmpeg = os.environ.get(ASR_MODEL_ENV, ""), shutil.which("ffmpeg")
    if not model or not ffmpeg or not Path(model).is_file(): return None
    code, out, _ = _run([ffmpeg, "-hide_banner", "-filters"])
    return Path(model) if code == 0 and re.search(r"^\s*\S*\s+whisper\s", out, re.MULTILINE) else None


def _filter_escape(value: str) -> str:
    return re.sub(r"([\\:,;'\[\]])", r"\\\1", value)


def transcribe(path: Path, model: Path, language: str = "auto") -> list[tuple[float, float, str]] | None:
    """Chạy whisper.cpp qua ffmpeg, SRT ra stdout (`destination=pipe:1`), không ghi file. None khi ffmpeg lỗi."""
    ffmpeg = shutil.which("ffmpeg")
    spec = (f"whisper=model={_filter_escape(str(model))}:language={_filter_escape(language)}:queue=10"
            f":format=srt:destination=pipe\\:1")
    code, out, _ = _run([str(ffmpeg), "-hide_banner", "-nostats", "-loglevel", "error", "-threads", "1",
                         "-i", str(path), "-vn", "-af", spec, "-f", "null", "-"])
    return parse_srt(out) if code == 0 else None


_SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    """Các đoạn (start, end, lời) của một SRT; bỏ khối thiếu mốc giờ hoặc không có chữ."""
    out: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        found = next(((i, mt) for i, mt in ((i, _SRT_TIME.match(line)) for i, line in enumerate(lines[:2])) if mt), None)
        if found is None: continue
        times, g = found[0], [int(x) for x in found[1].groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        words = " ".join(lines[times + 1:])
        if words: out.append((start, end, words))
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def wer(expected: str, heard: str) -> float:
    """Tỷ lệ lỗi từ (Levenshtein trên từ, bỏ dấu câu và hoa/thường), kẹp 0..1."""
    a, b = _words(expected), _words(heard)
    if not a: return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return round(min(1.0, prev[-1] / len(a)), 3)


def narration_wer(heard: list[tuple[float, float, str]],
                  narration: list[tuple[str, float, float, str]]) -> dict[str, float]:
    """WER từng cảnh: đoạn ASR thuộc cảnh chứa điểm giữa của nó. Cảnh không có lời thì không chấm."""
    out: dict[str, float] = {}
    for sid, start, end, text in narration:
        if not text.strip(): continue
        spoken = " ".join(t for s, e, t in heard if start <= (s + e) / 2 < end)
        out[sid] = wer(text, spoken)
    return out


def _qc_narration(path: Path, narration: list[tuple[str, float, float, str]], m: dict[str, Any]) -> list[Finding]:
    model = asr_model()
    if model is None: return []
    language = os.environ.get(ASR_LANGUAGE_ENV, "auto")
    heard = transcribe(path, model, language)
    if heard is None:
        return [Finding(level="warn", text="ASR (ffmpeg whisper) thất bại; chưa đối chiếu lời đọc", location="narration")]
    scenes = narration_wer(heard, narration)
    total = round(sum(scenes.values()) / len(scenes), 3) if scenes else 0.0
    m["asr"] = {"model": model.name, "language": language, "wer": total, "segments": len(heard), "scenes": scenes}
    return [Finding(level="warn", text=f"cảnh {sid}: ASR nghe khớp {1 - w:.0%} lời đọc (nghi TTS đọc sai, bỏ đoạn "
                                       f"hoặc nhạc át tiếng; ngưỡng {1 - ASR_WER_WARN:.0%})", location="narration")
            for sid, w in scenes.items() if w > ASR_WER_WARN]


def _qc_thumbnail(path: Path, m: dict[str, Any]) -> list[Finding]:
    info = probe(path)
    if info is None:
        return [Finding(level="warn", text=f"không đọc được thumbnail {path.name}", location="thumbnail")]
    size = f"{info['width']}x{info['height']}"
    m["thumbnail"] = {"resolution": size, "bytes": info["bytes"]}
    f: list[Finding] = []
    if info["bytes"] > PLATFORM_THUMBNAIL_MAX_BYTES:
        f.append(Finding(level="block", text=f"thumbnail {info['bytes'] / 1e6:.1f} MB vượt giới hạn nền tảng "
                                             f"{PLATFORM_THUMBNAIL_MAX_BYTES // 1_000_000} MB", location="thumbnail"))
    elif info["bytes"] > THUMBNAIL_MAX_BYTES:
        f.append(Finding(level="warn", text=f"thumbnail {info['bytes'] / 1e6:.1f} MB vượt mục tiêu 2 MB của renderer "
                                            f"(YouTube nhận ≤ {PLATFORM_THUMBNAIL_MAX_BYTES // 1_000_000} MB; file nặng tải chậm)",
                         location="thumbnail"))
    if size != THUMBNAIL_SIZE:
        f.append(Finding(level="warn", text=f"thumbnail {size} khác chuẩn {THUMBNAIL_SIZE}", location="thumbnail"))
    return f


# ---------- QC từng cảnh: dữ liệu cho editor, người duy nhất sửa được cảnh ----------

IMAGE_DARK, IMAGE_BRIGHT, IMAGE_FLAT = 40.0, 215.0, 30.0   # thang Y 0–255 của ffmpeg signalstats
SILENCE_RATIO = 0.9                                        # giọng đọc gần như im lặng = TTS hỏng, không phải cảnh lặng
SHORT_READ_RATIO = 0.4                                     # đọc ngắn hơn 40% so với số từ = TTS nuốt mất phần cuối


def measure_image(path: Path, start_s: float = 0) -> dict[str, float]:
    """Độ sáng và tương phản của một ảnh (`signalstats` in mọi khoá metadata ra stderr)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not Path(path).is_file(): return {}
    _, _, err = _run([ffmpeg, "-hide_banner", "-nostats", "-ss",str(start_s), "-i", str(path), "-vf", "signalstats,metadata=print",
                      "-frames:v", "1", "-f", "null", "-"])
    out: dict[str, float] = {}
    for key in ("YAVG", "YLOW", "YHIGH"):
        m = re.search(rf"lavfi\.signalstats\.{key}=(-?\d+(?:\.\d+)?)", err)
        if m: out[key.lower()] = round(float(m.group(1)), 1)
    if "yhigh" in out and "ylow" in out: out["contrast"] = round(out["yhigh"] - out["ylow"], 1)
    return out


def measure_silence(path: Path, start_s: float = 0, duration_s: float | None = None) -> float:
    """Tổng số giây im lặng trong một file audio."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not Path(path).is_file(): return 0.0
    span=['-t',str(duration_s)] if duration_s is not None else []
    _, _, err = _run([ffmpeg, "-hide_banner", "-nostats", '-ss',str(start_s), "-i", str(path), *span,"-af", "silencedetect=n=-50dB:d=0.3",
                      "-f", "null", "-"])
    return round(sum(float(x) for x in re.findall(r"silence_duration:\s*(\d+(?:\.\d+)?)", err)), 2)


def qc_scene(image: Path | None, audio: Path | None, narration: str = "", declared_s: float = 0.0,
             clip_start_s: float | None = None) -> dict[str, Any]:
    """Đo một cảnh. Editor được yêu cầu bắt "ảnh tối", "narration vấp" nhưng chỉ nhận JSON đường dẫn — đây là chỗ
    biến những thứ đó thành số để nó quyết định trên dữ liệu chứ không phải phỏng đoán."""
    m: dict[str, Any] = {}; f: list[Finding] = []
    if image is None or not Path(image).is_file():
        f.append(Finding(level="block", text="thiếu ảnh cảnh", location="scene_image"))
    else:
        m["image"] = measure_image(Path(image),clip_start_s+declared_s/2) if clip_start_s is not None else measure_image(Path(image))
        yavg = m["image"].get("yavg")
        if yavg is not None:
            if yavg < IMAGE_DARK: f.append(Finding(level="warn", text=f"ảnh gần như đen (độ sáng {yavg}/255): sinh lại với prompt sáng hơn", location="scene_image"))
            elif yavg > IMAGE_BRIGHT: f.append(Finding(level="warn", text=f"ảnh gần như trắng (độ sáng {yavg}/255)", location="scene_image"))
        if m["image"].get("contrast", 999) < IMAGE_FLAT:
            f.append(Finding(level="warn", text=f"ảnh gần như một màu (tương phản {m['image'].get('contrast')}): không thấy chủ thể", location="scene_image"))
    if audio is None or not Path(audio).is_file():
        f.append(Finding(level="block", text="thiếu giọng đọc", location="scene_audio"))
    else:
        dur = probe_duration(Path(audio)) or 0.0
        silence = measure_silence(Path(audio))
        if clip_start_s is not None:
            dur=min(declared_s,max(0,dur-clip_start_s))
            silence=measure_silence(Path(audio),clip_start_s,declared_s)
            m['clip_start_s']=clip_start_s
        m["audio"] = {"duration_s": dur, "silence_s": silence}
        if dur <= 0:
            f.append(Finding(level="block", text="file giọng đọc rỗng", location="scene_audio"))
        else:
            if silence / dur > SILENCE_RATIO:
                f.append(Finding(level="block", text=f"giọng đọc gần như im lặng ({silence}s/{dur}s): TTS hỏng, sinh lại audio", location="scene_audio"))
            words = len(narration.split())
            if words and dur < (words / WORDS_PER_SECOND) * SHORT_READ_RATIO:
                f.append(Finding(level="warn", text=f"chỉ đọc {dur}s cho {words} từ: nhiều khả năng TTS nuốt phần cuối", location="scene_audio"))
            if declared_s > 0 and abs(dur - declared_s) / declared_s > 0.2:
                f.append(Finding(level="warn", text=f"thời lượng thật {dur}s lệch manifest ({declared_s}s)", location="scene_audio"))
    return {"metrics": m, "findings": [x.model_dump() for x in f]}


def qc_scenes(manifest: Any) -> dict[str, dict[str, Any]]:
    """Đo mọi cảnh của một manifest. Rỗng khi máy không có ffmpeg — editor vẫn chạy, chỉ là không có số."""
    if not shutil.which("ffmpeg"): return {}
    out: dict[str, dict[str, Any]] = {}
    for s in manifest.scenes:
        img = s.asset_refs.get("scene_video") or s.asset_refs.get("scene_image"); aud = s.asset_refs.get("scene_audio")
        out[s.scene_id] = qc_scene(Path(img) if img else None, Path(aud) if aud else None, s.narration, s.duration_s,s.clip_start_s)
    return out
