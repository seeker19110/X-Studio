"""Real Edge speech and FFmpeg: repair, captions, cache recovery, Shorts and SQLite restoration."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw
from xagents_core.sqlite_bus import Lease

from studio.backup import create_backup, restore_backup
from studio.editing import MediaLibrary
from studio.events import CutList, Scene, SceneManifest
from studio.inspection import inspect_project
from studio.media import MediaConfig, make_media
from studio.render_cache import digest
from studio.renderer import Renderer
from studio.resources import MediaAccounting
from studio.shorts import ShortsRequest, render_short
from studio.sqlite_bus import SQLiteBus
from studio.storage import IdeaStore
from studio.website_render import speech


def main() -> None:
    root = (Path("output/real-validation") / datetime.now().strftime("media-%Y%m%d-%H%M%S")).resolve()
    root.mkdir(parents=True)
    cfg = MediaConfig(
        output_dir=root / "media",
        upload_dir=root / "uploads",
        tts={"provider": "edge", "voice": "vi-VN-HoaiMyNeural"},
        video={
            "provider": "ffmpeg",
            "fps": 25,
            "resolution": "640x360",
            "burn_captions": True,
            "motion": "none",
            "transition_s": 0,
        },
        render={"mode": "bounded", "threads": 2},
        production={"scenes": {"LIVE": {"S1": "first", "S2": "second"}}},
    )
    ideas = IdeaStore(root / "studio.sqlite")
    ideas.import_items([{"id": "live-idea", "title": "Ý tưởng kiểm tra dữ liệu thực tế"}])
    lease = Lease(root / "studio.sqlite")
    lease.acquire()
    bus = SQLiteBus(root / "studio.sqlite")
    try:
        library = MediaLibrary(cfg)
        library.root.mkdir(parents=True)
        for index, id in enumerate(("first", "second")):
            path = library.root / (id + ".png")
            image = Image.new("RGB", (640, 360), ("#155866", "#55438a")[index])
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((90, 80, 550, 280), radius=24, fill=("#42ddbd", "#f6bd63")[index])
            image.save(path)
            library.save(
                index,
                {
                    "id": id,
                    "kind": "image",
                    "path": path.name,
                    "license": "owned",
                    "attribution": "X-Studio local validation fixture",
                },
            )
        manifest = SceneManifest(
            video_id="LIVE",
            voice={"voice_id": "vi-VN-HoaiMyNeural", "language": "vi"},
            scenes=[
                Scene(
                    scene_id="S1",
                    order=0,
                    narration="Trước khi sửa video, hãy kiểm tra môi trường và tạo một bản sao lưu trong thư mục mới.",
                    visual_prompt="Mẫu đồ họa kiểm thử thứ nhất",
                ),
                Scene(
                    scene_id="S2",
                    order=1,
                    narration="Sau khi hoàn thành, xem lại hình ảnh, nghe lời đọc và kiểm tra phụ đề trước khi duyệt xuất bản.",
                    visual_prompt="Mẫu đồ họa kiểm thử thứ hai",
                ),
            ],
        )
        renderer = Renderer(bus, make_media(cfg))
        renderer.operation_id = "live-render"
        renderer.render(manifest)
        renderer.operation_id = "live-final-1"
        first = renderer.finalize(manifest)
        preserved = {k: digest(Path(v)) for k, v in manifest.scenes[1].asset_refs.items()}
        manifest.scenes[1].locked = True
        cut = CutList(
            video_id="LIVE",
            manifest_version=1,
            decision="repair",
            repairs=[
                {
                    "scene_id": "S1",
                    "action": "regenerate_audio",
                    "reason": "Kiểm tra sửa cảnh thực tế",
                    "new_narration": "Bản sửa thực tế đã hoàn thành. Hãy giữ bản sao lưu, kiểm tra lời đọc mới và đối chiếu phụ đề tiếng Việt.",
                }
            ],
        )
        renderer.operation_id = "live-repair"
        updated = renderer.apply_cutlist(manifest, cut)
        assert all(digest(Path(updated.scenes[1].asset_refs[k])) == sha for k, sha in preserved.items())
        renderer.operation_id = "live-final-2"
        final = renderer.finalize(updated)
        ledger = MediaAccounting(bus, cfg)
        before = ledger.summary()["projects"]["LIVE"]["generations"]
        renderer.finalize(updated)
        assert ledger.summary()["projects"]["LIVE"]["generations"] == before
        inspection = inspect_project(updated, final, cfg)
        assert not inspection["findings"]
        short = render_short(
            Path(final.path),
            Path(final.path).with_name("captions_v2.srt"),
            cfg.output_dir / "SHORT",
            ShortsRequest(
                video_id="LIVE",
                manifest_version=2,
                source_checksum=final.checksum,
                title="Đoạn dọc thử thật",
                start_s=0,
                end_s=5,
                request_id="real-short",
            ),
            cfg,
        )
        source = root / "website-speech"
        source.mkdir()
        text = "Đây là kiểm tra bộ nhớ đệm giọng đọc. Nếu tệp bị hỏng, xưởng phải tạo lại âm thanh và mốc phụ đề."
        audio, duration, words = speech(text, "vi-VN-HoaiMyNeural", source, ledger, "WEB-LIVE")
        count = ledger.summary()["projects"]["WEB-LIVE"]["generations"]
        speech(text, "vi-VN-HoaiMyNeural", source, ledger, "WEB-LIVE")
        assert ledger.summary()["projects"]["WEB-LIVE"]["generations"] == count
        audio.write_bytes(b"corrupted audio")
        speech(text, "vi-VN-HoaiMyNeural", source, ledger, "WEB-LIVE")
        assert ledger.summary()["projects"]["WEB-LIVE"]["generations"] == count + 1
        backup = create_backup(root / "studio.sqlite", cfg, root / "backup")
        restored = restore_backup(root / "backup", root / "restored")
        assert IdeaStore(Path(restored["db"])).snapshot()["ideas"][0]["id"] == "live-idea"
        from studio.factory import Factory
        from studio.media import load_media_config

        os.environ["STUDIO_POLICY_KEY_FILE"] = str(root / "restored-policy.key")
        reopened = Factory(Path(restored["db"]), load_media_config(root / "restored" / "media.restore.yaml"), demo=True)
        try:
            assert not reopened.running
            assert all(reopened.media_path(e.event_id).is_file() for e in reopened.bus.replay("media-assets"))
        finally:
            reopened.close()
        result = {
            "root": str(root),
            "initial_final": first.path,
            "edited_final": final.path,
            "scene_2_preserved": True,
            "warm_generation_unchanged": True,
            "native_word_timing": all(s.asset_refs.get("scene_timing") for s in updated.scenes),
            "inspection": inspection,
            "short": short,
            "website_speech": {"duration_s": duration, "word_count": len(words), "corruption_recovered": True},
            "backup": backup,
            "restore": restored,
            "ledger": ledger.summary(),
        }
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        Path("output/real-validation/live-media-path.txt").write_text(str(root), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        bus.close()
        lease.release()


if __name__ == "__main__":
    main()
