"""Website exports use verified evidence, durable jobs and a constrained C-drive destination."""
import asyncio
from itertools import pairwise
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from studio.website import JobAction, WebsiteJobs, WebsiteRequest, c_path, plan_video
from studio.website_capture import ReadOnlyNetwork
from studio.website_render import caption_chunks, clock, fit_slots


@pytest.mark.parametrize('url', ['file:///C:/secret', 'http://user:pass@example.org', 'https://', 'javascript:alert(1)', 'http://example.org:99999'])
def test_reject_unsafe_website_url(url):
    with pytest.raises(ValueError): WebsiteRequest(url=url)


@pytest.mark.parametrize('path', ['D:\\Videos', '\\\\server\\share', 'C:Videos', 'C:\\', 'C:\\Videos\\..\\Windows', 'C:\\Videos\\NUL', 'C:\\Videos\\bad.', 'C:\\Videos\\bad:stream'])
def test_export_requires_normal_absolute_c_path(path):
    with pytest.raises(ValueError): WebsiteRequest(url='https://example.org', output_dir=path)


def test_request_and_c_path_defaults(tmp_path, monkeypatch):
    request = WebsiteRequest(url='  https://example.org/  ', mode='both', duration_s=90, quality='4k')
    assert request.url == 'https://example.org/' and request.output_dir == 'C:\\Videos\\X-Studio'
    monkeypatch.setattr('studio.website.C_MOUNT', tmp_path)
    assert c_path(request.output_dir) == tmp_path / 'Videos' / 'X-Studio'
    for values in ({'duration_s': 0}, {'duration_s': 301}, {'mode': 'bad'}, {'quality': '8k'}, {'extra': 1}):
        with pytest.raises(ValueError): WebsiteRequest(url='https://example.org', **values)
    outside = tmp_path.parent / 'other'; outside.mkdir(exist_ok=True)
    (tmp_path / 'escape').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError): c_path('C:\\escape\\file')


def evidence():
    return [{'id': 0, 'url': 'https://example.org', 'title': 'Home', 'capture_version': 4, 'text': 'Học tiếng Anh', 'links': []},
            {'id': 1, 'url': 'https://example.org/learn', 'title': 'Learn', 'capture_version': 4, 'text': 'Bắt đầu học', 'links': []}]


def payload():
    return {'title': 'Làm quen website', 'scenes': [{'page_id': i % 2, 'title': f'Bước {i}',
            'narration': 'Mở trang và đọc nội dung hướng dẫn.', 'instruction': 'Đọc nội dung trên trang'} for i in range(4)]}


def test_planner_validates_evidence_and_retries_bounded():
    from studio.llm import FakeClient
    client = FakeClient(responses=[{**payload(), 'scenes': [{**s, 'page_id': 77} for s in payload()['scenes']]}, payload()])
    plan = plan_video(client, WebsiteRequest(url='https://example.org'), evidence(), 'tutorial')
    assert len(plan.scenes) == 4 and len(client.calls) == 2
    assert 'KHÔNG TIN CẬY' in client.calls[0]['user']
    assert '77' in client.calls[1]['user']
    with pytest.raises(ValueError, match='3'):
        plan_video(FakeClient(responses=[{}] * 3), WebsiteRequest(url='https://example.org'), evidence(), 'intro')


def test_jobs_survive_restart_and_command_replay(tmp_path, monkeypatch):
    monkeypatch.setattr('studio.website.C_MOUNT', tmp_path)
    jobs = WebsiteJobs(tmp_path / 'jobs.sqlite', Mock())
    jobs.enqueue('a' * 32, {'url': 'https://example.org', 'mode': 'both'})
    jobs.enqueue('a' * 32, {'url': 'https://example.org'})
    assert len(jobs.snapshot()) == 1
    jobs.store.update('a' * 32, status='rendering', progress=60)
    jobs.close()
    reopened = WebsiteJobs(tmp_path / 'jobs.sqlite', Mock())
    assert reopened.snapshot()[0]['status'] == 'queued'
    reopened.action(JobAction(job_id='a' * 32, action='cancel'))
    assert reopened.snapshot()[0]['status'] == 'cancelled'
    reopened.action(JobAction(job_id='a' * 32, action='retry'))
    assert reopened.snapshot()[0]['status'] == 'queued'
    with pytest.raises(ValueError): reopened.action(JobAction(job_id='missing', action='retry'))
    reopened.close()


def test_readonly_network_pins_requests_and_blocks_writes(monkeypatch):
    monkeypatch.setattr('studio.website_capture.default_fetcher', lambda url, **kwargs: (200, 'text/html', url, b'body'))
    guard = ReadOnlyNetwork(); route = AsyncMock(); route.request.url = 'https://example.org'; route.request.method = 'GET'; route.request.headers = {}
    asyncio.run(guard.route(route))
    assert route.fulfill.call_args.kwargs['body'] == b'body'
    route.reset_mock(); route.request.method = 'POST'; asyncio.run(guard.route(route)); route.abort.assert_called_once()
    monkeypatch.setattr('studio.website_capture.default_fetcher', Mock(side_effect=ValueError('private')))
    route.reset_mock(); route.request.method = 'GET'; route.request.headers = {}; route.request.url = 'https://private.example'; asyncio.run(guard.route(route)); route.abort.assert_called_once()


def test_captions_and_timing_fit_requested_duration_without_cutting_speech():
    assert clock(61.234) == '00:01:01,234'
    chunks = caption_chunks(' '.join(['đồng hành cùng bạn'] * 12))
    assert all(len(line) <= 42 for c in chunks for line in c.split('\n'))
    slots, pace = fit_slots([12, 11, 10, 9], 60)
    assert sum(slots) == pytest.approx(60) and pace == 1
    slots, pace = fit_slots([16, 16, 16, 16], 60)
    assert sum(slots) == pytest.approx(60) and 1 < pace <= 1.25
    with pytest.raises(ValueError): fit_slots([25, 25, 25], 30)
    with pytest.raises(ValueError): fit_slots([2, 2, 2], 60)


def test_factory_accepts_website_jobs_and_serves_only_registered_exports(tmp_path, monkeypatch):
    from studio.factory import Factory
    from studio.media import MediaConfig
    monkeypatch.setattr('studio.website.C_MOUNT', tmp_path)
    from studio.llm import FakeClient
    monkeypatch.setattr('studio.factory.make_client',lambda:FakeClient())
    factory = Factory(tmp_path / 'studio.sqlite', MediaConfig(output_dir=tmp_path / 'out'), demo=False)
    try:
        factory.running = False
        id = factory.command('website', {'url': 'https://example.org', 'duration_s': 60})
        factory.cycle()
        assert factory.snapshot()['website_jobs'][0]['id'] == id
        assert factory.media_path('website-' + id + '-intro') is None
        job = factory.websites.store.get(id); root = Path(job['directory']); root.mkdir(parents=True)
        (root / 'intro.mp4').write_bytes(b'video')
        factory.websites.store.update(id, status='completed', outputs={'intro': 'intro.mp4'})
        assert factory.media_path('website-' + id + '-intro') == root / 'intro.mp4'
        assert factory.media_path('website-' + id + '-unknown') is None
        outside = tmp_path / 'outside.mp4'; outside.write_bytes(b'secret')
        factory.websites.store.update(id, outputs={'intro': '../outside.mp4'})
        assert factory.media_path('website-' + id + '-intro') is None
        factory.command('website_action', {'job_id': id, 'action': 'cancel'}); factory.cycle()
    finally: factory.close()


def test_cli_planner_uses_only_existing_subscription_profiles(monkeypatch):
    from studio.llm import LLMConfig
    from studio.website import cli_client
    for config in [LLMConfig(provider='codex'), LLMConfig(backends=[{'name':'sub','provider':'codex'}, {'provider':'openai'}])]:
        monkeypatch.setattr('studio.website.load_config', lambda c=config: c)
        client = Mock(); monkeypatch.setattr('studio.website.make_client', lambda cfg, client=client: client)
        assert cli_client() is client
        assert all(b['provider'] == 'codex' for b in config.backends)
    for config in [LLMConfig(), LLMConfig(backends=[{'provider':'openai'}])]:
        monkeypatch.setattr('studio.website.load_config', lambda c=config: c)
        with pytest.raises(ValueError, match=r'CLI|backend'): cli_client()


def test_job_pipeline_checkpoints_both_exports_and_reuses_sources(tmp_path, monkeypatch):
    from studio.llm import FakeClient
    monkeypatch.setattr('studio.website.C_MOUNT', tmp_path)
    capture = Mock(return_value=evidence()); monkeypatch.setattr('studio.website_capture.capture_site', capture)
    def render(request, plan, pages, root, kind, progress, checkpoint):
        checkpoint(); progress('verifying', .95, 'Checking')
        (root / (kind + '.mp4')).write_bytes(b'checked video')
        return {kind: kind + '.mp4'}
    monkeypatch.setattr('studio.website_render.render_video', render)
    client = FakeClient(responses=[payload(), payload()])
    monkeypatch.setattr('studio.website.cli_client', lambda: client)
    jobs = WebsiteJobs(tmp_path / 'jobs.sqlite')
    jobs.enqueue('b' * 32, {'url':'https://example.org', 'mode':'both'})
    jobs.run('b' * 32); row = jobs.store.get('b' * 32)
    assert row['status'] == 'completed' and row['progress'] == 100
    assert set(row['outputs']) == {'intro', 'tutorial'}
    jobs.run('b' * 32)
    assert capture.call_count == 1 and len(client.calls) == 2
    assert jobs.media_path('website-' + 'b' * 32 + '-intro').read_bytes() == b'checked video'
    assert jobs.media_path('website-missing-intro') is None
    assert jobs.media_path('wrong') is None
    jobs.action(JobAction(job_id='b'*32, action='cancel'))
    with pytest.raises(ValueError): jobs.action(JobAction(job_id='b'*32, action='retry'))
    with pytest.raises(ValueError): jobs.store.update('missing', status='failed')
    jobs.close()


@pytest.mark.parametrize('reason', ['cancel', 'shutdown', 'failure'])
def test_job_worker_can_cancel_resume_or_report_failure(tmp_path, monkeypatch, reason):
    monkeypatch.setattr('studio.website.C_MOUNT', tmp_path)
    jobs = WebsiteJobs(tmp_path / 'jobs.sqlite', Mock())
    jobs.enqueue('c' * 32, {'url':'https://example.org'})
    if reason == 'cancel': jobs.store.update('c'*32, cancel_requested=True)
    elif reason == 'shutdown': jobs.stop.set()
    else: monkeypatch.setattr('studio.website_capture.capture_site', Mock(side_effect=RuntimeError('capture failed')))
    jobs.run('c' * 32)
    expected = {'cancel':'cancelled','shutdown':'queued','failure':'failed'}[reason]
    assert jobs.store.get('c'*32)['status'] == expected
    if reason == 'failure': assert 'capture failed' in jobs.store.get('c'*32)['error']
    jobs.close()


def test_job_worker_starts_one_job_and_active_cancel_is_deferred(tmp_path, monkeypatch):
    monkeypatch.setattr('studio.website.C_MOUNT', tmp_path)
    jobs = WebsiteJobs(tmp_path / 'jobs.sqlite', Mock()); jobs.start()
    jobs.enqueue('job', {'url':'https://example.org'})
    calls = []; monkeypatch.setattr(jobs, 'run', lambda id: calls.append(id))
    jobs.start(); jobs.worker.join(); assert calls == ['job']
    jobs.worker = Mock(); jobs.worker.is_alive.return_value = True
    jobs.start(); assert calls == ['job']
    jobs.store.update('job', status='voicing')
    jobs.action(JobAction(job_id='job', action='cancel'))
    assert jobs.store.get('job')['cancel_requested'] and jobs.store.get('job')['status'] == 'voicing'
    jobs.close(); jobs.worker.join.assert_called_once()
    jobs.start()


@pytest.mark.parametrize('budget', ['requests', 'bytes', 'time', 'oversized'])
def test_capture_network_caps_resources_and_never_falls_back_to_browser_network(monkeypatch, budget):
    from studio.tools import MAX_BYTES
    guard = ReadOnlyNetwork(); route = AsyncMock(); route.request.url = 'https://example.org'; route.request.method = 'GET'; route.request.headers = {}
    if budget == 'requests': guard.requests = 1024
    if budget == 'bytes': guard.bytes = 128 * 1024 * 1024 + 1
    if budget == 'time': guard.deadline = 0
    monkeypatch.setattr('studio.website_capture.default_fetcher', lambda url, **kwargs: (200, 'text/plain', url, b'x' * (MAX_BYTES + 1)))
    asyncio.run(guard.route(route)); route.abort.assert_called_once(); route.fulfill.assert_not_called()


def test_discovery_stays_on_site_and_skips_write_links():
    from studio.website_capture import discover_links
    page = {'links':[{'url':u,'text':'Link'} for u in ['https://example.org/a#x','https://example.org/a#y',
            'https://other.org/b','javascript:bad','https://example.org/logout','https://example.org/b?token=x',
            'https://example.org/visited','https://example.org/learn']]}
    assert discover_links([page], 'example.org', {'https://example.org/visited'}) == ['https://example.org/a','https://example.org/learn']


def mock_capture_browser(monkeypatch, links=None, failures=None, challenge=False):
    from unittest.mock import MagicMock
    page = AsyncMock(); page.set_default_timeout = Mock(); current = {'url':'https://example.org'}
    async def goto(url, **kwargs):
        current['url'] = url
        if failures and url in failures: return failures[url]
        return Mock(status=200)
    async def evaluate(js, *args):
        return {'url':current['url'],'title':'Example','text':'verify you are human '*12 if challenge else 'Nội dung công khai trên trang này '*10,
                'links': links or []}
    page.goto.side_effect = goto; page.evaluate.side_effect = evaluate
    def screenshot(**kwargs):
        from PIL import Image, ImageDraw
        image=Image.new('RGB',(3200,2000),'white');ImageDraw.Draw(image).rectangle((100,100,1100,800),fill='blue')
        image.save(kwargs['path'])
    page.screenshot.side_effect = screenshot
    context = AsyncMock(); context.on = Mock(); context.new_page.return_value = page
    browser = AsyncMock(); browser.new_context.return_value = context
    playwright = AsyncMock(); playwright.chromium.launch.return_value = browser
    manager = MagicMock(); manager.__aenter__ = AsyncMock(return_value=playwright); manager.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr('playwright.async_api.async_playwright', lambda: manager)
    monkeypatch.setattr('studio.website_capture.pin_url', lambda url:(url, '1.1.1.1'))
    monkeypatch.setattr('studio.website_capture.default_fetcher', lambda url:(200, 'text/html', url, b'body'))
    return page, context, browser


def test_capture_snapshots_navigation_and_closes_browser(tmp_path, monkeypatch):
    from studio.website_capture import capture_site
    links = [{'url':'https://example.org/learn','text':'Học'}, {'url':'https://example.org/about','text':'Giới thiệu'}]
    _page, context, browser = mock_capture_browser(monkeypatch, links)
    pages = capture_site('https://example.org', tmp_path, 3, lambda:None)
    assert len(pages) == 3 and all((tmp_path / p['image']).exists() for p in pages)
    assert context.route.call_args.args[0] == '**/*'
    callback = context.route_web_socket.call_args.args[1]; socket = Mock(); callback(socket); socket.close.assert_called_once()
    browser.close.assert_awaited_once()


def test_capture_single_page_scrolls_and_skips_unavailable_secondary_page(tmp_path, monkeypatch):
    from studio.website_capture import capture_site
    _page, _, browser = mock_capture_browser(monkeypatch, [{'url':'https://example.org/error','text':'Error'}],
                                            {'https://example.org/error':Mock(status=404)})
    pages = capture_site('https://example.org', tmp_path, 3, lambda:None)
    assert len(pages) == 3 and pages[-1]['scroll_y'] == 1400
    browser.close.assert_awaited_once()


@pytest.mark.parametrize('error', ['http','none','challenge','cancel','empty','initial'])
def test_capture_reports_unavailable_site_and_always_closes(tmp_path, monkeypatch, error):
    from studio.website import ExportInterrupted
    from studio.website_capture import capture_site
    failures = {'https://example.org':None if error == 'none' else Mock(status=500)} if error in {'http','none'} else None
    _, _, browser = mock_capture_browser(monkeypatch, failures=failures, challenge=error == 'challenge')
    def checkpoint():
        if error == 'cancel': raise ExportInterrupted()
    if error == 'initial': monkeypatch.setattr('studio.website_capture.default_fetcher', lambda url:(403,'text/html',url,b''))
    with pytest.raises((ValueError, ExportInterrupted)):
        capture_site('https://example.org', tmp_path, 0 if error == 'empty' else 3, checkpoint)
    if error != 'initial': browser.close.assert_awaited_once()


def test_caption_files_have_safe_ass_text_and_actual_word_timing(tmp_path):
    from studio.website_render import ass_subtitles, subtitles, timed_captions
    words = [{'text':text,'start':i*.5,'duration':.4} for i,text in enumerate(['Mở trang.'] + ['đồng hành']*14)]
    cues = timed_captions(words, .4, 1.1)
    assert len(cues) > 1 and cues[0]['start'] == .4
    assert all(c['end'] < n['start'] for c,n in pairwise(cues))
    assert timed_captions([],0,1) == []
    subtitles(tmp_path/'srt', cues); assert '00:00:00,400' in (tmp_path/'srt').read_text()
    ass_subtitles(tmp_path/'ass', [{'start':0,'end':1,'text':'{\\pos(1,2)}\nXin chào'}],1920,1080)
    output=(tmp_path/'ass').read_text(); assert '\\pos' not in output and r'\NXin chào' in output
    assert caption_chunks('x'*170) == ['x'*42+'\n'+'x'*42, 'x'*42+'\n'+'x'*42, 'xx']
    assert caption_chunks('') == []
    for durations in ([], [float('nan')], [0]):
        with pytest.raises(ValueError): fit_slots(durations,60)
    with pytest.raises(ValueError): fit_slots([10]*3,100)


def test_speech_stages_audio_and_preserves_cache_on_failure(tmp_path, monkeypatch):
    from studio.website_render import speech, voice_audio
    class Voice:
        def __init__(self,*args,**kwargs): pass
        async def stream(self):
            for chunk in [{'type':'audio','data':b'voice'}, {'type':'WordBoundary','text':'Xin chào','offset':1000000,'duration':2000000}]:
                yield chunk
    monkeypatch.setattr('edge_tts.Communicate', Voice)
    monkeypatch.setattr('studio.website_render.probe', lambda path:{'format':{'duration':'4'}})
    first=speech('Xin chào','voice',tmp_path); second=speech('Xin chào','voice',tmp_path)
    assert first == second and first[1] == 4
    class Empty(Voice):
        async def stream(self):
            yield {'type':'audio','data':b''}
    monkeypatch.setattr('edge_tts.Communicate', Empty)
    with pytest.raises(ValueError): asyncio.run(voice_audio('hello','voice',tmp_path/'empty'))
    with pytest.raises(ValueError): speech('fails','voice',tmp_path)
    assert not list(tmp_path.glob('*.part.mp3')) and first[0].read_bytes() == b'voice'


def test_media_subprocess_reports_failures_and_timeout(monkeypatch):
    import subprocess

    from studio.website_render import probe, run
    monkeypatch.setattr('studio.website_render.subprocess.run', lambda *a,**k:Mock(returncode=0,stdout='{"streams":[]}',stderr=''))
    assert probe(Path('audio.mp3')) == {'streams':[]}
    monkeypatch.setattr('studio.website_render.subprocess.run', lambda *a,**k:Mock(returncode=1,stderr='codec failed'))
    with pytest.raises(ValueError,match='codec failed'): run(['ffmpeg'])
    monkeypatch.setattr('studio.website_render.subprocess.run', Mock(side_effect=subprocess.TimeoutExpired('ffmpeg',900)))
    with pytest.raises(subprocess.TimeoutExpired): run(['ffmpeg'])


def test_unicode_card_is_native_4k_and_requires_readable_font(tmp_path, monkeypatch):
    from PIL import Image

    from studio.website import WebsitePlan
    from studio.website_render import card, font_file
    image=tmp_path/'capture.png';Image.new('RGB',(3200,2000),'#edb663').save(image)
    font='/mnt/c/Windows/Fonts/segoeui.ttf'
    if not Path(font).exists(): font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    monkeypatch.setenv('STUDIO_WEBSITE_FONT',font)
    plan=WebsitePlan.model_validate(payload());plan.title='Đồng Hành Cùng Bạn — '+('Làm quen '*10)
    card(plan,plan.scenes[0],image,tmp_path/'card.png','4k',0,'https://example.org')
    with Image.open(tmp_path/'card.png') as output: assert output.size == (3840,2160) and output.getpixel((0,0)) == (55,221,189)
    monkeypatch.delenv('STUDIO_WEBSITE_FONT'); assert Path(font_file()).is_file()
    monkeypatch.setenv('STUDIO_WEBSITE_FONT',str(tmp_path/'missing.ttf'))
    with pytest.raises(ValueError,match='Unicode'):font_file()


def test_renderer_caches_segments_and_verifies_before_publishing(tmp_path, monkeypatch):
    from PIL import Image

    from studio.website import WebsitePlan
    from studio.website_render import render_video
    source=tmp_path/'source';source.mkdir();Image.new('RGB',(1600,1000),'orange').save(source/'p.png')
    pages=[{**p,'image':'p.png'} for p in evidence()]
    monkeypatch.setattr('studio.website_render.shutil.which',lambda binary: binary)
    audio=source/'voice.wav';audio.write_bytes(b'audio')
    monkeypatch.setattr('studio.website_render.speech',lambda *args:(audio,4,[{'text':'Xin chào','start':0,'duration':1}]))
    monkeypatch.setattr('studio.website_render.card',lambda plan,scene,screen,out,*args:out.write_bytes(b'card'))
    calls=[]
    def command(argv,cwd=None,timeout=900):
        calls.append(argv)
        out=Path(argv[-1]);out=out if out.is_absolute() else cwd/out
        out.write_bytes(b'encoded');return ''
    monkeypatch.setattr('studio.website_render.run',command)
    monkeypatch.setattr('studio.website_render.verify_video',lambda *args:{'passed':True})
    events=[];request=WebsiteRequest(url='https://example.org',duration_s=20)
    exports=render_video(request,WebsitePlan.model_validate(payload()),pages,tmp_path,'intro',lambda *a:events.append(a),lambda:None)
    assert (tmp_path/exports['intro']).read_bytes() == b'encoded' and events[-1][0] == 'verifying'
    count=sum('.part.mp4' in a[-1] and 'segment-' in a[-1] for a in calls)
    render_video(request,WebsitePlan.model_validate(payload()),pages,tmp_path,'intro',lambda *a:None,lambda:None)
    assert sum('.part.mp4' in a[-1] and 'segment-' in a[-1] for a in calls) == count == 4
    monkeypatch.setattr('studio.website_render.verify_video',Mock(side_effect=ValueError('QC failed')))
    with pytest.raises(ValueError,match='QC failed'):
        render_video(request,WebsitePlan.model_validate(payload()),pages,tmp_path,'intro',lambda *a:None,lambda:None)
    assert (tmp_path/'intro.mp4').read_bytes() == b'encoded'
    monkeypatch.setattr('studio.website_render.shutil.which',lambda binary:None)
    with pytest.raises(ValueError,match='FFmpeg'):
        render_video(request,WebsitePlan.model_validate(payload()),pages,tmp_path,'intro',lambda *a:None,lambda:None)


def test_output_verification_checks_codec_duration_and_decodes_all_frames(tmp_path, monkeypatch):
    from studio.website_render import verify_video
    file=tmp_path/'video.mp4';file.write_bytes(b'video')
    info={'streams':[{'codec_type':'video','width':1920,'height':1080,'codec_name':'h264','pix_fmt':'yuv420p','avg_frame_rate':'30/1'},
                     {'codec_type':'audio','codec_name':'aac','sample_rate':'48000','channels':2}], 'format':{'duration':'60'}}
    monkeypatch.setattr('studio.website_render.probe',lambda p:info);decode=Mock();monkeypatch.setattr('studio.website_render.run',decode)
    report=verify_video(file,WebsiteRequest(url='https://example.org'),'ffmpeg')
    assert report['passed'] and report['bytes'] == 5 and len(report['sha256']) == 64
    decode.assert_called_once()
    info['format']['duration']='55'
    with pytest.raises(ValueError,match='kiểm tra'):verify_video(file,WebsiteRequest(url='https://example.org'),'ffmpeg')


def test_job_automatically_rewrites_narration_that_does_not_fit(tmp_path, monkeypatch):
    from studio.llm import FakeClient
    from studio.website_render import NarrationFitError
    monkeypatch.setattr('studio.website.C_MOUNT', tmp_path)
    monkeypatch.setattr('studio.website_capture.capture_site',lambda *a:evidence())
    attempts=[]
    def render(*args):
        attempts.append(1)
        if len(attempts)==1: raise NarrationFitError('Lời đọc 75 giây, cần tối đa 57 giây')
        return {'intro':'intro.mp4'}
    monkeypatch.setattr('studio.website_render.render_video',render)
    client=FakeClient(responses=[payload(),payload()]);jobs=WebsiteJobs(tmp_path/'jobs.sqlite',client)
    jobs.enqueue('fit',{'url':'https://example.org'});jobs.run('fit')
    assert jobs.store.get('fit')['status']=='completed' and len(attempts)==2
    assert '75 giây' in client.calls[-1]['user']
    monkeypatch.setattr('studio.website_render.render_video',Mock(side_effect=NarrationFitError('too long')))
    client.responses.extend([payload()]*3);jobs.run('fit')
    assert jobs.store.get('fit')['status']=='failed' and 'too long' in jobs.store.get('fit')['error']
    jobs.close()


def test_pinned_fetch_preserves_read_only_app_headers_without_forwarding_credentials():
    from studio.tools import default_fetcher
    response=Mock();response.status=200;response.headers={'Content-Type':'text/x-component'};response.read.return_value=b'RSC'
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    requests=[]
    def opener(ip,request): requests.append(request);return response
    from unittest.mock import patch
    with patch('studio.tools.pin_url',return_value=('https://example.org','1.1.1.1')):
        assert default_fetcher('https://example.org',opener=opener,request_headers={'RSC':'1','Accept':'text/x-component',
                'Authorization':'Bearer secret','Cookie':'session=secret'})[3] == b'RSC'
    assert requests[0].get_header('Rsc')=='1' and requests[0].get_header('Accept')=='text/x-component'
    assert not requests[0].has_header('Authorization') and not requests[0].has_header('Cookie')


def test_capture_does_not_count_a_redirected_duplicate_as_a_new_step(tmp_path,monkeypatch):
    from studio.website_capture import capture_site
    page,_,browser=mock_capture_browser(monkeypatch,[{'url':'https://example.org/a','text':'A'}])
    async def same_page(js,*args):
        return {'url':'https://example.org','title':'Home','text':'Thông tin công khai '*16,'links':[{'url':'https://example.org/a','text':'A'}]}
    page.evaluate.side_effect=same_page
    pages=capture_site('https://example.org',tmp_path,3,lambda:None)
    assert len(pages)==3 and pages[1]['scroll_y']==700
    browser.close.assert_awaited_once()


def test_browser_uses_pinned_dns_for_native_same_site_responses(monkeypatch):
    guard=ReadOnlyNetwork(pinned_hosts={'example.org':'1.1.1.1'})
    route=AsyncMock();route.request.url='https://example.org/page';route.request.method='GET';route.request.headers={}
    asyncio.run(guard.route(route))
    route.continue_.assert_awaited_once();route.fulfill.assert_not_called()
    route=AsyncMock();route.request.url='https://example.org:8181/page';route.request.method='GET';route.request.headers={}
    monkeypatch.setattr('studio.website_capture.default_fetcher',Mock(side_effect=ValueError('invalid port')))
    asyncio.run(guard.route(route));route.abort.assert_awaited_once()


def test_capture_evidence_rejects_blank_or_non_retina_screenshots(tmp_path):
    from PIL import Image, ImageDraw

    from studio.website_capture import check_capture
    path=tmp_path/'page.png';Image.new('RGB',(3200,2000),'white').save(path)
    with pytest.raises(ValueError,match='trống'):check_capture(path)
    image=Image.new('RGB',(3200,2000),'white');ImageDraw.Draw(image).rectangle((100,100,1100,800),fill='blue');image.save(path)
    check_capture(path)
    Image.new('RGB',(100,100),'red').save(path)
    with pytest.raises(ValueError,match='3200'):check_capture(path)


def test_native_response_budget_and_malformed_headers():
    from studio.tools import MAX_BYTES
    guard=ReadOnlyNetwork(pinned_hosts={'example.org':'1.1.1.1'})
    guard.observe_response(Mock(url='https://other.org',headers={'content-length':'10'}));assert guard.bytes==0
    guard.observe_response(Mock(url='https://example.org',headers={'content-length':'unknown'}));assert guard.bytes==0
    guard.observe_response(Mock(url='https://example.org',headers={'content-length':'100'}));assert guard.bytes==100
    guard.observe_response(Mock(url='https://example.org',headers={'content-length':str(MAX_BYTES+1)}))
    assert guard.bytes > 128*1024*1024


def test_capture_skips_auth_redirects_and_resource_exhaustion(tmp_path,monkeypatch):
    from studio.website_capture import ReadOnlyNetwork, capture_site
    page,_,_=mock_capture_browser(monkeypatch,[{'url':'https://example.org/locked','text':'Locked'}])
    def evaluate(js,*args):
        return {'url':'https://example.org/login' if page.goto.call_count==2 else 'https://example.org',
                'title':'Home','text':'Public content '*30,'links':[{'url':'https://example.org/locked','text':'Locked'}]}
    page.evaluate.side_effect=evaluate
    assert len(capture_site('https://example.org',tmp_path,3,lambda:None))==3
    mock_capture_browser(monkeypatch)
    guard=ReadOnlyNetwork();guard.bytes=128*1024*1024+1
    monkeypatch.setattr('studio.website_capture.ReadOnlyNetwork',lambda *a,**kw:guard)
    with pytest.raises(ValueError,match='giới hạn'):capture_site('https://example.org',tmp_path,3,lambda:None)


def test_windows_destination_mapping_is_absolute(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('studio.website.os',SimpleNamespace(name='nt'));monkeypatch.chdir(tmp_path)
    assert c_path('C:\\Videos\\X-Studio') == (Path('C:/')/'Videos'/'X-Studio').resolve()


def test_offline_demo_never_starts_real_website_generation(tmp_path):
    from studio.factory import Factory
    from studio.media import MediaConfig
    factory=Factory(tmp_path/'studio.sqlite',MediaConfig(output_dir=tmp_path/'out'),demo=True)
    try:
        with pytest.raises(ValueError,match='demo'):factory.command('website',{'url':'https://example.org'})
        assert not factory.commands.pending()
    finally:factory.close()


def test_modern_site_can_load_four_pages_of_small_app_resources():
    guard=ReadOnlyNetwork(pinned_hosts={'example.org':'1.1.1.1'});guard.requests=300
    route=AsyncMock();route.request.url='https://example.org/assets/app.js';route.request.method='GET';route.request.headers={}
    asyncio.run(guard.route(route));route.continue_.assert_awaited_once()


def test_tts_retries_temporary_service_failure_without_repeating_cached_speech(tmp_path,monkeypatch):
    from studio.website_render import speech
    attempts=[]
    async def service(text,voice,path):
        attempts.append(text)
        if len(attempts)==1: raise OSError('TTS temporarily unavailable')
        path.write_bytes(b'audio');return [{'text':'Xin chào','start':0,'duration':1}]
    monkeypatch.setattr('studio.website_render.voice_audio',service)
    monkeypatch.setattr('studio.website_render.probe',lambda path:{'format':{'duration':'4'}})
    monkeypatch.setattr('time.sleep',lambda seconds:None)
    speech('Xin chào','voice',tmp_path);speech('Xin chào','voice',tmp_path)
    assert len(attempts)==2
