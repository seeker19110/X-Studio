"""CLI artwork stays declarative: validate SVG and rasterize without executing model code."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio.cli_visual import generate, sanitize_svg
from studio.media import MediaError


def test_svg_preserves_actual_illustration_and_output_dimensions():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1500"><g fill="#f97316"><path d="M 10 20 L 30 40 Z"/><rect x="20" y="40" width="50" height="80" rx="5"/><circle cx="30" cy="40" r="10"/><ellipse cx="30" cy="40" rx="10" ry="20"/><polygon points="1,2 3,4 5,6"/><polyline points="1 2 3 4"/><line x1="1" y1="2" x2="3" y2="4" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.5" transform="translate(10 20)"/></g></svg>'
    result = sanitize_svg(svg, 720, 1280)
    assert 'width="720"' in result and 'height="1280"' in result
    assert 'viewBox="0 0 1000 1500"' in result and 'fill="#f97316"' in result
    assert 'M 10 20 L 30 40 Z' in result


@pytest.mark.parametrize("svg", [
    'not XML', '<html/>', '<svg xmlns="urn:other"/>', '<svg><script/></svg>',
    '<svg><image href="https://example.com/a.png"/></svg>', '<svg onload="alert(1)"/>',
    '<svg><rect fill="url(https://example.com)"/></svg>', '<svg><rect x="nan"/></svg>',
    '<svg><rect x="999999"/></svg>', '<svg><path d="alert(1)"/></svg>',
    '<svg><polygon points="bad"/></svg>', '<svg><g transform="url(x)"/></svg>',
    '<svg><path stroke-linecap="bad"/></svg>', '<svg><path stroke-linejoin="bad"/></svg>',
    '<svg viewBox="0 0 0 20"/>', '<svg viewBox="0 0 20"/>', '<!DOCTYPE svg><svg/>',
    '<svg>' + '<rect/>' * 241 + '</svg>', '<svg>' + ' ' * 250001 + '</svg>',
])
def test_svg_rejects_active_external_or_malformed_content(svg):
    with pytest.raises(MediaError): sanitize_svg(svg, 720, 1280)


def test_generate_uses_real_client_and_records_model(tmp_path, monkeypatch):
    from studio import cli_visual
    calls = []
    class Client:
        def complete(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(json=lambda: {'svg': '<svg><rect width="1000" height="1000" fill="#fff"/></svg>'},
                                   model='actual-cli-model', input_tokens=120, output_tokens=80)
    monkeypatch.setattr(cli_visual, 'make_client', lambda: Client())
    def run(argv, **kwargs):
        assert argv[0] == 'my-ffmpeg' and '-frames:v' in argv
        assert kwargs['timeout'] == 120
        Path(argv[-1]).write_bytes(b'PNG')
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(cli_visual.subprocess, 'run', run)
    out = tmp_path / 'image.png'
    generate('A student reading a book', '720x1280', out, 'my-ffmpeg')
    assert calls[0]['model_tier'] == 'standard' and calls[0]['user'] == 'A student reading a book'
    assert out.exists() and out.with_suffix('.svg').exists()
    assert 'actual-cli-model' in out.with_suffix('.art.json').read_text()


@pytest.mark.parametrize('size', ['wrong', '0x720', '10000x720'])
def test_generate_rejects_invalid_size(tmp_path, size):
    with pytest.raises(MediaError): generate('p', size, tmp_path / 'a.png')


@pytest.mark.parametrize('failure', ['exit', 'oserror'])
def test_generate_reports_render_failure(tmp_path, monkeypatch, failure):
    from studio import cli_visual
    monkeypatch.setattr(cli_visual, 'make_client', lambda: SimpleNamespace(complete=lambda **k:
        SimpleNamespace(json=lambda: {'svg': '<svg/>'}, model='cli', input_tokens=1, output_tokens=1)))
    def run(*a, **k):
        if failure == 'oserror': raise OSError('missing ffmpeg')
        return subprocess.CompletedProcess(a, 1, '', 'bad rasterizer')
    monkeypatch.setattr(cli_visual.subprocess, 'run', run)
    with pytest.raises(MediaError): generate('p', '720x1280', tmp_path / 'a.png')


def test_cli_main_reads_prompt_stdin(tmp_path, monkeypatch):
    import io

    from studio import cli_visual
    calls = []
    monkeypatch.setattr(cli_visual.sys, 'argv', ['cli_visual', '--size', '720x1280', '--out', str(tmp_path / 'a.png')])
    monkeypatch.setattr(cli_visual.sys, 'stdin', io.StringIO('hình tiếng Việt'))
    monkeypatch.setattr(cli_visual, 'generate', lambda *args: calls.append(args))
    cli_visual.main()
    assert calls == [('hình tiếng Việt', '720x1280', tmp_path / 'a.png', 'ffmpeg')]


def test_svg_accepts_safe_fill_rules_and_rejects_unknown_rules():
    assert 'fill-rule="evenodd"' in sanitize_svg('<svg><path d="M0 0Z" fill-rule="evenodd"/></svg>', 720, 1280)
    assert 'fill-rule="nonzero"' in sanitize_svg('<svg><path d="M0 0Z" fill-rule="nonzero"/></svg>', 720, 1280)
    with pytest.raises(MediaError, match='fill-rule'):
        sanitize_svg('<svg><path fill-rule="invalid"/></svg>', 720, 1280)


def test_svg_accepts_safe_dashed_strokes_and_root_metadata():
    svg = '<svg version="1.1" preserveAspectRatio="xMidYMid meet"><path d="M0 0 L10 10" stroke-dasharray="12, 8" stroke-dashoffset="2"/></svg>'
    assert 'stroke-dasharray="12, 8"' in sanitize_svg(svg, 720, 1280)
    assert 'stroke-dasharray="none"' in sanitize_svg('<svg><path stroke-dasharray="none"/></svg>', 720, 1280)
    for value in ['<svg version="invalid"/>', '<svg preserveAspectRatio="bad"/>',
                  '<svg><path stroke-dasharray="url(x)"/></svg>', '<svg><path stroke-dasharray="-2 1"/></svg>']:
        with pytest.raises(MediaError): sanitize_svg(value, 720, 1280)


def test_cli_main_reports_actionable_error_without_traceback(tmp_path, monkeypatch, capsys):
    import io

    from studio import cli_visual
    monkeypatch.setattr(cli_visual.sys, 'argv', ['cli_visual', '--size', '720x1280', '--out', str(tmp_path / 'a.png')])
    monkeypatch.setattr(cli_visual.sys, 'stdin', io.StringIO('scene'))
    def fail(*args): raise MediaError('unsupported SVG attribute: style')
    monkeypatch.setattr(cli_visual, 'generate', fail)
    with pytest.raises(SystemExit) as exc: cli_visual.main()
    assert exc.value.code == 1
    assert capsys.readouterr().err.strip() == 'unsupported SVG attribute: style'
