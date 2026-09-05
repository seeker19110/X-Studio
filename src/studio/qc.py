"""QC bằng CODE trên file thật (không phải trên manifest).

Ba reviewer của khối chất lượng đọc JSON: họ không mở được file, không nghe được tiếng. Nên "hook ≤ 5 giây",
"thời lượng đúng", "không cảnh trống" trước đây không ai kiểm được trước gate publish. Module này đo file bằng
ffprobe/ffmpeg và trả về những con số đó, để quality-reviewer đọc trong `package` và người duyệt thấy trong checklist.

Không có ffprobe trên máy (chế độ `fake`, CI tối giản) thì báo cáo nói thẳng là không đo được, chứ không đoán bừa.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import Finding
from .media import TARGET_LUFS, THUMBNAIL_MAX_BYTES, THUMBNAIL_SIZE, TRUE_PEAK_DB, WORDS_PER_SECOND, probe_duration

DURATION_TOLERANCE = 0.10   # lệch > 10% so với manifest = dựng hỏng, không phải sai số làm tròn
LUFS_TOLERANCE = 1.5
BLACK_MIN_S = 0.5           # đoạn hình đen liên tục từng này giây trở lên là cảnh hỏng
SILENCE_MIN_S = 1.5         # khoảng lặng dài hơn đệm chuyển cảnh: khán giả tưởng video đứng
TIMEOUT_S = 300


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
        return [head] + [f"qc:{f.level}:{f.location or '-'}:{f.text}" for f in self.findings]


def _run(args: list[str]) -> tuple[int, str, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
        return r.returncode, r.stdout, r.stderr
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
    except json.JSONDecodeError:
        return None
    streams = data.get("streams") or []
    v: dict[str, Any] = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = 0.0
    if v.get("r_frame_rate") and "/" in str(v["r_frame_rate"]):
        num, den = str(v["r_frame_rate"]).split("/")
        fps = round(float(num) / float(den), 2) if float(den) else 0.0
    fmt = data.get("format") or {}
    return {"duration_s": round(float(fmt.get("duration") or 0), 2), "bytes": int(fmt.get("size") or 0),
            "width": int(v.get("width") or 0), "height": int(v.get("height") or 0), "fps": fps,
            "video_codec": v.get("codec_name"), "has_audio": a is not None,
            "audio_codec": (a or {}).get("codec_name"), "sample_rate": int((a or {}).get("sample_rate") or 0),
            "channels": int((a or {}).get("channels") or 0)}


def measure(path: Path) -> dict[str, Any]:
    """Một lượt ffmpeg đo âm lượng (ebur128), hình đen (blackdetect) và khoảng lặng (silencedetect)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg: return {}
    _, _, err = _run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
                      "-vf", f"blackdetect=d={BLACK_MIN_S}:pic_th=0.98",
                      "-af", f"ebur128=peak=true,silencedetect=n=-50dB:d={SILENCE_MIN_S}", "-f", "null", "-"])
    out: dict[str, Any] = {}
    lufs = re.findall(r"^\s*I:\s+(-?\d+(?:\.\d+)?) LUFS", err, re.MULTILINE)
    peak = re.findall(r"^\s*Peak:\s+(-?\d+(?:\.\d+)?) dBFS", err, re.MULTILINE)
    if lufs: out["lufs"] = float(lufs[-1])
    if peak: out["true_peak_db"] = float(peak[-1])
    out["black_spans"] = [round(float(x), 2) for x in re.findall(r"black_duration:(\d+(?:\.\d+)?)", err)]
    out["silence_spans"] = [round(float(x), 2) for x in re.findall(r"silence_duration:\s*(\d+(?:\.\d+)?)", err)]
    return out


def qc_video(path: Path, expected_duration_s: float = 0.0, expected_resolution: str = "", expected_fps: int = 0,
             thumbnail: Path | None = None, captions: Path | None = None) -> QCReport:
    """Đo bản dựng cuối và so với những gì manifest hứa. `block` = thứ không được phép lên nền tảng."""
    info = probe(Path(path))
    if info is None:
        return QCReport(available=False, metrics={"file": str(path)})
    m: dict[str, Any] = dict(info)
    m["resolution"] = f"{info['width']}x{info['height']}"
    m.update(measure(Path(path)))
    f: list[Finding] = []
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
    if m.get("silence_spans"):
        f.append(Finding(level="warn", text=f"{len(m['silence_spans'])} khoảng lặng ≥ {SILENCE_MIN_S}s "
                                            f"(dài nhất {max(m['silence_spans'])}s)", location="audio"))
    if thumbnail is not None:
        f += _qc_thumbnail(Path(thumbnail), m)
    m["captions"] = str(captions) if captions and Path(captions).is_file() else None
    if captions is None or not Path(captions).is_file():
        f.append(Finding(level="warn", text="không có phụ đề đi kèm", location="captions"))
    return QCReport(available=True, metrics=m, findings=f)


def _qc_thumbnail(path: Path, m: dict[str, Any]) -> list[Finding]:
    info = probe(path)
    if info is None:
        return [Finding(level="warn", text=f"không đọc được thumbnail {path.name}", location="thumbnail")]
    size = f"{info['width']}x{info['height']}"
    m["thumbnail"] = {"resolution": size, "bytes": info["bytes"]}
    f: list[Finding] = []
    if info["bytes"] > THUMBNAIL_MAX_BYTES:
        f.append(Finding(level="block", text=f"thumbnail {info['bytes'] / 1e6:.1f} MB vượt giới hạn nền tảng 2 MB",
                         location="thumbnail"))
    if size != THUMBNAIL_SIZE:
        f.append(Finding(level="warn", text=f"thumbnail {size} khác chuẩn {THUMBNAIL_SIZE}", location="thumbnail"))
    return f


# ---------- QC từng cảnh: dữ liệu cho editor, người duy nhất sửa được cảnh ----------

IMAGE_DARK, IMAGE_BRIGHT, IMAGE_FLAT = 40.0, 215.0, 30.0   # thang Y 0–255 của ffmpeg signalstats
SILENCE_RATIO = 0.9                                        # giọng đọc gần như im lặng = TTS hỏng, không phải cảnh lặng
SHORT_READ_RATIO = 0.4                                     # đọc ngắn hơn 40% so với số từ = TTS nuốt mất phần cuối


def measure_image(path: Path) -> dict[str, float]:
    """Độ sáng và tương phản của một ảnh (`signalstats` in mọi khoá metadata ra stderr)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not Path(path).is_file(): return {}
    _, _, err = _run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-vf", "signalstats,metadata=print",
                      "-frames:v", "1", "-f", "null", "-"])
    out: dict[str, float] = {}
    for key in ("YAVG", "YLOW", "YHIGH"):
        m = re.search(rf"lavfi\.signalstats\.{key}=(-?\d+(?:\.\d+)?)", err)
        if m: out[key.lower()] = round(float(m.group(1)), 1)
    if "yhigh" in out and "ylow" in out: out["contrast"] = round(out["yhigh"] - out["ylow"], 1)
    return out


def measure_silence(path: Path) -> float:
    """Tổng số giây im lặng trong một file audio."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not Path(path).is_file(): return 0.0
    _, _, err = _run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "silencedetect=n=-50dB:d=0.3",
                      "-f", "null", "-"])
    return round(sum(float(x) for x in re.findall(r"silence_duration:\s*(\d+(?:\.\d+)?)", err)), 2)


def qc_scene(image: Path | None, audio: Path | None, narration: str = "", declared_s: float = 0.0) -> dict[str, Any]:
    """Đo một cảnh. Editor được yêu cầu bắt "ảnh tối", "narration vấp" nhưng chỉ nhận JSON đường dẫn — đây là chỗ
    biến những thứ đó thành số để nó quyết định trên dữ liệu chứ không phải phỏng đoán."""
    m: dict[str, Any] = {}; f: list[Finding] = []
    if image is None or not Path(image).is_file():
        f.append(Finding(level="block", text="thiếu ảnh cảnh", location="scene_image"))
    else:
        m["image"] = measure_image(Path(image))
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
        img = s.asset_refs.get("scene_image"); aud = s.asset_refs.get("scene_audio")
        out[s.scene_id] = qc_scene(Path(img) if img else None, Path(aud) if aud else None, s.narration, s.duration_s)
    return out
