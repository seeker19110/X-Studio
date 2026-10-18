"""Dashboard-only synthetic fixtures: no model, provider, FFmpeg or platform request."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from studio.events import Envelope, MediaAsset, Provenance, Scene, SceneManifest, VideoBrief
from studio.factory import Factory
from studio.media import MediaConfig


def measure(count: int, memory: bool = False) -> dict:
    if memory: tracemalloc.start()
    with tempfile.TemporaryDirectory(prefix='xstudio-benchmark-') as temporary:
        root = Path(temporary); os.environ['STUDIO_POLICY_KEY_FILE'] = str(root / 'policy.key')
        factory = Factory(root / 'studio.sqlite',MediaConfig(output_dir=root / 'out'),demo=True)
        try:
            for index in range(count):
                vid = f'V{index:06d}'
                brief = VideoBrief(video_id=vid,channel_id='C1',working_title=f'Dự án {index}',pillar='Mẫu',angle='Mẫu',audience='Mẫu')
                factory.bus.publish(Envelope(topic='video-briefs',key=vid,actor='desk',payload=brief.model_dump()))
                scenes = [Scene(scene_id=f'S{number}',order=number,narration='Lời đọc tiếng Việt để đo kích thước dữ liệu. '*16,
                                visual_prompt='Khung hình minh họa cho fixture hiệu năng.') for number in range(10)]
                factory.bus.publish(Envelope(topic='scene-manifests',key=vid,actor='renderer',payload=SceneManifest(video_id=vid,scenes=scenes).model_dump()))
                for scene in scenes:
                    asset = MediaAsset(video_id=vid,kind='scene_image',scene_id=scene.scene_id,path=str(root / vid / (scene.scene_id+'.png')),
                                       provider='fake',provenance=Provenance(generated_by='benchmark-fixture'))
                    factory.bus.publish(Envelope(topic='media-assets',key=vid,actor='renderer',payload=asset.model_dump()))
            samples = []
            for _ in range(3):
                started = time.perf_counter(); factory.refresh(); samples.append(time.perf_counter()-started)
            result = {'projects':count,'events':len(list(factory.bus.replay())), 'refresh_seconds_median':statistics.median(samples),
                      'full_state_bytes':len(json.dumps(factory.snapshot(),ensure_ascii=False).encode())}
            if hasattr(factory,'project_detail'):
                result['overview_bytes'] = len(json.dumps(factory.snapshot(lean=True),ensure_ascii=False).encode())
            result['python_peak_bytes'] = tracemalloc.get_traced_memory()[1] if memory else None
            try:
                import resource
                rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                result['process_peak_rss_bytes'] = rss if sys.platform=='darwin' else rss*1024
            except ImportError: result['process_peak_rss_bytes'] = None
            return result
        finally:
            factory.close()
            if memory: tracemalloc.stop()


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument('--sizes',nargs='+',type=int,default=[10,100,1000])
    parser.add_argument('--memory',action='store_true'); parser.add_argument('--output',type=Path,required=True); args = parser.parse_args()
    results = []
    for count in args.sizes:
        row = measure(count,args.memory); results.append(row); print(json.dumps(row),flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'fixture':'10 scenes per project; no render','results':results},indent=2),encoding='utf-8')


if __name__ == '__main__': main()
