"""Explicit delegated automation; all external effects still pass a durable gate.

manual: wait at every gate. production: approve valid plans, hold publication.
publish: also approve publish gates only after independent reviews and fresh QC.
Replies and escalations remain human decisions. No generated content grants authority.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .events import Envelope
from .media import frame_size
from .policy_receipts import ACTOR, sign
from .qc import qc_video
from .render_cache import digest

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


class Autopilot:
    def __init__(self, orchestrator: Orchestrator):
        self.orch = orchestrator
        self.holds: dict[str, str] = {}
        self.recheck: dict[str, tuple[str, float, str]] = {}

    def assess(self, sid: str) -> str:
        o = self.orch; cfg = o.renderer.media.cfg
        mode = cfg.production.get("automation", "manual")
        gate = o.gate.pending[sid]
        if mode not in {"production", "publish"}: return "Duyệt thủ công"
        if o.gate.approvers and ACTOR not in o.gate.approvers: return "Chưa cấp quyền policy:studio trong gate.approvers"
        if gate.kind == "plan":
            briefs = o.plans.get(sid, {}).get("briefs", [])
            errors = o.desk.check_plan(briefs)
            if not briefs or errors: return "; ".join(errors) or "Kế hoạch rỗng"
            max_minutes = float(cfg.production.get("max_minutes", 720))
            if any(not 0 < b.get("target_minutes", 0) <= max_minutes for b in briefs): return "Thời lượng vượt giới hạn"
            if any(b.get("risk_tags") for b in briefs): return "Kế hoạch có nội dung cần chuyên gia duyệt"
            return ""
        if gate.kind != "publish" or mode != "publish": return "Chờ người duyệt xuất bản"
        vid = sid.removeprefix("PUB-")
        if not o.desk.ready_for_publish(vid): return "Chưa đủ ba review và gói xuất bản"
        assets = [e.payload for e in o.bus.replay("media-assets", vid)]
        final = next((a for a in reversed(assets) if a["kind"] == "final_video"), None)
        if not final or any(a["provider"] == "fake" for a in assets): return "Không tự xuất bản media fake"
        manifest = o.manifest(vid)
        if manifest is None: return "Thiếu scene manifest"
        paths = {p for s in manifest.scenes for p in s.asset_refs.values()}
        paths.add(final["path"])
        if Path(final["path"]).is_file() and Path(final["path"]).stat().st_size > 256 * 1024**3:
            return "File vượt giới hạn 256 GB"
        thumbnail = o._chosen_thumbnail(vid)
        if thumbnail: paths.add(thumbnail)
        # Include current soundtrack, even when visuals/audio were reused from prior versions.
        paths.update(a["path"] for a in assets if a["kind"] == "music" and a["manifest_version"] == manifest.version)
        for path in paths:
            asset = next((a for a in reversed(assets) if a["path"] == path), None)
            if asset is None or asset["provenance"]["license"] not in {"generated", "owned", "licensed", "cc-by", "cc0"}:
                return "Thiếu hồ sơ bản quyền của tài nguyên đang dùng"
            file = Path(path)
            if not file.is_file() or digest(file)[:16] != asset["checksum"]: return "Tài nguyên bị thiếu hoặc thay đổi"
        preflight = o.preflights.get(vid)
        if preflight is None or any(f.level == "block" for f in preflight.findings): return "Metadata chưa đạt preflight"
        metadata = o.bus.latest("metadata-packages", vid)
        description = metadata.payload.get("description", "") if metadata else ""
        for asset in assets:
            if asset["path"] in paths and asset["provenance"]["license"] == "cc-by":
                credit = asset["provenance"].get("attribution", "").strip()
                if not credit or credit not in description: return "Thiếu ghi công CC-BY trong mô tả xuất bản"
        captions = o._captions_of(vid)
        report = qc_video(Path(final["path"]), expected_duration_s=final.get("duration_s") or 0,
                          expected_resolution=frame_size(cfg.video, manifest.aspect),
                          expected_fps=int(cfg.video.get("fps", 30)),
                          thumbnail=Path(thumbnail) if thumbnail else None,
                          captions=Path(captions) if captions else None)
        o.qcs[vid] = report
        if not report.metrics.get("duration_s"): return "QC chưa đo được file thật"
        duration = float(report.metrics["duration_s"])
        target = o.desk.briefs[vid].target_minutes * 60
        if duration > float(cfg.production.get("max_minutes", 720)) * 60: return "Video vượt giới hạn thời lượng"
        if abs(duration-target) > target * float(cfg.production.get("duration_tolerance", .2)):
            return "Thời lượng thực tế lệch quá xa mục tiêu; cần điều chỉnh kịch bản"
        levels = {"block"} if cfg.production.get("allow_qc_warnings", False) else {"block", "warn"}
        problems = [f.text for f in report.findings if f.level in levels]
        return "; ".join(problems)

    def advance(self) -> int:
        count = 0; self.holds = {}
        for sid, gate in list(self.orch.gate.pending.items()):
            generation = gate.created_at.isoformat()
            prior = self.recheck.get(sid)
            reason = prior[2] if prior and prior[0] == generation and time.monotonic() < prior[1] else self.assess(sid)
            if reason:
                self.holds[sid] = reason
                if not prior or prior[0] != generation or time.monotonic() >= prior[1]:
                    self.recheck[sid] = (generation, time.monotonic() + 30, reason)
                continue
            mode = self.orch.renderer.media.cfg.production.get("automation")
            data = sign({"subject_id": sid, "decision": "approve", "by": ACTOR,
                         "reason": f"Delegated {mode}: deterministic checks passed", "policy": mode,
                         "request_at": gate.created_at.isoformat()})
            self.orch.bus.publish(Envelope(topic="audit-log", key=ACTOR, actor=ACTOR, payload={
                "actor": ACTOR, "action": "gate.decide", "evidence": json.dumps(data, ensure_ascii=False)}))
            count += 1
        return count
