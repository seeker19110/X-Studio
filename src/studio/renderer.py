"""Renderer: CODE biến scene manifest thành asset thật qua lớp media (ADR-0004). Không có model nào ở đây.

- `render(manifest)`: mỗi cảnh chưa `locked` (hoặc nằm trong danh sách sửa) → TTS + ảnh; cảnh đã có asset và locked
  thì dùng lại. Ghép bản nháp `draft_video`. Mọi asset có checksum + provenance (provider:model, prompt_ref) để
  rights-checker kiểm và gate publish thấy nguồn gốc.
- `apply_cutlist(manifest, cut)`: sửa đúng những cảnh editor yêu cầu (regenerate/replace/lock), tăng `version` manifest,
  render lại chỉ phần đó → bản nháp mới. Không làm lại cả video.
- `finalize(manifest, order)`: ghép `final_video` theo thứ tự chốt.
- `thumbnails(spec)`: mỗi biến thể A/B một ảnh NỀN (không chữ — model ảnh viết sai dấu tiếng Việt), rồi CODE phủ chữ,
  thu về 1280x720 và nén xuống ≤ 2 MB cho đúng giới hạn nền tảng.
Khung hình và kích thước ảnh bám theo `aspect` của manifest (`media.frame_size`, `media.image_size`): short 9:16 ra
1080x1920, và model ảnh chỉ nhận kích thước hợp lệ của chính nó.
Kết quả publish lên `media-assets` dưới actor `renderer`, kèm audit.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .bus import InMemoryBus
from .events import AssetKind, AuditLog, CutList, Envelope, MediaAsset, Provenance, Scene, SceneManifest, ThumbnailSpec
from .media import FakeVideo, FFmpegAssembler, MediaError, MediaResult, MediaSuite, frame_size, image_size, make_media
from .media_cost import Ledger, without_prices
from .motion import LAYOUT_VERSION, MotionSpec, render_motion, safe_bottom
from .production import copy_asset, finish, selections
from .render_cache import RenderCache, digest, fingerprint
from .timeline import caption_pages, chapter_time, karaoke_ass, srt, timeline
from .video_gen import validate_video_inputs

ACTOR = "renderer"
# Chữ nằm trong ảnh sinh là lỗi (skill visual-direction): model ảnh viết sai dấu tiếng Việt, và chữ phải do code phủ lên.
NO_TEXT = "Không chữ, không watermark, không logo trong ảnh."


def checksum(p: Path) -> str:
    return digest(p)[:16] if p.exists() else ""


class Renderer:
    def __init__(self, bus: InMemoryBus, media: MediaSuite | None = None, out_dir: Path | None = None,
                 upload_dir: Path | None = None):
        self.bus = bus
        self.operation_id: str | None = None
        self.media = media or make_media()
        self.out_dir = Path(out_dir) if out_dir else self.media.cfg.output_dir
        # `replacement_path` trong cut-list do model viết → chỉ chấp nhận file nằm TRONG thư mục upload này
        self.upload_dir = Path(upload_dir or self.media.cfg.upload_dir or self.out_dir / "uploads")
        self.cache = RenderCache(self.out_dir / ".cache" / "media")
        # Sổ chi phí media (ADR-0016): chỉ lượt cache trượt — provider thật được gọi — mới bị tính và soát trần.
        self.ledger = Ledger(bus, self.media.cfg, self._audit)

    # ---------- helpers ----------

    def _dir(self, video_id: str) -> Path:
        d = self.out_dir / video_id; d.mkdir(parents=True, exist_ok=True); return d

    def _asset(self, video_id: str, kind: AssetKind, r: MediaResult, manifest_version: int, scene_id: str | None = None,
               prompt_ref: str | None = None, variant_id: str | None = None, license: str = "generated") -> MediaAsset:
        return MediaAsset(video_id=video_id, kind=kind, path=str(r.path), scene_id=scene_id, manifest_version=manifest_version,
                          provider=r.provider, checksum=checksum(r.path), duration_s=r.duration_s, variant_id=variant_id,
                          provenance=Provenance(generated_by=f"{r.provider}:{r.model}", prompt_ref=prompt_ref, license=license))

    def safe_upload(self, raw: str) -> Path | None:
        """Đường dẫn thay thế hợp lệ khi resolve xong vẫn nằm trong upload_dir và là file; ngược lại None (bị từ chối)."""
        base = self.upload_dir.resolve()
        p = Path(raw)
        p = (base / p if not p.is_absolute() else p).resolve()
        return p if p.is_relative_to(base) and p.is_file() else None

    def _video_opts(self, aspect: str) -> tuple[int, str]:
        v = self.media.cfg.video
        return int(v.get("fps", 30)), frame_size(v, aspect)

    def _audit(self, action: str, video_id: str, data: dict[str, Any]) -> None:
        a = AuditLog(actor=ACTOR, action=action, video_id=video_id, evidence=json.dumps(data, ensure_ascii=False))
        self.bus.publish(Envelope(topic="audit-log", key=ACTOR, actor=ACTOR, payload=a.model_dump()))

    def _deliver(self, events: list[Envelope]) -> None:
        # One orchestrator owns a database. Reuse prepared IDs after interruption,
        # including the window between SQLite commit and code-stage checkpoint.
        seen = {e.event_id for topic in {e.topic for e in events} for e in self.bus.replay(topic)}
        for env in events:
            if env.event_id not in seen:
                self.bus.publish(env)

    def _resume_output(self) -> list[Envelope] | None:
        if self.operation_id is None: return None
        for env in reversed(list(self.bus.replay("audit-log", ACTOR))):
            if env.payload["action"] != "render.prepared": continue
            data = json.loads(env.payload["evidence"])
            if data["operation_id"] == self.operation_id:
                events = [Envelope.model_validate(e) for e in data["events"]]
                for output in events:
                    if output.topic == "media-assets":
                        asset = MediaAsset.model_validate(output.payload)
                        path = Path(asset.path)
                        if not path.is_file() or checksum(path) != asset.checksum:
                            raise MediaError(f"prepared output damaged: {path}; restore the exact checkpoint file")
                self._deliver(events)
                return events
        return None

    def _publish(self, assets: list[MediaAsset], action: str,
                 manifest: SceneManifest | None = None) -> list[Envelope]:
        out = [Envelope(topic="media-assets", key=a.video_id, actor=ACTOR, payload=a.model_dump(),
                        causation_id=self.operation_id) for a in assets]
        events = list(out)
        if manifest is not None:
            events.insert(0, Envelope(topic="scene-manifests", key=manifest.video_id, actor=ACTOR,
                                      payload=manifest.model_dump(), causation_id=self.operation_id))
        if assets:
            a = AuditLog(actor=ACTOR, action=action, video_id=assets[0].video_id,
                         evidence=json.dumps({"assets": [(x.kind, x.scene_id or x.variant_id) for x in assets],
                                              "providers": self.media.names,
                                              "sandbox": self.media.sandbox.name}, ensure_ascii=False))
            events.append(Envelope(topic="audit-log", key=ACTOR, actor=ACTOR, payload=a.model_dump(),
                                   causation_id=self.operation_id))
        if self.operation_id is not None:
            # Persist the entire outbox before its first visible output. This is a
            # local bus recovery guarantee, not exactly-once remote provider work.
            self._audit("render.prepared", assets[0].video_id if assets else "", {
                "operation_id": self.operation_id, "events": [e.model_dump(mode="json") for e in events]})
        self._deliver(events)
        return out

    # ---------- render ----------

    def _scene_order(self, m: SceneManifest, order: list[str] | None = None) -> list[str]:
        ids = [s.scene_id for s in m.scenes]
        if not ids or len(ids) != len(set(ids)):
            raise MediaError("manifest requires nonempty scenes with unique scene IDs")
        if order and (len(order) != len(ids) or set(order) != set(ids)):
            raise MediaError("scene order must contain every scene exactly once")
        return order or [s.scene_id for s in sorted(m.scenes, key=lambda s: s.order)]

    def render_scene(self, m: SceneManifest, s: Scene, audio: bool = True, image: bool = True,
                     force: bool = False, revision: str = "") -> list[MediaAsset]:
        self._scene_order(m)
        d = self._dir(m.video_id) / f"v{m.version}"; out: list[MediaAsset] = []
        hits: dict[str, bool] = {}
        local, _ = selections(self.media.cfg, self.upload_dir, m)
        # ADR-0017: người vận hành (`production.graphics`) ghi đè graphic production-manager chọn cho cảnh.
        graphic = self.media.cfg.production.get("graphics", {}).get(m.video_id, {}).get(s.scene_id)
        motion = MotionSpec.model_validate(graphic) if graphic else s.graphic
        if motion and not graphic and image:
            # Graphic của model là phần tăng cường: asset thư viện người vận hành chọn cho cảnh thắng nó, và thiếu
            # FFmpeg thì cảnh dùng ảnh như cũ — không làm hỏng video.
            reason = ("người vận hành đã chọn asset thư viện" if s.scene_id in local
                      else "" if isinstance(self.media.video, FFmpegAssembler) else "video.provider không phải ffmpeg")
            if reason:
                self._audit("render.graphic_skipped", m.video_id,
                            {"scene_id": s.scene_id, "template": motion.template, "reason": reason})
                motion = None
        generated = image and not motion and s.scene_id not in local and m.cinema.visual_mode == "generated_video"
        if generated:
            if self.media.scene_video is None:
                raise MediaError("generated_video requires a configured scene_video.provider")
            config = self.media.cfg.scene_video
            prompt = m.cinema.prompt(s.visual_prompt, str(self.media.cfg.production.get("style", ""))) + " " + NO_TEXT
            seed = m.cinema.seed_for(s.scene_id, revision)
            negative = m.cinema.negative_prompt
            values = validate_video_inputs(prompt, negative, config.get("duration_s", 5), config.get("fps", 24),
                                           config.get("resolution", "832x480"), seed, config)
            clip_duration = values["duration_s"]
            fps = values["fps"]
            resolution = frame_size({"resolution": values["resolution"]}, m.aspect)
        if audio:
            key: dict[str, Any] = {"kind": "tts", "config": without_prices(self.media.cfg.tts), "voice": m.voice, "text": s.narration,
                   "adapter": type(self.media.tts).__qualname__, "revision": revision}
            r, hits["audio"] = self.cache.produce(key, d / f"{s.scene_id}.wav",
                lambda p: self.ledger.charge(m.video_id, "tts", len(s.narration),
                                             lambda: self.media.tts.synthesize(s.narration, m.voice, p),
                                             scene_id=s.scene_id), force=force)
            s.asset_refs["scene_audio"] = str(r.path); s.duration_s = r.duration_s or s.duration_s
            # Mốc từng từ của provider (nếu có) nằm cạnh audio cảnh để finalize dựng phụ đề theo lời đọc thật;
            # provider/giọng đổi mà không còn mốc thì xoá bản cũ, không để phụ đề bám nhầm audio trước.
            sidecar = d / f"{s.scene_id}.words.json"
            if r.words:
                sidecar.write_text(json.dumps([list(w) for w in r.words], ensure_ascii=False), encoding="utf-8")
            else:
                sidecar.unlink(missing_ok=True)
            out.append(self._asset(m.video_id, "scene_audio", r, m.version, s.scene_id, prompt_ref=f"{s.scene_id}:narration"))
        if image:
            if motion:
                if not isinstance(self.media.video, FFmpegAssembler):
                    raise MediaError("motion graphics require video.provider=ffmpeg")
                fps, resolution = self._video_opts(m.aspect)
                duration = s.duration_s + self.media.video.tail_pad_s
                w, h = map(int, resolution.split("x"))
                bottom = safe_bottom(w, h, bool(self.media.cfg.video.get("burn_captions")))
                key = {"motion": motion.model_dump(), "duration": duration, "fps": fps, "resolution": resolution,
                       "revision": revision, "layout": LAYOUT_VERSION, "bottom": bottom}
                engine, spec = self.media.video, motion
                r, hits["motion"] = self.cache.produce(key, d / f"{s.scene_id}_motion.mp4",
                    lambda p: render_motion(spec, p, engine, duration, fps, resolution, bottom=bottom), force=force)
                s.asset_refs.pop("scene_image", None); s.asset_refs["scene_video"] = str(r.path)
                out.append(self._asset(m.video_id, "scene_video", r, m.version, s.scene_id, prompt_ref=f"motion:{s.scene_id}"))
            elif s.scene_id in local:
                source = local[s.scene_id]
                path = copy_asset(source, d / f"{s.scene_id}_visual")
                kind: AssetKind = "scene_video" if source.kind == "video" else "scene_image"
                r = MediaResult(path, "local", "licensed-library")
                asset = self._asset(m.video_id, kind, r, m.version, s.scene_id)
                asset.provenance = source.provenance()
                s.asset_refs.pop("scene_image", None); s.asset_refs.pop("scene_video", None)
                s.asset_refs[kind] = str(path)
                out.append(asset)
            elif generated:
                generator = self.media.scene_video
                assert generator is not None
                # Generate a short shot. The assembler loops footage to fit the narrator.
                duration = min(clip_duration, s.duration_s + getattr(self.media.video, "tail_pad_s", 0.0))
                identity = getattr(generator, "cache_identity", without_prices(config))
                key = {"kind": "scene_video", "config": identity, "prompt": prompt, "negative_prompt": negative,
                       "duration": duration, "fps": fps, "resolution": resolution, "seed": seed,
                       "adapter": type(generator).__qualname__, "revision": revision}
                r, hits["video"] = self.cache.produce(key, d / f"{s.scene_id}.mp4",
                    lambda p: self.ledger.charge(m.video_id, "scene_video", duration,
                        lambda: generator.generate(prompt, negative_prompt=negative, duration_s=duration,
                                                   fps=fps, resolution=resolution, seed=seed, out=p),
                        scene_id=s.scene_id), force=force)
                s.asset_refs.pop("scene_image", None); s.asset_refs["scene_video"] = str(r.path)
                out.append(self._asset(m.video_id, "scene_video", r, m.version, s.scene_id,
                            prompt_ref=f"{s.scene_id}:visual_prompt:seed={seed}", license=str(config.get("license", "unknown"))))
            else:
                style = str(self.media.cfg.production.get("style", "")).strip()
                prompt, size = f"{m.cinema.prompt(s.visual_prompt, style)} {NO_TEXT}".strip(), image_size(self.media.cfg, m.aspect)
                key = {"kind": "image", "config": without_prices(self.media.cfg.image), "prompt": prompt, "size": size,
                       "adapter": type(self.media.image).__qualname__, "revision": revision}
                r, hits["image"] = self.cache.produce(key, d / f"{s.scene_id}.png",
                    lambda p: self.ledger.charge(m.video_id, "image", 1, lambda: self.media.image.generate(prompt, size, p),
                                                 scene_id=s.scene_id), force=force)
                s.asset_refs.pop("scene_video", None)
                s.asset_refs["scene_image"] = str(r.path)
                out.append(self._asset(m.video_id, "scene_image", r, m.version, s.scene_id, prompt_ref=f"{s.scene_id}:visual_prompt"))
        self._audit("render.cache", m.video_id, {"scene_id": s.scene_id, "hits": hits})
        return out

    def _check_locked(self, m: SceneManifest, s: Scene, pending: list[MediaAsset] | None = None) -> None:
        for kind in ("scene_audio", "scene_video" if "scene_video" in s.asset_refs else "scene_image"):
            path = Path(s.asset_refs.get(kind, ""))
            if not path.is_file() or not path.stat().st_size:
                raise MediaError(f"locked scene {s.scene_id}: missing {kind}; restore the approved file or unlock explicitly")
            records = [e.payload for e in self.bus.replay("media-assets", m.video_id)]
            records += [a.model_dump() for a in pending or []]
            previous = next((a for a in reversed(records) if a["path"] == str(path) and a.get("checksum")
                             and a.get("scene_id") == s.scene_id and a["kind"] == kind), None)
            if previous is None:
                raise MediaError(f"locked scene {s.scene_id}: missing registered provenance for {kind}")
            if previous["checksum"] != checksum(path):
                raise MediaError(f"locked scene {s.scene_id}: checksum mismatch for {kind}")

    def _segments(self, m: SceneManifest, order: list[str] | None = None,
                  pending: list[MediaAsset] | None = None) -> list[tuple[Path, Path, float]]:
        by_id = {s.scene_id: s for s in m.scenes}
        ids = self._scene_order(m, order)
        segs = []
        for sid in ids:
            s = by_id[sid]
            visual = s.asset_refs.get("scene_video") or s.asset_refs.get("scene_image")
            if not visual or "scene_audio" not in s.asset_refs:
                raise ValueError(f"cảnh {sid} chưa có đủ asset để ghép")
            if s.locked: self._check_locked(m, s, pending)
            segs.append((Path(visual), Path(s.asset_refs["scene_audio"]), s.duration_s))
        return segs

    def render(self, m: SceneManifest, only: set[str] | None = None) -> list[MediaAsset]:
        """Render các cảnh cần (chưa locked hoặc trong `only`) rồi ghép bản nháp. Trả về asset đã publish."""
        prior = self._resume_output()
        if prior is not None:
            return [MediaAsset.model_validate(e.payload) for e in prior if e.topic == "media-assets"]
        assets: list[MediaAsset] = []
        self._scene_order(m)
        selections(self.media.cfg, self.upload_dir, m)
        for s in m.scenes:
            if s.locked:
                self._check_locked(m, s)
                continue
            need = (only is not None and s.scene_id in only) or (only is None and not (s.locked and len(s.asset_refs) >= 2))
            if need:
                assets += self.render_scene(m, s)
        fps, res = self._video_opts(m.aspect)
        r = self.media.video.assemble(self._segments(m), self._dir(m.video_id) / f"draft_v{m.version}.mp4", fps, res)
        assets.append(self._asset(m.video_id, "draft_video", r, m.version, prompt_ref=f"manifest:v{m.version}"))
        # manifest cùng version nhưng đã có asset_refs/duration thật → publish lại để bus là nguồn sự thật (resume, editor, finalize)
        self._publish(assets, "render.draft", m)
        return assets

    def apply_cutlist(self, m: SceneManifest, cut: CutList) -> SceneManifest:
        """Sửa manifest theo cut-list: đổi prompt/narration, thay asset người tải lên, khoá cảnh. Trả về manifest mới
        (version + 1) đã render lại đúng các cảnh bị chạm."""
        prior = self._resume_output()
        if prior is not None:
            return SceneManifest.model_validate(next(e.payload for e in prior if e.topic == "scene-manifests"))
        m = m.model_copy(deep=True)
        self._scene_order(m, cut.order)
        if cut.video_id != m.video_id or cut.manifest_version != m.version:
            raise MediaError("cut-list must reference the current video and manifest version")
        by_id = {s.scene_id: s for s in m.scenes}
        replacements: list[MediaAsset] = []
        touched: set[str] = set(); regen: dict[str, tuple[bool, bool]] = {}
        for r in cut.repairs:
            s = by_id.get(r.scene_id)
            if s is None: continue
            if r.action == "lock": s.locked = True; continue
            if s.locked: continue  # cảnh đã khoá không sinh lại
            if ((r.new_narration is not None and r.action not in {"regenerate_audio", "regenerate_both"})
                    or (r.new_visual_prompt is not None and r.action not in {"regenerate_image", "regenerate_both"})
                    or (r.action == "replace_asset" and not r.replacement_path)):
                raise MediaError(f"repair {r.scene_id}: fields must match the requested regeneration or replacement")
            if r.new_visual_prompt: s.visual_prompt = r.new_visual_prompt
            if r.new_narration: s.narration = r.new_narration
            if r.action == "replace_asset" and r.replacement_path:
                safe = self.safe_upload(r.replacement_path)
                if safe is None:  # ngoài upload_dir (vd. ../../etc) hoặc không tồn tại → bỏ qua sửa này, ghi audit
                    self._audit("replace_asset.rejected", m.video_id, {"scene_id": r.scene_id, "path": r.replacement_path[:200],
                                                                        "upload_dir": str(self.upload_dir)})
                    continue
                s.asset_refs.pop("scene_video", None)
                s.asset_refs["scene_image"] = str(safe); s.locked = True
                # An uploaded file is not proof of a license. Rights review must resolve it.
                replacements.append(self._asset(m.video_id, "scene_image", MediaResult(safe, "local", "upload"),
                                                m.version + 1, s.scene_id, license="unknown"))
                continue
            regen[r.scene_id] = (r.action in {"regenerate_audio", "regenerate_both"}, r.action in {"regenerate_image", "regenerate_both"})
            touched.add(r.scene_id)
        if cut.order:
            for i, sid in enumerate(cut.order):
                if sid in by_id: by_id[sid].order = i
            m.scenes.sort(key=lambda x: x.order)
        new = SceneManifest(video_id=m.video_id, version=m.version + 1, script_version=m.script_version,
                            scenes=[Scene(**s.model_dump()) for s in m.scenes], voice=m.voice, aspect=m.aspect, cinema=m.cinema)
        assets: list[MediaAsset] = replacements
        for s in new.scenes:
            if s.scene_id in regen:
                a, i = regen[s.scene_id]
                assets += self.render_scene(new, s, audio=a, image=i, revision=fingerprint(cut.model_dump()))
        fps, res = self._video_opts(new.aspect)
        vr = self.media.video.assemble(self._segments(new, pending=assets), self._dir(new.video_id) / f"draft_v{new.version}.mp4", fps, res)
        assets.append(self._asset(new.video_id, "draft_video", vr, new.version, prompt_ref=f"manifest:v{new.version}"))
        self._publish(assets, f"render.repair:{','.join(sorted(touched)) or 'none'}", new)
        return new

    def cues(self, m: SceneManifest, order: list[str] | None = None) -> list[Any]:
        """Mốc từng cảnh trong bản dựng cuối, tính theo đúng tham số ghép của assembler đang dùng."""
        return timeline(m, order, pad=getattr(self.media.video, "tail_pad_s", 0.0),
                        transition=getattr(self.media.video, "transition_s", 0.0),
                        frame_rate=self._video_opts(m.aspect)[0] if getattr(self.media.video, "bounded", False) else None)

    def _word_times(self, m: SceneManifest) -> dict[str, list[Any]]:
        """Mốc từng từ đã lưu cạnh audio từng cảnh (`<cảnh>.words.json`); file hỏng/thiếu = cảnh đó chia theo tỷ lệ."""
        out: dict[str, list[Any]] = {}
        for s in m.scenes:
            audio = s.asset_refs.get("scene_audio")
            if not audio:
                continue
            try:
                data = json.loads((Path(audio).parent / f"{s.scene_id}.words.json").read_text(encoding="utf-8"))
                out[s.scene_id] = [(float(a), float(b), str(w)) for a, b, w in data]
            except (OSError, ValueError, TypeError):
                continue
        return out

    def finalize(self, m: SceneManifest, order: list[str] | None = None) -> MediaAsset:
        """Bản cuối + phụ đề. Phụ đề sinh thẳng từ narration của manifest (đó chính là văn bản đã đọc) nên không cần
        nhận dạng giọng nói, và mốc thời gian khớp bản dựng vì cùng một công thức."""
        prior = self._resume_output()
        if prior is not None:
            return MediaAsset.model_validate(next(e.payload for e in prior
                                                  if e.topic == "media-assets" and e.payload["kind"] == "final_video"))
        _, music = selections(self.media.cfg, self.upload_dir, m)
        fps, res = self._video_opts(m.aspect)
        directory = self._dir(m.video_id)
        target = directory / f"final_v{m.version}.mp4"
        with tempfile.TemporaryDirectory(prefix=".finalize-", dir=directory) as temp:
            stage = Path(temp)
            # Keep the video's parent stable so bounded scene checkpoints remain reusable.
            pending_video = directory / f"{stage.name}.mp4"
            try:
                r = self.media.video.assemble(self._segments(m, order), pending_video, fps, res)
                assets: list[MediaAsset] = []; moves: list[tuple[MediaAsset, Path]] = []
                captions = None; music_path = None
                words = self._word_times(m)
                text = srt(caption_pages(self.cues(m, order),
                                        max_chars=int(self.media.cfg.video.get("caption_max_chars", 42)), words=words))
                if text:
                    captions = stage / f"captions_v{m.version}.srt"
                    captions.write_text(text, encoding="utf-8")
                    cap = self._asset(m.video_id, "captions", MediaResult(
                        captions, "studio", "srt-word-times" if words else "srt-from-manifest"),
                                      m.version, prompt_ref=f"manifest:v{m.version}")
                    assets.append(cap); moves.append((cap, directory / captions.name))
                if music:
                    music_path = copy_asset(music, stage / f"music_v{m.version}")
                    ma = self._asset(m.video_id, "music", MediaResult(music_path, "local", "licensed-library"), m.version)
                    ma.provenance = music.provenance()
                    assets.append(ma); moves.append((ma, directory / music_path.name))
                burn = captions if self.media.cfg.video.get("burn_captions") else None
                karaoke = None
                if burn and words and self.media.cfg.video.get("karaoke"):
                    # Đốt cứng ASS `\k` (từ đổi màu đúng lúc được đọc); SRT vẫn là phụ đề đăng nền tảng.
                    karaoke = stage / f"captions_v{m.version}.ass"
                    karaoke.write_text(karaoke_ass(self.cues(m, order), words, res,
                                                   max_chars=int(self.media.cfg.video.get("caption_max_chars", 42))),
                                       encoding="utf-8")
                    burn = karaoke
                # FakeVideo records references only; its JSON must point to durable filenames.
                fake = isinstance(self.media.video, FakeVideo)
                finish(self.media.video, r.path, directory / burn.name if burn and fake else burn,
                       directory / music_path.name if music_path and fake else music_path,
                       music.attribution if music else "", float(self.media.cfg.production.get("music_volume", 0.12)))
                if karaoke:
                    karaoke.replace(directory / karaoke.name)
                sheet = self._contact_sheet(m, order, r.path, stage)
                if sheet is not None:
                    assets.append(sheet); moves.append((sheet, directory / Path(sheet.path).name))
                for asset, destination in moves:
                    Path(asset.path).replace(destination); asset.path = str(destination)
                r.path.replace(target); r.path = target
                a = self._asset(m.video_id, "final_video", r, m.version, prompt_ref=f"manifest:v{m.version}")
                assets.insert(0, a)
                self._publish(assets, "render.final")
                return a
            finally:
                pending_video.unlink(missing_ok=True)

    def _contact_sheet(self, m: SceneManifest, order: list[str] | None, video: Path, stage: Path) -> MediaAsset | None:
        """Lưới một khung/cảnh trích từ bản cuối thật cho người duyệt (ADR-0016). Chỉ khi có ffmpeg thật (bản giả là
        JSON, không có khung hình để trích); hỏng thì ghi audit và bỏ qua — công cụ soát không được làm mất bản dựng."""
        if not isinstance(self.media.video, FFmpegAssembler): return None
        marks = [((c.start + c.end) / 2, f"{c.scene_id} · {chapter_time(c.start)}") for c in self.cues(m, order)]
        try:
            r = self.media.video.contact_sheet(video, marks, stage / f"contact_sheet_v{m.version}.jpg")
        except MediaError as e:
            self._audit("render.contact_sheet_failed", m.video_id, {"error": str(e)[:300]})
            return None
        return self._asset(m.video_id, "contact_sheet", r, m.version, prompt_ref=f"manifest:v{m.version}")

    def thumbnails(self, spec: ThumbnailSpec) -> list[MediaAsset]:
        """Ảnh nền không chữ (model ảnh) → CODE phủ `overlay_text`, thu về 1280x720, nén ≤ 2 MB. Asset publish là bản
        hoàn thiện; provenance vẫn ghi provider:model của ảnh nền, phần code làm nằm trong audit `render.thumbnails`."""
        prior = self._resume_output()
        if prior is not None:
            return [MediaAsset.model_validate(e.payload) for e in prior if e.topic == "media-assets"]
        d = self._dir(spec.video_id) / "thumbnails"; out: list[MediaAsset] = []; notes: dict[str, str] = {}
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".thumbnails-", dir=d) as temp:
            for var in spec.variants:
                prompt, size = f"{var.prompt} {NO_TEXT}", image_size(self.media.cfg, "16:9")
                def generate_base(p: Path, prompt: str = prompt, size: str = size, variant: str = var.variant_id) -> MediaResult:
                    return self.ledger.charge(spec.video_id, "image", 1, lambda: self.media.image.generate(prompt, size, p),
                                              variant_id=variant)
                base, _ = self.cache.produce({"kind": "thumbnail", "config": without_prices(self.media.cfg.image), "prompt": prompt, "size": size},
                                             d / f"{var.variant_id}_base.png",
                                             generate_base)
                fin = self.media.video.finish_thumbnail(base.path, Path(temp) / f"{var.variant_id}.jpg", text=var.overlay_text)
                notes[var.variant_id] = fin.notes
                r = MediaResult(fin.path, base.provider, base.model)
                out.append(self._asset(spec.video_id, "thumbnail", r, 0, prompt_ref=f"thumbnail:{var.variant_id}",
                                       variant_id=var.variant_id))
            # Complete every variant before replacing any published thumbnail.
            for asset in out:
                destination = d / f"{asset.variant_id}.jpg"
                Path(asset.path).replace(destination); asset.path = str(destination)
            if out: self._audit("thumbnail.finish", spec.video_id, notes)
            self._publish(out, "render.thumbnails")
        return out
