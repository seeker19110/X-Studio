"""Bounded rendering keeps scene timing and resumes without encoding completed scenes."""
import json
import shutil
import subprocess

import pytest

from studio.bus import InMemoryBus
from studio.events import Scene, SceneManifest
from studio.media import MediaConfig, make_media
from studio.renderer import Renderer


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires ffmpeg")
def test_bounded_real_render_resume_duration_and_finishing(tmp_path):
    cfg = MediaConfig(output_dir=tmp_path, render={"mode": "bounded", "threads": 1},
                      video={"provider": "ffmpeg", "resolution": "160x90", "fps": 10,
                             "burn_captions": True, "motion": "none"})
    r = Renderer(InMemoryBus(), make_media(cfg))
    m = SceneManifest(video_id="LONG", scenes=[Scene(scene_id=f"S{i}", order=i, narration="Xin chào.", visual_prompt="p")
                                             for i in range(3)])
    r.render(m)
    cache = list((tmp_path / "LONG" / ".segments").glob("*.mkv"))
    assert 1 <= len(cache) <= 3
    mtimes = [p.stat().st_mtime_ns for p in cache]
    final = r.finalize(m)
    assert [p.stat().st_mtime_ns for p in cache] == mtimes
    data = json.loads(subprocess.check_output(["ffprobe", "-v", "quiet", "-show_format", "-show_streams", "-of", "json", final.path]))
    duration = float(data["format"]["duration"])
    assert abs(duration - final.duration_s) < .15
    assert abs(r.cues(m)[-1].end + r.media.video.tail_pad_s - duration) < .2
    assert all(c.start >= 0 and c.end > c.start for c in r.cues(m))
    assert "video" in {s["codec_type"] for s in data["streams"]}


def test_bounded_mode_is_selected_without_changing_legacy_graph_mode(tmp_path):
    from studio.long_render import BoundedAssembler
    from studio.media import FFmpegAssembler
    if shutil.which("ffmpeg") is None: pytest.skip("requires ffmpeg")
    suite = make_media(MediaConfig(render={"mode": "bounded"}, video={"provider": "ffmpeg"}))
    assert isinstance(suite.video, BoundedAssembler) and suite.video.transition_s == 0
    assert type(make_media(MediaConfig(video={"provider": "ffmpeg"})).video) is FFmpegAssembler


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='requires ffmpeg')
def test_real_footage_and_looped_music_finish_without_extending_video(tmp_path):
    import yaml

    from studio.media import probe_duration
    uploads=tmp_path/'uploads';uploads.mkdir()
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10','-t','1','-c:v','libx264',str(uploads/'clip.mp4')],check=True)
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=220:duration=0.3',str(uploads/'music.wav')],check=True)
    (uploads/'library.yaml').write_text(yaml.safe_dump({'assets':{'clip':{'kind':'video','path':'clip.mp4','license':'owned'},
        'music':{'kind':'music','path':'music.wav','license':'owned'}}}))
    cfg=MediaConfig(output_dir=tmp_path,video={'provider':'ffmpeg','resolution':'160x90','fps':10},
                    render={'mode':'bounded','threads':1,'timeout_s':10},
                    production={'scenes':{'V':{'S':'clip'}},'music':{'V':'music'}})
    r=Renderer(InMemoryBus(),make_media(cfg));m=SceneManifest(video_id='V',scenes=[Scene(scene_id='S',order=0,narration='Hello world',visual_prompt='x')])
    r.render(m);final=r.finalize(m)
    assert abs(probe_duration(final.path)-final.duration_s)<.2


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='requires ffmpeg')
def test_bounded_uses_one_lossless_checkpoint_and_limits_input_threads(tmp_path):
    from unittest.mock import Mock

    cfg = MediaConfig(output_dir=tmp_path, render={'mode': 'bounded', 'threads': 1},
                      video={'provider': 'ffmpeg', 'resolution': '160x90', 'fps': 10, 'motion': 'none'})
    r = Renderer(InMemoryBus(), make_media(cfg))
    r.media.video._run = Mock(wraps=r.media.video._run)
    m = SceneManifest(video_id='DISK', scenes=[Scene(scene_id='S', order=0, narration='Hello world.', visual_prompt='p')])
    r.render(m)
    files = list((tmp_path / 'DISK' / '.segments').glob('*.mkv'))
    assert len(files) == 1
    streams = json.loads(subprocess.check_output(['ffprobe', '-v', 'quiet', '-show_streams', '-of', 'json', files[0]]))['streams']
    assert next(s for s in streams if s['codec_type'] == 'audio')['codec_name'] == 'flac'
    for call in r.media.video._run.call_args_list:
        args = call.args[0]
        for index, arg in enumerate(args):
            if arg == '-i':
                assert '-threads' in args[:index]
    # FLAC also compresses the fake silent narration; no duplicate PCM checkpoint.
    assert files[0].stat().st_size < 48000 * 2 * 2 * 0.5


@pytest.mark.parametrize('duration,pad,fps', [(float('nan'), 0, 10), (float('inf'), 0, 10),
                                             (-1, 0, 10), (0, 0, 10), (1, -1, 10), (1, 0, 0)])
def test_invalid_frame_spans_fail_with_actionable_media_error(duration, pad, fps):
    from studio.long_render import frame_span
    from studio.media import MediaError

    with pytest.raises(MediaError, match=r'duration|fps'):
        frame_span(duration, pad, fps)


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='requires ffmpeg')
def test_interrupted_concat_preserves_final_and_resumes_valid_checkpoints(tmp_path):
    from unittest.mock import Mock

    from studio.long_render import BoundedAssembler
    from studio.media import FakeImage, FakeTTS, MediaError

    image = tmp_path / 'visual.png'
    audio = tmp_path / 'voice.wav'
    FakeImage().generate('test', '160x90', image)
    FakeTTS().synthesize('Hello world', {}, audio)
    assembler = BoundedAssembler(threads=1, motion='none')
    out = tmp_path / 'final.mp4'
    out.write_bytes(b'previous final')
    real_run = assembler._run
    def fail_concat(args, **kwargs):
        if 'concat' in args:
            raise MediaError('interrupted concat')
        return real_run(args, **kwargs)
    assembler._run = Mock(side_effect=fail_concat)
    with pytest.raises(MediaError, match='interrupted concat'):
        assembler.assemble([(image, audio, 1)], out, 10, '160x90')
    assert out.read_bytes() == b'previous final'
    assert not list(tmp_path.glob('.concat-*'))
    blobs = list((tmp_path / '.segments').glob('*.mkv'))
    assert len(blobs) == 1
    assembler._run = Mock(wraps=real_run)
    assembler.assemble([(image, audio, 1)], out, 10, '160x90')
    assert assembler._run.call_count == 1  # only concat; scene survived the interruption
    blobs[0].write_bytes(b'broken checkpoint')
    assembler._run.reset_mock()
    assembler.assemble([(image, audio, 1)], out, 10, '160x90')
    assert assembler._run.call_count == 2  # corrupt scene is repaired, then concatenated


def test_bounded_disk_configuration_and_negative_limits(tmp_path):
    from studio.long_render import BoundedAssembler
    from studio.media import MediaError
    suite = make_media(MediaConfig(render={'mode': 'bounded', 'segment_cache_max_bytes': 123,
                                           'min_free_disk_bytes': 456}, video={'provider': 'ffmpeg'}))
    assert suite.video.segment_cache_max_bytes == 123
    assert suite.video.min_free_disk_bytes == 456
    for kwargs in ({'segment_cache_max_bytes': -1}, {'min_free_disk_bytes': -1}):
        with pytest.raises(MediaError, match='nonnegative'):
            BoundedAssembler(**kwargs)


def test_bounded_free_space_stops_before_encode_after_encode_and_before_concat(tmp_path, monkeypatch):
    from collections import namedtuple
    from pathlib import Path
    from unittest.mock import Mock

    from studio.long_render import BoundedAssembler
    from studio.media import MediaError
    usage = namedtuple('usage', 'total used free')
    visual = tmp_path / 'v.png'; visual.write_bytes(b'image')
    audio = tmp_path / 'a.wav'; audio.write_bytes(b'voice')
    monkeypatch.setattr('studio.long_render.probe_duration', lambda _: 1)
    for free_values, expected_calls in (([0], 0), ([1000, 0], 1), ([1000, 1000, 101], 1)):
        out = tmp_path / str(expected_calls) / str(free_values[-1]) / 'out.mp4'
        asm = BoundedAssembler(min_free_disk_bytes=100, segment_cache_max_bytes=1000)
        def run(args, **kwargs): Path(args[-1]).write_bytes(b'x' * 10)
        asm._run = Mock(side_effect=run)
        monkeypatch.setattr('studio.long_render.shutil.disk_usage', Mock(side_effect=[usage(2000, 0, n) for n in free_values]))
        with pytest.raises(MediaError, match='free disk'):
            asm.assemble([(visual, audio, 1)], out, 10, '160x90')
        assert asm._run.call_count == expected_calls
        assert not out.exists()


def test_bounded_retention_protects_future_scenes(tmp_path, monkeypatch):
    from pathlib import Path

    from studio.long_render import BoundedAssembler
    visual = tmp_path / 'v.png'; visual.write_bytes(b'image')
    audios = [tmp_path / f'a{i}.wav' for i in range(3)]
    for i, audio in enumerate(audios): audio.write_bytes(str(i).encode())
    monkeypatch.setattr('studio.long_render.probe_duration', lambda _: 1)
    encoded = []
    asm = BoundedAssembler(min_free_disk_bytes=0, motion='none')
    def run(args, **kwargs):
        if 'concat' not in args: encoded.append(args)
        Path(args[-1]).write_bytes(b'x' * 10)
    asm._run = run
    scenes = [(visual, a, 1) for a in audios]
    asm.assemble(scenes, tmp_path / 'out.mp4', 10, '160x90')
    asm.segment_cache_max_bytes = 20
    # Old first scene is inactive. Both later scenes must survive initial pruning.
    asm.assemble(scenes[1:], tmp_path / 'out.mp4', 10, '160x90')
    assert len(encoded) == 3
    assert len(list((tmp_path / '.segments').glob('*.mkv'))) == 2


def test_bounded_disk_failure_keeps_previous_final_and_active_checkpoints(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock

    from studio.long_render import BoundedAssembler
    from studio.media import MediaError

    visual = tmp_path / 'v.png'; visual.write_bytes(b'image')
    audio = tmp_path / 'a.wav'; audio.write_bytes(b'voice')
    out = tmp_path / 'out.mp4'; out.write_bytes(b'previous final')
    monkeypatch.setattr('studio.long_render.probe_duration', lambda _: 1)
    asm = BoundedAssembler(min_free_disk_bytes=100, segment_cache_max_bytes=5)
    def run(args, **kwargs): Path(args[-1]).write_bytes(b'x' * 10)
    asm._run = Mock(side_effect=run)
    with pytest.raises(MediaError, match=r'active.*budget'):
        asm.assemble([(visual, audio, 1)], out, 10, '160x90')
    assert asm._run.call_count == 1 and out.read_bytes() == b'previous final'
    assert len(list((tmp_path / '.segments').glob('*.mkv'))) == 1
    asm.segment_cache_max_bytes = 10
    asm._run.reset_mock()
    monkeypatch.setattr('studio.long_render.shutil.disk_usage', Mock(side_effect=[
        SimpleNamespace(free=100000), SimpleNamespace(free=0)]))
    with pytest.raises(MediaError, match='free disk'):
        asm.assemble([(visual, audio, 1)], out, 10, '160x90')
    assert asm._run.call_count == 1  # cached segment; only concat
    assert out.read_bytes() == b'previous final'
    assert not list(tmp_path.glob('.concat-*'))
