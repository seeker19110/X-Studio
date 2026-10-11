"""Subscription CLI designs SVG artwork; fixed code validates and rasterizes it.

No model-generated program is executed. The artwork has no scripts, external assets
or lettering; the renderer adds typography using the installed Vietnamese font.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .llm import LLMError, make_client
from .media import MediaError
from .sandbox import clean_env

TAGS = {'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'polygon', 'polyline', 'line'}
NUMBERS = {'x', 'y', 'width', 'height', 'rx', 'ry', 'cx', 'cy', 'r', 'x1', 'y1', 'x2', 'y2',
           'stroke-width', 'opacity', 'fill-opacity', 'stroke-opacity', 'stroke-dashoffset'}
NS = 'http://www.w3.org/2000/svg'


def sanitize_svg(value: str, width: int, height: int) -> str:
    if len(value) > 250000 or '<!' in value:
        raise MediaError('CLI artwork: SVG too large or contains declarations')
    try:
        root = ET.fromstring(value)
        nodes = list(root.iter())
        if root.tag not in {'svg', '{' + NS + '}svg'} or len(nodes) > 240:
            raise ValueError('invalid SVG root or too many shapes')
        for node in nodes:
            tag = node.tag.removeprefix('{' + NS + '}')
            if tag not in TAGS: raise ValueError('unsupported SVG element')
            node.tag = tag
            node.text = node.tail = None
            for key, val in node.attrib.items():
                if key in NUMBERS:
                    number = float(val)
                    if not math.isfinite(number) or abs(number) > 8192: raise ValueError('invalid coordinate')
                elif key in {'fill', 'stroke'}:
                    if not re.fullmatch(r'none|#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}', val): raise ValueError('invalid color')
                elif key == 'd':
                    if len(val) > 20000 or not re.fullmatch(r'[MmLlHhVvCcSsQqTtAaZz0-9eE.,+\s-]+', val):
                        raise ValueError('invalid path')
                elif key == 'points':
                    if not re.fullmatch(r'[0-9eE.,+\s-]+', val): raise ValueError('invalid points')
                elif key == 'transform':
                    if not re.fullmatch(r'(?:(?:translate|rotate|scale|matrix)\([0-9eE.,+\s-]+\)\s*)+', val):
                        raise ValueError('invalid transform')
                elif key == 'stroke-linecap':
                    if val not in {'butt', 'round', 'square'}: raise ValueError('invalid linecap')
                elif key == 'stroke-linejoin':
                    if val not in {'miter', 'round', 'bevel'}: raise ValueError('invalid linejoin')
                elif key == 'fill-rule':
                    if val not in {'nonzero', 'evenodd'}: raise ValueError('invalid fill-rule')
                elif key == 'stroke-dasharray':
                    if val != 'none':
                        dash = [float(n) for n in re.split(r'[\s,]+', val.strip())]
                        if not all(math.isfinite(n) and 0 <= n <= 8192 for n in dash):
                            raise ValueError('invalid stroke-dasharray')
                elif key == 'version' and node is root:
                    if val not in {'1.0', '1.1', '2.0'}: raise ValueError('invalid SVG version')
                elif key == 'preserveAspectRatio' and node is root:
                    if not re.fullmatch(r'none|x(?:Min|Mid|Max)Y(?:Min|Mid|Max)(?: (?:meet|slice))?', val):
                        raise ValueError('invalid preserveAspectRatio')
                elif key == 'viewBox' and node is root:
                    values = [float(n) for n in val.split()]
                    if len(values) != 4 or not all(math.isfinite(n) for n in values) or min(values[2:]) <= 0:
                        raise ValueError('invalid viewBox')
                else: raise ValueError('unsupported SVG attribute: ' + key)
        root.set('xmlns', NS)
        root.set('width', str(width)); root.set('height', str(height))
        if 'viewBox' not in root.attrib: root.set('viewBox', '0 0 1000 1000')
        return ET.tostring(root, encoding='unicode')
    except (ET.ParseError, ValueError) as exc:
        raise MediaError(f'CLI artwork: invalid SVG ({exc})') from exc


def generate(prompt: str, size: str, out: Path, ffmpeg: str = 'ffmpeg') -> None:
    try:
        width, height = [int(n) for n in size.split('x')]
        if not 1 <= min(width, height) <= max(width, height) <= 4096: raise ValueError('size out of bounds')
    except ValueError as exc:
        raise MediaError('CLI artwork: size must be WIDTHxHEIGHT, at most 4096') from exc
    result = make_client().complete(
        system=f'Create polished editorial vector artwork illustrating the requested scene. Return JSON with svg. '
        f'Use a 0 0 {width} {height} viewBox and compose for that aspect ratio. Fill the background. '
        'Use a cohesive bright palette, meaningful recognizable objects and layered depth. '
        'Make subjects large, with solid saturated fills and bold dark contours. Foreground objects should occupy '
        'at least one third of the canvas. Use two or three distinct background color regions. '
        'Avoid large nearly white empty areas or pale transparent subjects; prioritize readable contrast. '
        'Use only svg/g/path/rect/circle/ellipse/polygon/polyline/line with literal hex colors. '
        'All points values must contain complete numeric coordinate pairs, with no units or placeholders. '
        'No text, images, external resources, scripts, CSS, defs, gradients, filters or event handlers. '
        'Keep the important objects in the middle 65% so the video can crop or pan. At most 160 shapes.',
        user=prompt, schema={'type': 'object', 'properties': {'svg': {'type': 'string'}},
                             'required': ['svg'], 'additionalProperties': False}, model_tier='standard')
    svg = sanitize_svg(result.json()['svg'], width, height)
    out.parent.mkdir(parents=True, exist_ok=True)
    source = out.with_suffix('.svg'); source.write_text(svg, encoding='utf-8')
    out.with_suffix('.art.json').write_text(json.dumps({'model': result.model, 'prompt': prompt,
        'input_tokens': result.input_tokens, 'output_tokens': result.output_tokens}, ensure_ascii=False), encoding='utf-8')
    try:
        done = subprocess.run([ffmpeg, '-y', '-i', str(source), '-frames:v', '1', '-threads', '1', str(out)],
                              capture_output=True, text=True, timeout=120, env=clean_env())
        if done.returncode: raise MediaError('CLI artwork: rasterizer failed: ' + done.stderr[-1000:])
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaError('CLI artwork: cannot run rasterizer') from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', required=True); parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    try:
        generate(sys.stdin.read(), args.size, args.out, args.ffmpeg)
    except (MediaError, LLMError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':  # pragma: no cover - exercised by the live CLI smoke check
    main()
