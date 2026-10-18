"""Local, read-only readiness checks. Synthetic smoke is explicitly requested and never calls providers."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .llm import LLMError, load_config
from .media import IMAGE_PROVIDERS, TTS_PROVIDERS, MediaConfig, find_font, load_media_config


def _run(argv: list[str], timeout: int = 10, cwd: Path | None = None) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=timeout, cwd=cwd)
    if result.returncode: raise OSError('Tool failed')
    return result.stdout + '\n' + result.stderr


def _chromium() -> bool:
    configured = os.environ.get('STUDIO_WEBSITE_CHROMIUM')
    if configured: return bool(shutil.which(configured))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime: return Path(runtime.chromium.executable_path).is_file()


def check_environment(cfg: MediaConfig, *, flow: str = 'render', demo: bool = False,
                      output: Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    def add(id: str, okay: bool, success: str, failure: str, *, warning: bool = False) -> None:
        checks.append({'id': id, 'status': 'ok' if okay else 'warn' if warning else 'error',
                       'message': success if okay else failure})
    add('python', sys.version_info >= (3, 11), 'Python 3.11+ có sẵn', 'Cần Python 3.11 trở lên')
    directory = (output or cfg.output_dir).resolve()
    while not directory.exists(): directory = directory.parent
    add('output', directory.is_dir() and os.access(directory, os.W_OK | os.X_OK),
        'Thư mục đích có quyền ghi', 'Chọn thư mục đích có quyền ghi và truy cập')
    try:
        add('disk', shutil.disk_usage(directory).free >= int(cfg.render.get('min_free_disk_bytes', 256 * 1024 * 1024)),
            'Dung lượng trống đạt ngưỡng cấu hình', 'Giải phóng dung lượng hoặc chọn ổ đĩa khác')
    except OSError: add('disk', False, '', 'Không đọc được dung lượng ổ đĩa')
    if not demo:
        binary = str(os.environ.get('STUDIO_WEBSITE_FFMPEG', 'ffmpeg') if flow == 'website' else cfg.video.get('binary', 'ffmpeg'))
        probe = os.environ.get('STUDIO_WEBSITE_FFPROBE', 'ffprobe') if flow == 'website' else 'ffprobe'
        found = shutil.which(binary)
        add('ffmpeg', bool(found), 'FFmpeg có sẵn', 'Cài FFmpeg và đặt executable trên PATH hoặc cấu hình đường dẫn')
        add('ffprobe', bool(shutil.which(probe)), 'ffprobe có sẵn', 'Cài ffprobe cùng bộ FFmpeg')
        if found:
            try:
                features = _run([found, '-hide_banner', '-encoders']) + _run([found, '-hide_banner', '-filters'])
                add('ffmpeg_features', all(name in features for name in ('libx264', 'aac', 'subtitles', 'drawtext', 'loudnorm')),
                    'Đủ encoder và filter dựng video', 'Cần libx264, AAC, subtitles/libass, drawtext và loudnorm')
            except (OSError, subprocess.SubprocessError, TimeoutError):
                add('ffmpeg_features', False, '', 'Không chạy được FFmpeg trong thời hạn; kiểm tra executable')
        font_options = {'font': os.environ['STUDIO_WEBSITE_FONT']} if flow == 'website' and os.environ.get('STUDIO_WEBSITE_FONT') else cfg.video
        add('font', bool(find_font(font_options)), 'Font Unicode có sẵn', 'Cài DejaVu Sans hoặc cấu hình font Unicode .ttf')
        sections = {'tts': cfg.tts, 'image': cfg.image}
        if flow == 'film': sections['scene_video'] = cfg.scene_video
        if flow == 'website':
            try: ready = _chromium()
            except (OSError, RuntimeError): ready = False
            add('chromium', ready, 'Chromium đã cài', 'Chạy uv run playwright install chromium hoặc đặt STUDIO_WEBSITE_CHROMIUM')
            sections = {}
        for name, section in sections.items():
            provider = str(section.get('provider', 'none'))
            available = set(TTS_PROVIDERS) if name == 'tts' else set(IMAGE_PROVIDERS) if name == 'image' else {'comfyui', 'command'}
            if provider not in available | {'fake', 'none'}:
                add(name, False, '', 'Provider không được hỗ trợ; kiểm tra hồ sơ media')
                continue
            if provider == 'command':
                try:
                    argv = section.get('command') or ''
                    argv = shlex.split(argv) if isinstance(argv, str) else argv
                    ready = bool(argv and shutil.which(argv[0]))
                except (ValueError, TypeError): ready = False
                add(name, ready, 'Lệnh provider đã cấu hình và có executable', 'Kiểm tra lệnh provider và executable')
            else:
                add(name, provider not in {'none', 'fake'}, 'Provider đã cấu hình; chưa kiểm tra kết nối',
                    'Provider chưa cấu hình hoặc đang mô phỏng', warning=provider == 'fake')
        try:
            llm = load_config()
            backends = llm.backends or [{'provider': llm.provider, 'binary': llm.extra.get('binary')}]
            ready = any(b.get('provider') in {'codex', 'claude-code'} and
                        shutil.which(str(b.get('binary') or ('claude' if b['provider'] == 'claude-code' else 'codex')))
                        for b in backends)
            if flow != 'website': ready = ready or any(b.get('provider') not in {'fake', 'codex', 'claude-code'} for b in backends)
            add('llm', bool(ready), 'Backend text đã cấu hình; chưa kiểm tra đăng nhập hoặc quota',
                'Cấu hình backend text và cài CLI cần thiết', warning=flow != 'website')
        except (ValueError, OSError, AttributeError, LLMError, yaml.YAMLError): add('llm', False, '', 'Không đọc được cấu hình backend text')
    return {'ok': all(c['status'] != 'error' for c in checks), 'flow': flow, 'demo': demo,
            'verified': False, 'checks': checks}


def smoke(output: Path, cfg: MediaConfig) -> dict:
    """Twelve seconds of synthetic picture, tone and Vietnamese subtitles; no provider or publishing."""
    if output.exists(): raise ValueError('Smoke output already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    binary = str(cfg.video.get('binary', 'ffmpeg'))
    with tempfile.TemporaryDirectory(prefix='.smoke-', dir=output.parent) as temp:
        root = Path(temp)
        (root / 'captions.srt').write_text('1\n00:00:00,000 --> 00:00:12,000\nKiểm tra hình ảnh, âm thanh và phụ đề Việt\n', encoding='utf-8')
        _run([binary, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25',
              '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-vf', 'subtitles=captions.srt',
              '-t', '12', '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
              '-movflags', '+faststart', 'smoke.mp4'], timeout=120, cwd=root)
        raw = _run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', 'smoke.mp4'], cwd=root)
        data = json.loads(raw)
        if ({s['codec_type'] for s in data['streams']} != {'video', 'audio'}
                or abs(float(data['format']['duration']) - 12) > .1): raise ValueError('Smoke media failed validation')
        _run([binary, '-v', 'error', '-i', 'smoke.mp4', '-f', 'null', '-'], timeout=60, cwd=root)
        (root / 'smoke.mp4').replace(output)
    return {'verified': True, 'synthetic': True, 'duration_s': 12, 'output': str(output)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='X-Studio kiểm tra môi trường cục bộ, không gọi provider')
    parser.add_argument('--flow', choices=['render', 'website', 'film'], default='render')
    parser.add_argument('--media', type=Path)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--smoke', type=Path, help='Tạo fixture tổng hợp 12 giây, không gọi provider')
    args = parser.parse_args(argv)
    try:
        cfg = MediaConfig() if args.demo else load_media_config(args.media)
        report = check_environment(cfg, flow=args.flow, demo=args.demo)
        if args.smoke and report['ok']: report['smoke'] = smoke(args.smoke, cfg)
    except (ValueError, OSError, TypeError, subprocess.SubprocessError, LLMError, yaml.YAMLError):
        report = {'ok': False, 'verified': False, 'checks': [{'id':'configuration', 'status':'error',
                  'message':'Không kiểm tra được cấu hình/công cụ. Kiểm tra hồ sơ media và đường dẫn.'}]}
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
          '\n'.join(f"[{c['status']}] {c['message']}" for c in report['checks']))
    return 0 if report['ok'] else 1


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
