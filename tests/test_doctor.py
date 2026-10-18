import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from studio import doctor
from studio.media import MediaConfig


def test_doctor_reports_missing_tools_without_network_or_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.shutil, 'which', lambda _: None)
    monkeypatch.setattr(doctor, 'find_font', lambda *args: None)
    monkeypatch.setattr(doctor, 'load_config', lambda: SimpleNamespace(provider='fake', backends=[]))
    monkeypatch.setattr(doctor.subprocess, 'run', Mock(side_effect=AssertionError('missing executables cannot run')))
    out = tmp_path / 'not-created'
    report = doctor.check_environment(MediaConfig(output_dir=out), flow='website')
    assert not report['ok'] and not out.exists()
    assert {'ffmpeg', 'ffprobe', 'font', 'llm'} <= {c['id'] for c in report['checks'] if c['status'] == 'error'}
    assert report['verified'] is False
    assert all(c['message'] for c in report['checks'])


def test_doctor_demo_is_offline_and_does_not_require_media_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.subprocess, 'run', Mock(side_effect=AssertionError('offline demo')))
    assert doctor.check_environment(MediaConfig(output_dir=tmp_path), demo=True)['ok']


def test_doctor_validates_filters_disk_provider_without_exposing_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.shutil, 'which', lambda _: '/bin/tool')
    monkeypatch.setattr(doctor, 'find_font', lambda *args: Path('/font.ttf'))
    monkeypatch.setattr(doctor.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    monkeypatch.setattr(doctor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='libx264 drawtext', stderr=''))
    cfg = MediaConfig(output_dir=tmp_path, tts={'provider':'command', 'command':'"secret-unclosed'})
    report = doctor.check_environment(cfg)
    assert not report['ok'] and 'secret-unclosed' not in json.dumps(report)
    assert any(c['id'] == 'disk' and c['status'] == 'error' for c in report['checks'])
    assert any(c['id'] == 'ffmpeg_features' and c['status'] == 'error' for c in report['checks'])


@pytest.mark.parametrize('failure', [OSError('secret-key'), TimeoutError('secret-key')])
def test_doctor_tool_failures_are_actionable_and_redacted(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(doctor.shutil, 'which', lambda _: '/bin/tool')
    monkeypatch.setattr(doctor.subprocess, 'run', Mock(side_effect=failure))
    report = doctor.check_environment(MediaConfig(output_dir=tmp_path))
    assert not report['ok'] and 'secret-key' not in json.dumps(report)


def test_doctor_cli_json_and_missing_profile_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(doctor, 'check_environment', lambda *a, **kw: {'ok': True, 'checks': [], 'verified': False})
    assert doctor.main(['--demo', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['ok']
    monkeypatch.setattr(doctor, 'load_media_config', Mock(side_effect=ValueError('secret')))
    assert doctor.main(['--json']) == 1
    assert 'secret' not in capsys.readouterr().out


def test_website_readiness_failure_precedes_capture_or_model(tmp_path, monkeypatch):
    from studio.website import WebsiteJobs
    monkeypatch.setattr('studio.website.C_MOUNT', tmp_path)
    monkeypatch.setattr(doctor, 'check_environment', lambda *a, **kw: {'ok':False, 'checks':[{'status':'error','message':'Cần FFmpeg'}]})
    capture = Mock(side_effect=AssertionError('capture must not start'))
    monkeypatch.setattr('studio.website_capture.capture_site', capture)
    jobs = WebsiteJobs(tmp_path / 'studio.sqlite')
    jobs.enqueue('a', {'url':'https://example.org'})
    jobs.run('a')
    assert jobs.store.get('a')['status'] == 'failed'
    assert 'Cần FFmpeg' in jobs.store.get('a')['error']
    capture.assert_not_called()


def test_cli_dispatches_operations(monkeypatch):
    from studio import cli
    monkeypatch.setattr(cli.doctor, 'main', lambda args: 2 if args == ['--demo'] else 1)
    monkeypatch.setattr(cli.backup, 'main', lambda args: 3 if args == ['create'] else 1)
    assert cli.main(['doctor','--demo']) == 2
    assert cli.main(['backup','create']) == 3


def test_local_doctor_success_and_chromium_resolution(tmp_path, monkeypatch):
    font = tmp_path / 'font.ttf'; font.write_bytes(b'fixture')
    monkeypatch.setenv('STUDIO_WEBSITE_CHROMIUM', str(font))
    monkeypatch.setattr(doctor.shutil, 'which', lambda _: str(font))
    assert doctor._chromium()
    monkeypatch.delenv('STUDIO_WEBSITE_CHROMIUM')
    runtime = Mock(); runtime.__enter__ = Mock(return_value=runtime); runtime.__exit__ = Mock(return_value=False)
    runtime.chromium.executable_path = str(font)
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda:runtime)
    assert doctor._chromium()
    monkeypatch.setattr(doctor, 'find_font', lambda *a: font)
    monkeypatch.setattr(doctor, 'load_config', lambda: SimpleNamespace(provider='codex', backends=[], extra={}))
    monkeypatch.setattr(doctor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='libx264 aac subtitles drawtext loudnorm', stderr=''))
    cfg = MediaConfig(output_dir=tmp_path, tts={'provider':'command','command':'tool --out {out}'})
    assert doctor.check_environment(cfg, flow='website')['ok']
    assert doctor.check_environment(cfg, flow='film')['flow'] == 'film'
    monkeypatch.setattr(doctor, '_chromium', Mock(side_effect=RuntimeError('browser unavailable')))
    monkeypatch.setattr(doctor.shutil, 'disk_usage', Mock(side_effect=OSError('disk unavailable')))
    assert not doctor.check_environment(cfg, flow='website')['ok']


def test_doctor_smoke_failure_and_cli_text(tmp_path, monkeypatch, capsys):
    actual_run = doctor._run
    existing = tmp_path / 'exists.mp4'; existing.touch()
    with pytest.raises(ValueError): doctor.smoke(existing, MediaConfig())
    monkeypatch.setattr(doctor, '_run', lambda argv, **kw: '{"streams":[{"codec_type":"video"}],"format":{"duration":"12"}}')
    with pytest.raises(ValueError): doctor.smoke(tmp_path / 'invalid.mp4', MediaConfig())
    monkeypatch.setattr(doctor, 'check_environment', lambda *a, **kw: {'ok':True,'checks':[{'status':'ok','message':'fixture'}]})
    monkeypatch.setattr(doctor, 'smoke', lambda *a: {'verified':True})
    assert doctor.main(['--demo','--smoke',str(tmp_path / 'smoke.mp4')]) == 0
    assert '[ok] fixture' in capsys.readouterr().out
    monkeypatch.setattr(doctor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(OSError): actual_run(['tool'])
    monkeypatch.setattr(doctor.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('tool', 10)))
    with pytest.raises(subprocess.TimeoutExpired): actual_run(['tool'])


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='requires real FFmpeg')
def test_synthetic_smoke_has_real_picture_audio_and_twelve_seconds(tmp_path):
    output = tmp_path / 'smoke.mp4'
    result = doctor.smoke(output, MediaConfig())
    assert result['verified'] and output.stat().st_size > 10000


def test_unknown_provider_is_an_error_without_running_it(tmp_path,monkeypatch):
    monkeypatch.setattr(doctor.shutil,'which',lambda _:None)
    cfg=MediaConfig(output_dir=tmp_path,tts={'provider':'typo'})
    report=doctor.check_environment(cfg)
    assert any(c['id']=='tts' and c['status']=='error' for c in report['checks'])
