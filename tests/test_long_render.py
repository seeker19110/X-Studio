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
    cache = list((tmp_path / "LONG" / ".segments").glob("scene-*.mkv"))
    assert len(cache) == 3
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
