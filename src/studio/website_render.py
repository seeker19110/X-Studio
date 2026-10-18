"""Native screenshot cards, real speech timing, captions and verified atomic MP4 exports."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .render_cache import digest
from .speech import caption_chunks as caption_chunks
from .speech import edge_audio, timed_captions, validate_words
from .website import WebsitePlan, WebsiteRequest, save_json


def clock(seconds: float, separator: str = ',') -> str:
    ms = round(seconds * 1000); hours, ms = divmod(ms, 3600000); minutes, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f'{hours:02}:{minutes:02}:{sec:02}{separator}{ms:03}'




class NarrationFitError(ValueError):
    pass


def fit_slots(durations: list[float], target: int) -> tuple[list[float], float]:
    if not durations or any(not math.isfinite(d) or d <= 0 for d in durations):
        raise ValueError('Thời lượng giọng đọc không hợp lệ')
    # Reserve a short visual lead-in and a spoken-sentence tail. Never truncate speech to fit a target.
    overhead = len(durations) * .75
    pace = max(1., sum(durations) / (target - overhead)) if target > overhead else 2.
    if pace > 1.25: raise NarrationFitError(f'Lời đọc {sum(durations):.1f} giây; cần tối đa {target - overhead:.1f} giây. Giảm từ khoảng {round(sum(durations)/3.2)} xuống {round((target-overhead)*3.1)} từ, vẫn giữ đủ ý; không cắt lời đọc.')
    if sum(durations) / target < .55: raise NarrationFitError('Lời đọc quá ngắn; bổ sung thông tin từ bằng chứng để đạt khoảng ' + str(round(target * 3.1)) + ' từ')
    slots = [d / pace + .75 for d in durations]
    padding = (target - sum(slots)) / len(slots)
    slots = [s + padding for s in slots]
    if max(slots) > 20.01: raise NarrationFitError('Một cảnh vượt 20 giây; chia lại nội dung thành các cảnh ngắn hơn')
    # Frame-exact boundaries; final residual belongs to the last scene.
    slots = [round(s * 30) / 30 for s in slots]
    slots[-1] += target - sum(slots)
    return slots, pace


def run(argv: list[str], cwd: Path | None = None, timeout: int = 900) -> str:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode: raise ValueError('Công cụ media thất bại: ' + result.stderr[-600:])
    return result.stdout


def probe(path: Path) -> dict:
    binary = os.environ.get('STUDIO_WEBSITE_FFPROBE', 'ffprobe')
    return json.loads(run([binary, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))




async def voice_audio(text: str, voice: str, path: Path) -> list[dict]:
    return await edge_audio(text, voice, path)


def speech(text: str, voice: str, source: Path, accounting: Any = None, video_id: str = '') -> tuple[Path, float, list[dict]]:
    key = hashlib.sha256((voice + '\n' + text).encode()).hexdigest()[:24]
    audio = source / (key + '.mp3'); timing = source / (key + '.json')
    cached = None
    try:
        data = json.loads(timing.read_text(encoding='utf-8'))
        if (data['sha256'] == digest(audio) and audio.stat().st_size > 0
                and math.isfinite(float(data['duration'])) and float(data['duration']) > 0 and data['words']):
            data['words'] = validate_words(data['words'], float(data['duration']))
            cached = data
    except (OSError, ValueError, KeyError, TypeError): pass
    if cached is None:
        stage = audio.with_suffix('.part.mp3')
        try:
            for attempt in range(3):
                try:
                    def produce(): return asyncio.run(voice_audio(text, voice, stage))
                    words = accounting.generate(video_id,'tts',len(text)/1000,produce) if accounting is not None else produce()
                    break
                except Exception:
                    if attempt == 2: raise
                    time.sleep(2 ** attempt)
            duration = float(probe(stage)['format']['duration'])
            if not math.isfinite(duration) or duration <= 0: raise ValueError('Thời lượng giọng đọc không hợp lệ')
            words = validate_words(words, duration)
            checksum = digest(stage)
            stage.replace(audio); save_json(timing, {'duration': duration, 'words': words, 'sha256':checksum})
        finally: stage.unlink(missing_ok=True)
    data = json.loads(timing.read_text(encoding='utf-8'))
    return audio, float(data['duration']), data['words']




def subtitles(path: Path, cues: list[dict]) -> None:
    path.write_text('\n\n'.join(f"{i + 1}\n{clock(c['start'])} --> {clock(c['end'])}\n{c['text']}"
                              for i, c in enumerate(cues)) + '\n', encoding='utf-8')


def ass_subtitles(path: Path, cues: list[dict], width: int, height: int) -> None:
    header = (f'[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n'
              '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
              'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, '
              'Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
              f'Style: Default,Segoe UI,{round(height * .032)},&H00FFFFFF,&H00FFFFFF,&H00101B25,&H00101B25,'
              f'0,0,0,0,100,100,0,0,3,{max(2, height // 360)},0,2,{width // 10},{width // 10},{height // 20},1\n'
              '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    lines = []
    for cue in cues:
        text = cue['text'].replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\n', r'\N')
        # ASS uses centiseconds and accepts h:mm:ss.cc.
        start = clock(cue['start'], '.')[:-1]; end = clock(cue['end'], '.')[:-1]
        lines.append(f'Dialogue: 0,{start},{end},Default,,0,0,0,,{text}')
    path.write_text(header + '\n'.join(lines) + '\n', encoding='utf-8')


def font_file() -> str:
    explicit = os.environ.get('STUDIO_WEBSITE_FONT')
    candidates = [explicit] if explicit else ['C:/Windows/Fonts/segoeui.ttf', '/mnt/c/Windows/Fonts/segoeui.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    font = next((p for p in candidates if p and Path(p).is_file()), None)
    if not font: raise ValueError('Cần font Unicode; đặt STUDIO_WEBSITE_FONT tới file .ttf')
    return font


def card(plan: WebsitePlan, scene: Any, screenshot: Path, out: Path, quality: str, index: int, url: str) -> None:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    scale = 2 if quality == '4k' else 1; W, H = 1920 * scale, 1080 * scale
    image = Image.new('RGB', (W, H), '#091c2e'); draw = ImageDraw.Draw(image); font = font_file()
    def rect(box, fill, radius=0):
        draw.rounded_rectangle(tuple(int(v * scale) for v in box), radius=radius * scale, fill=fill)
    def text(value, xy, size, color='#eaf5ff', width=500, max_lines=3):
        selected: list[str] = []
        for font_size in range(size, 15, -1):
            f = ImageFont.truetype(font, font_size * scale); selected = []; line = ''
            for word in value.split():
                candidate = (line + ' ' + word).strip()
                if draw.textlength(candidate, font=f) > width * scale and line:
                    selected.append(line); line = word
                else: line = candidate
            selected.append(line)
            if len(selected) <= max_lines and all(draw.textlength(s, font=f) <= width * scale for s in selected): break
        for n, line in enumerate(selected[:max_lines]):
            draw.text((xy[0] * scale, (xy[1] + n * font_size * 1.35) * scale), line, font=f, fill=color)
    rect((0, 0, 1920, 8), '#37ddbd')
    text('WEBSITE → VIDEO  /  X-STUDIO', (60, 40), 22, '#70e5cd', width=1800, max_lines=1)
    text(plan.title, (60, 92), 44, width=1800, max_lines=2)
    text(f'{index + 1:02} / {len(plan.scenes):02}', (60, 248), 34, '#70e5cd', max_lines=1)
    text(scene.title, (60, 315), 38, width=300, max_lines=4)
    rect((60, 585, 366, 830), '#153449', 18)
    text('BƯỚC TIẾP THEO', (82, 613), 18, '#70e5cd', width=260, max_lines=1)
    text(scene.instruction, (82, 662), 28, width=260, max_lines=5)
    rect((410, 248, 1860, 878), '#f0f5f7', 16)
    rect((410, 248, 1860, 292), '#d6e3ea', 16)
    text(url.replace('https://', '').replace('http://', '')[:110], (440, 258), 17, '#254357', width=1360, max_lines=1)
    with Image.open(screenshot) as source:
        screen = ImageOps.contain(source.convert('RGB'), (1430 * scale, 570 * scale), Image.Resampling.LANCZOS)
        image.paste(screen, ((1135 * scale - screen.width // 2), (584 * scale - screen.height // 2)))
    text('Giao diện thật · Nội dung công khai · Làm theo nhịp của bạn', (410, 896), 19, '#91adc3', width=1450, max_lines=1)
    image.save(out)


def verify_video(path: Path, request: WebsiteRequest, ffmpeg: str) -> dict:
    info = probe(path); video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
    expected = (3840, 2160) if request.quality == '4k' else (1920, 1080)
    duration = float(info['format']['duration'])
    if ((video['width'], video['height']) != expected or video['codec_name'] != 'h264' or video['pix_fmt'] != 'yuv420p'
            or video['avg_frame_rate'] != '30/1' or abs(duration - request.duration_s) > .08
            or audio['codec_name'] != 'aac' or audio['sample_rate'] != '48000' or audio['channels'] != 2):
        raise ValueError('Video không đạt kiểm tra kích thước, codec hoặc thời lượng')
    run([ffmpeg, '-v', 'error', '-threads', '2', '-i', str(path), '-f', 'null', '-'])
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024): digest.update(block)
    return {'passed': True, 'duration_s': duration, 'width': video['width'], 'height': video['height'],
            'fps': 30, 'audio': 'AAC stereo 48kHz', 'sha256': digest.hexdigest(), 'bytes': path.stat().st_size}


def render_video(request: WebsiteRequest, plan: WebsitePlan, pages: list[dict], root: Path, kind: str,
                 progress: Any, checkpoint: Any, accounting: Any = None, video_id: str = '') -> dict[str, str]:
    ffmpeg = os.environ.get('STUDIO_WEBSITE_FFMPEG', 'ffmpeg')
    if not shutil.which(ffmpeg): raise ValueError('Cần FFmpeg và ffprobe trên PATH để xuất video')
    source = root / 'source'; cache = source / kind; cache.mkdir(exist_ok=True)
    progress('voicing', .05, 'Tạo giọng đọc tiếng Việt và mốc phụ đề')
    voices = []
    for i, scene in enumerate(plan.scenes):
        checkpoint()
        voices.append(speech(scene.narration,request.voice,source,accounting,video_id) if accounting is not None else speech(scene.narration, request.voice, source))
        progress('voicing', .05 + .20 * (i + 1) / len(plan.scenes), f'Giọng đọc cảnh {i + 1}/{len(plan.scenes)}')
    slots, pace = fit_slots([v[1] for v in voices], request.duration_s)
    by_id = {p['id']: p for p in pages}; cues = []; offset = 0.; segments = []; timeline = []
    width = 3840 if request.quality == '4k' else 1920; height = width * 9 // 16
    for i, (scene, voice, duration) in enumerate(zip(plan.scenes, voices, slots, strict=True)):
        checkpoint(); page = by_id[scene.page_id]
        png = cache / f'card-{i}.png'; segment = cache / f'segment-{i}.mp4'
        signature = hashlib.sha256(json.dumps(['website-card-v1', plan.title, i, len(plan.scenes), page['url'],
                        scene.model_dump(), request.voice, request.quality, duration, pace,
                        hashlib.sha256((source / page['image']).read_bytes()).hexdigest()], sort_keys=True).encode()).hexdigest()
        marker = cache / f'segment-{i}.json'
        valid = False
        try:
            metadata = json.loads(marker.read_text(encoding='utf-8'))
            valid = (metadata['signature'] == signature and metadata['sha256'] == digest(segment)
                     and metadata['bytes'] == segment.stat().st_size and segment.stat().st_size > 0)
        except (OSError, ValueError, TypeError, KeyError): pass
        if not valid:
            card(plan, scene, source / page['image'], png, request.quality, i, page['url'])
            partial = cache / f'segment-{i}.part.mp4'
            run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-filter_threads', '2', '-threads', '2',
                 '-loop', '1', '-framerate', '30', '-i', str(png), '-threads', '2', '-i', str(voice[0]),
                 '-vf', f'fade=t=in:st=0:d=0.3,fade=t=out:st={duration - .25}:d=0.25,format=yuv420p',
                 '-af', f'atempo={pace},adelay=400:all=1,apad', '-t', f'{duration:.6f}',
                 '-c:v', 'libx264', '-preset', 'medium', '-crf', '17', '-threads', '2', '-c:a', 'aac',
                 '-b:a', '256k', '-ar', '48000', '-ac', '2', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                 '-colorspace', 'bt709', str(partial)])
            partial.replace(segment)
            save_json(marker, {'signature':signature, 'sha256':digest(segment), 'bytes':segment.stat().st_size})
        segments.append(segment)
        cues.extend(timed_captions(voice[2], offset + .4, pace))
        timeline.append({'start': offset, 'end': offset + duration, **scene.model_dump()}); offset += duration
        progress('rendering', .25 + .5 * (i + 1) / len(plan.scenes), f'Dựng cảnh {i + 1}/{len(plan.scenes)}')
    for cue in cues: cue['end'] = min(cue['end'], request.duration_s - .05)
    srt = cache / 'subtitles.srt'; subtitles(srt, cues)
    ass_subtitles(cache / 'captions.ass', cues, width, height)
    concat = cache / 'concat.txt'
    # These generated basenames avoid FFmpeg quoting of arbitrary paths on Windows.
    concat.write_text('\n'.join(f"file '{p.name}'" for p in segments), encoding='utf-8')
    run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', 'concat.txt',
         '-c', 'copy', 'combined.mp4'], cwd=cache)
    checkpoint(); progress('rendering', .8, 'Ghép phụ đề và cân bằng âm lượng')
    stage = root / (kind + '.part.mp4')
    # Filter path is relative to cwd: drive letters/quotes in a user destination never enter a filter graph.
    run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-filter_threads', '2', '-threads', '2',
         '-i', 'combined.mp4', '-vf', 'ass=captions.ass', '-af', 'loudnorm=I=-16:TP=-1.5:LRA=7', '-t', str(request.duration_s),
         '-c:v', 'libx264', '-preset', 'medium', '-crf', '17', '-threads', '2', '-pix_fmt', 'yuv420p', '-r', '30',
         '-c:a', 'aac', '-b:a', '320k', '-ar', '48000', '-ac', '2', '-movflags', '+faststart',
         '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', str(stage)], cwd=cache)
    checkpoint(); progress('verifying', .95, 'Kiểm tra thời lượng và giải mã toàn bộ video')
    report = verify_video(stage, request, ffmpeg)
    report.update(voice=request.voice, speech_pace=pace, caption_cues=len(cues), source_pages=len(pages),
                  requested_duration_s=request.duration_s)
    stage.replace(root / (kind + '.mp4'))
    shutil.copyfile(srt, root / (kind + '.srt'))
    run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-threads', '2', '-ss', '1', '-i', str(root / (kind + '.mp4')),
         '-frames:v', '1', '-threads', '2', str(root / (kind + '.jpg'))])
    save_json(root / (kind + '-report.json'), report)
    save_json(root / (kind + '-script.json'), {'title': plan.title, 'scenes': timeline, 'pages': pages, 'captions': cues})
    return {kind: kind + '.mp4', kind + '-subtitles': kind + '.srt', kind + '-poster': kind + '.jpg',
            kind + '-report': kind + '-report.json', kind + '-script': kind + '-script.json'}
