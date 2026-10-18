"""Use the real console to edit real media, derive Shorts and restore the resulting factory."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from studio.backup import restore_backup
from studio.console import ConsoleServer
from studio.factory import Factory
from studio.media import load_media_config
from studio.render_cache import digest


def until(predicate, timeout=600):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.25)
    raise TimeoutError("Real console stage did not finish; inspect console-state.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--source-video-id", help="Select the existing source project explicitly")
    parser.add_argument("--keep-edited-parent", action="store_true", help="Verify a completed scene edit and continue with a fresh Shorts request")
    parser.add_argument(
        "--rights-dossier", type=Path, help="Have the real rights owner ingest this project dossier for each new final"
    )
    parser.add_argument(
        "--whole-source-short",
        action="store_true",
        help="Keep the complete source narration and CTA in the portrait review fixture",
    )
    parser.add_argument(
        "--create-scripted-fixture",
        action="store_true",
        help="Create isolated deterministic text/review fixtures; Edge, image CLI and FFmpeg remain real",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.create_scripted_fixture:
        root.mkdir(parents=True, exist_ok=False)
    os.environ["STUDIO_POLICY_KEY_FILE"] = str(root / "policy.key")
    cfg = copy.deepcopy(load_media_config())
    cfg.output_dir = root / "media"
    cfg.upload_dir = root / "uploads"
    cfg.tts = {"provider": "edge", "voice": "vi-VN-HoaiMyNeural"}
    cfg.image["timeout_s"] = 180
    cfg.video.update(resolution="640x360", fps=25, motion="none", transition_s=0, burn_captions=True)
    cfg.render.update(threads=2, mode="bounded")
    cfg.production.update(automation="production", max_minutes=10 if args.create_scripted_fixture else 1)
    factory = Factory(root / "studio.sqlite", cfg, demo=args.create_scripted_fixture)
    for backend in getattr(factory.orch.runner.client, "backends", []):
        if hasattr(backend.client, "timeout"):
            backend.client.timeout = 120.0
    if args.create_scripted_fixture:
        factory.command("create", {"title": "Fixture kiểm thử media qua giao diện", "audience": "Người kiểm thử"})
        started = time.monotonic()
        while not any(g["kind"] == "publish" for g in factory.snapshot()["gates"]):
            if time.monotonic() - started > 600:
                factory.close()
                raise TimeoutError("Initial real-media fixture did not reach its publish gate")
            factory.cycle()
        print("Real media ready; text/review models are deterministic test fixtures", flush=True)
    server = ConsoleServer(("127.0.0.1", 0), factory, "real-console-test")
    http = threading.Thread(target=server.serve_forever, daemon=True)
    http.start()
    work_errors = []

    def work():
        if not args.rights_dossier:
            factory.work()
            return
        spec = importlib.util.spec_from_file_location("live_smoke", Path(__file__).with_name("live-upgrades-smoke.py"))
        intake = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(intake)
        while not factory.stop.is_set():
            try:
                with factory._work_lock:
                    for final in list(factory.bus.replay("media-assets")):
                        if (
                            final.payload["kind"] == "final_video"
                            and final.payload["video_id"] not in factory.orch.paused
                            and not (root / ("rights-intake-" + final.event_id + ".json")).exists()
                        ):
                            intake.prepare_rights(factory, root, args.rights_dossier, final)
                    factory.cycle()
            except Exception as error:
                work_errors.append(f"{type(error).__name__}: {error}")
                factory.stop.set()
            factory.stop.wait(0.25)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True, executable_path=os.environ.get("STUDIO_WEBSITE_CHROMIUM"))
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/#projects")
            page.locator("#login-form [name=token]").fill("real-console-test")
            page.locator("#login-form button").click()
            page.locator("#connection").filter(has_text="Đã kết nối").wait_for()
            source = next(p for p in factory.snapshot()["projects"] if p["id"] == args.source_video_id) if args.source_video_id else factory.snapshot()["projects"][0]
            vid = source["id"]
            version = source["manifest_version"]
            before = factory.orch.manifest(vid)
            target = next(s for s in reversed(before.scenes) if not s.locked)
            others = {
                s.scene_id: {k: digest(Path(v)) for k, v in s.asset_refs.items()}
                for s in before.scenes
                if s.scene_id != target.scene_id
            }
            page.locator('#projects-view [data-project="' + vid + '"]').first.click()

            def edited():
                if work_errors:
                    raise RuntimeError(work_errors[-1])
                state = factory.snapshot()
                (root / "console-state.json").write_text(
                    json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                p = next(p for p in state["projects"] if p["id"] == vid)
                return (
                    p
                    if p["manifest_version"] > version
                    and any(
                        a["kind"] == "final_video" and a["manifest_version"] == p["manifest_version"]
                        for a in p["media"]
                    )
                    and any(g["id"] == "PUB-" + vid for g in state["gates"])
                    else None
                )

            if args.keep_edited_parent:
                assert version > 1 and target.narration.startswith("Tiếp theo, ")
                inspection = json.loads((root / "media" / vid / "review_v1.json").read_text(encoding="utf-8"))
                others = {}
                for ref in inspection["references"]:
                    if ref["scene_id"] != target.scene_id:
                        others.setdefault(ref["scene_id"], {})[ref["kind"]] = ref["sha256"]
                assert others
                updated = source
            else:
                page.locator("[data-detail-tab=scenes]").click()
                page.locator('[data-edit-scene="' + target.scene_id + '"]').click()
                page.locator("#scene-form [name=narration]").fill("Tiếp theo, " + target.narration)
                page.locator("#scene-form [type=submit]").click()
                page.locator("#scene-dialog").wait_for(state="hidden")
                print("Real scene edit submitted", flush=True)
                updated = until(edited)
            assert all(
                digest(Path(s.asset_refs[k])) == sha
                for s in factory.orch.manifest(vid).scenes
                if s.scene_id in others
                for k, sha in others[s.scene_id].items()
            )
            page.wait_for_function(
                '(v) => detailProject?.manifest_version === v && latest(detailProject,"final_video")?.manifest_version===v',
                arg=updated["manifest_version"],
            )
            existing_projects = {p["id"] for p in factory.snapshot()["projects"]}
            page.locator("[data-create-shorts]").click()
            final_asset = next(
                a
                for a in reversed(updated["media"])
                if a["kind"] == "final_video" and a["manifest_version"] == updated["manifest_version"]
            )
            short_title = (source["title"] + " · bản dọc") if args.whole_source_short else "Shorts từ lượt thử thật"
            end_s = str(int(final_asset["duration_s"] * 10) / 10) if args.whole_source_short else "5"
            page.locator("#shorts-form [name=title]").fill(short_title)
            page.locator("#shorts-form [name=end_s]").fill(end_s)
            page.locator("#shorts-form [type=submit]").click()
            page.locator("#shorts-dialog").wait_for(state="hidden")
            print("Real Shorts submitted", flush=True)

            def shorts_ready():
                if work_errors:
                    raise RuntimeError(work_errors[-1])
                state = factory.snapshot()
                (root / "console-state.json").write_text(
                    json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                created = {p["id"] for p in state["projects"]} - existing_projects
                return state if len(created) == 1 and all(any(g["id"] == "PUB-" + p for g in state["gates"]) for p in created | {vid}) else None

            final = until(shorts_ready)
            page.locator("#detail-dialog [data-close]").first.click()
            page.locator(".nav[data-view=settings]").click()
            page.locator("#resources-run").click()
            page.locator("#resources-result").filter(has_text="USD").wait_for()
            bundle = root / "console-backup"
            page.locator("#backup-form input").fill(str(bundle))
            page.locator("#backup-form [type=submit]").click()
            page.locator("#backup-result").filter(has_text=str(bundle)).wait_for(timeout=30000)
            page.screenshot(path=str(root / "console-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(root / "console-mobile.png"), full_page=True)
            assert not errors, errors
            browser.close()
        restore = restore_backup(bundle, root / "console-restored")
        reopened = Factory(
            Path(restore["db"]), load_media_config(root / "console-restored" / "media.restore.yaml"), demo=True
        )
        try:
            assert not reopened.running and len(reopened.snapshot()["projects"]) == len(final["projects"])
            assert all(reopened.media_path(e.event_id).is_file() for e in reopened.bus.replay("media-assets"))
        finally:
            reopened.close()
        (root / "console-result.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "model_mode": "scripted_fixture" if args.create_scripted_fixture else "live_cli",
                    "media_mode": "live_edge_image_cli_ffmpeg",
                    "source_video_id": vid,
                    "edited_version": updated["manifest_version"],
                    "edited_scene_id": target.scene_id,
                    "other_scenes_preserved": True,
                    "short_video_id": next(p["id"] for p in final["projects"] if p["id"] not in existing_projects),
                    "historical_projects": sorted(existing_projects - {vid}),
                    "gates_left_pending": [g["id"] for g in final["gates"]],
                    "restore": restore,
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("REAL CONSOLE PASSED: edit, fresh gates, Shorts, resources, backup/restore, mobile", flush=True)
    finally:
        factory.stop.set()
        worker.join()
        server.shutdown()
        http.join()
        server.server_close()
        factory.close()


if __name__ == "__main__":
    main()
