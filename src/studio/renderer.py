"""Renderer: CODE biến scene manifest thành asset thật qua lớp media (ADR-0004). Không có model nào ở đây.

- `render(manifest)`: mỗi cảnh chưa `locked` (hoặc nằm trong danh sách sửa) → TTS + ảnh; cảnh đã có asset và locked
  thì dùng lại. Ghép bản nháp `draft_video`. Mọi asset có checksum + provenance (provider:model, prompt_ref) để
  rights-checker kiểm và gate publish thấy nguồn gốc.
- `apply_cutlist(manifest, cut)`: sửa đúng những cảnh editor yêu cầu (regenerate/replace/lock), tăng `version` manifest,
  render lại chỉ phần đó → bản nháp mới. Không làm lại cả video.
- `finalize(manifest, order)`: ghép `final_video` theo thứ tự chốt.
- `thumbnails(spec)`: mỗi biến thể A/B một ảnh.
Kết quả publish lên `media-assets` dưới actor `renderer`, kèm audit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .bus import InMemoryBus
from .events import AssetKind, AuditLog, CutList, Envelope, MediaAsset, Provenance, Scene, SceneManifest, ThumbnailSpec
from .media import MediaResult, MediaSuite, make_media

ACTOR = "renderer"


def checksum(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else ""


class Renderer:
    def __init__(self, bus: InMemoryBus, media: MediaSuite | None = None, out_dir: Path | None = None,
                 upload_dir: Path | None = None):
        self.bus = bus
        self.media = media or make_media()
        self.out_dir = Path(out_dir) if out_dir else self.media.cfg.output_dir
        # `replacement_path` trong cut-list do model viết → chỉ chấp nhận file nằm TRONG thư mục upload này
        self.upload_dir = Path(upload_dir or self.media.cfg.upload_dir or self.out_dir / "uploads")

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

    def _audit(self, action: str, video_id: str, data: dict[str, Any]) -> None:
        a = AuditLog(actor=ACTOR, action=action, video_id=video_id, evidence=json.dumps(data, ensure_ascii=False))
        self.bus.publish(Envelope(topic="audit-log", key=ACTOR, actor=ACTOR, payload=a.model_dump()))

    def _publish(self, assets: list[MediaAsset], action: str) -> list[Envelope]:
        out = [self.bus.publish(Envelope(topic="media-assets", key=a.video_id, actor=ACTOR, payload=a.model_dump()))
               for a in assets]
        if assets:
            a = AuditLog(actor=ACTOR, action=action, video_id=assets[0].video_id,
                         evidence=json.dumps({"assets": [(x.kind, x.scene_id or x.variant_id) for x in assets],
                                              "providers": self.media.names}, ensure_ascii=False))
            self.bus.publish(Envelope(topic="audit-log", key=ACTOR, actor=ACTOR, payload=a.model_dump()))
        return out

    # ---------- render ----------

    def render_scene(self, m: SceneManifest, s: Scene, audio: bool = True, image: bool = True) -> list[MediaAsset]:
        d = self._dir(m.video_id) / f"v{m.version}"; out: list[MediaAsset] = []
        if audio:
            r = self.media.tts.synthesize(s.narration, m.voice, d / f"{s.scene_id}.wav")
            s.asset_refs["scene_audio"] = str(r.path); s.duration_s = r.duration_s or s.duration_s
            out.append(self._asset(m.video_id, "scene_audio", r, m.version, s.scene_id, prompt_ref=f"{s.scene_id}:narration"))
        if image:
            size = str(self.media.cfg.image.get("size") or ("1024x1792" if m.aspect == "9:16" else "1792x1024"))
            r = self.media.image.generate(s.visual_prompt, size, d / f"{s.scene_id}.png")
            s.asset_refs["scene_image"] = str(r.path)
            out.append(self._asset(m.video_id, "scene_image", r, m.version, s.scene_id, prompt_ref=f"{s.scene_id}:visual_prompt"))
        return out

    def _segments(self, m: SceneManifest, order: list[str] | None = None) -> list[tuple[Path, Path, float]]:
        by_id = {s.scene_id: s for s in m.scenes}
        ids = order or [s.scene_id for s in sorted(m.scenes, key=lambda x: x.order)]
        segs = []
        for sid in ids:
            s = by_id[sid]
            if "scene_image" not in s.asset_refs or "scene_audio" not in s.asset_refs:
                raise ValueError(f"cảnh {sid} chưa có đủ asset để ghép")
            segs.append((Path(s.asset_refs["scene_image"]), Path(s.asset_refs["scene_audio"]), s.duration_s))
        return segs

    def render(self, m: SceneManifest, only: set[str] | None = None) -> list[MediaAsset]:
        """Render các cảnh cần (chưa locked hoặc trong `only`) rồi ghép bản nháp. Trả về asset đã publish."""
        assets: list[MediaAsset] = []
        for s in m.scenes:
            need = (only is not None and s.scene_id in only) or (only is None and not (s.locked and len(s.asset_refs) >= 2))
            if need:
                assets += self.render_scene(m, s)
        v = self.media.cfg.video
        r = self.media.video.assemble(self._segments(m), self._dir(m.video_id) / f"draft_v{m.version}.mp4",
                                      int(v.get("fps", 30)), str(v.get("resolution", "1920x1080")))
        assets.append(self._asset(m.video_id, "draft_video", r, m.version, prompt_ref=f"manifest:v{m.version}"))
        # manifest cùng version nhưng đã có asset_refs/duration thật → publish lại để bus là nguồn sự thật (resume, editor, finalize)
        self.bus.publish(Envelope(topic="scene-manifests", key=m.video_id, actor=ACTOR, payload=m.model_dump()))
        self._publish(assets, "render.draft")
        return assets

    def apply_cutlist(self, m: SceneManifest, cut: CutList) -> SceneManifest:
        """Sửa manifest theo cut-list: đổi prompt/narration, thay asset người tải lên, khoá cảnh. Trả về manifest mới
        (version + 1) đã render lại đúng các cảnh bị chạm."""
        by_id = {s.scene_id: s for s in m.scenes}
        touched: set[str] = set(); regen: dict[str, tuple[bool, bool]] = {}
        for r in cut.repairs:
            s = by_id.get(r.scene_id)
            if s is None: continue
            if r.action == "lock": s.locked = True; continue
            if s.locked: continue  # cảnh đã khoá không sinh lại
            if r.new_visual_prompt: s.visual_prompt = r.new_visual_prompt
            if r.new_narration: s.narration = r.new_narration
            if r.action == "replace_asset" and r.replacement_path:
                safe = self.safe_upload(r.replacement_path)
                if safe is None:  # ngoài upload_dir (vd. ../../etc) hoặc không tồn tại → bỏ qua sửa này, ghi audit
                    self._audit("replace_asset.rejected", m.video_id, {"scene_id": r.scene_id, "path": r.replacement_path[:200],
                                                                        "upload_dir": str(self.upload_dir)})
                    continue
                s.asset_refs["scene_image"] = str(safe); s.locked = True; continue
            regen[r.scene_id] = (r.action in {"regenerate_audio", "regenerate_both"}, r.action in {"regenerate_image", "regenerate_both"})
            touched.add(r.scene_id)
        if cut.order:
            for i, sid in enumerate(cut.order):
                if sid in by_id: by_id[sid].order = i
            m.scenes.sort(key=lambda x: x.order)
        new = SceneManifest(video_id=m.video_id, version=m.version + 1, script_version=m.script_version,
                            scenes=[Scene(**s.model_dump()) for s in m.scenes], voice=m.voice, aspect=m.aspect)
        assets: list[MediaAsset] = []
        for s in new.scenes:
            if s.scene_id in regen:
                a, i = regen[s.scene_id]; assets += self.render_scene(new, s, audio=a, image=i)
        v = self.media.cfg.video
        vr = self.media.video.assemble(self._segments(new), self._dir(new.video_id) / f"draft_v{new.version}.mp4",
                                      int(v.get("fps", 30)), str(v.get("resolution", "1920x1080")))
        assets.append(self._asset(new.video_id, "draft_video", vr, new.version, prompt_ref=f"manifest:v{new.version}"))
        self.bus.publish(Envelope(topic="scene-manifests", key=new.video_id, actor=ACTOR, payload=new.model_dump()))
        self._publish(assets, f"render.repair:{','.join(sorted(touched)) or 'none'}")
        return new

    def finalize(self, m: SceneManifest, order: list[str] | None = None) -> MediaAsset:
        v = self.media.cfg.video
        r = self.media.video.assemble(self._segments(m, order), self._dir(m.video_id) / f"final_v{m.version}.mp4",
                                      int(v.get("fps", 30)), str(v.get("resolution", "1920x1080")))
        a = self._asset(m.video_id, "final_video", r, m.version, prompt_ref=f"manifest:v{m.version}")
        self._publish([a], "render.final")
        return a

    def thumbnails(self, spec: ThumbnailSpec) -> list[MediaAsset]:
        d = self._dir(spec.video_id) / "thumbnails"; out = []
        for var in spec.variants:
            r = self.media.image.generate(f"{var.prompt}. Overlay text: {var.overlay_text}", "1792x1024", d / f"{var.variant_id}.png")
            out.append(self._asset(spec.video_id, "thumbnail", r, 0, prompt_ref=f"thumbnail:{var.variant_id}", variant_id=var.variant_id))
        self._publish(out, "render.thumbnails")
        return out
