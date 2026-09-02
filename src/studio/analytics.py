"""Phần xác định của khối phân tích: map đường cong giữ chân (retention) vào thời lượng cảnh (scene-aware
retention) và kiểm định thí nghiệm A/B. Model chỉ diễn giải kết quả đã tính, không tự tính số."""
from __future__ import annotations

import math
from itertools import pairwise

from .events import Experiment, ExperimentKind, PerformanceSnapshot, RetentionDrop, RetentionPoint, SceneManifest

DROP_THRESHOLD_PCT = 5.0  # sụt ≥ 5 điểm % giữa hai mốc liên tiếp = một điểm rơi đáng chú ý
CONFIDENCE_MIN = 0.95


def scene_at(m: SceneManifest, t: float) -> str | None:
    acc = 0.0
    for s in sorted(m.scenes, key=lambda x: x.order):
        acc += s.duration_s
        if t < acc: return s.scene_id
    return m.scenes[-1].scene_id if m.scenes else None


def retention_drops(curve: list[RetentionPoint], m: SceneManifest | None, threshold: float = DROP_THRESHOLD_PCT) -> list[RetentionDrop]:
    """Điểm rơi: sụt ≥ threshold điểm % giữa hai mốc liên tiếp; gắn scene_id nếu có manifest."""
    out: list[RetentionDrop] = []
    pts = sorted(curve, key=lambda p: p.t)
    for a, b in pairwise(pts):
        d = a.pct - b.pct
        if d >= threshold:
            out.append(RetentionDrop(scene_id=scene_at(m, b.t) if m else None, t=b.t, drop_pct=round(d, 2)))
    return out


def _z_two_proportions(x1: int, n1: int, x2: int, n2: int) -> float:
    if min(n1, n2) == 0: return 0.0
    p1, p2 = x1 / n1, x2 / n2; p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 else 0.0
    return (p2 - p1) / se if se else 0.0


def _confidence(z: float) -> float:
    return 0.5 * (1 + math.erf(abs(z) / math.sqrt(2)))


def judge_experiment(experiment_id: str, kind: ExperimentKind, control: PerformanceSnapshot, variant: PerformanceSnapshot,
                     retention_tolerance_s: float = 0.0) -> Experiment:
    """Kết luận A/B CTR: biến thể chỉ thắng khi độ tin cậy ≥ 0.95 VÀ thời lượng xem trung bình không giảm quá
    `retention_tolerance_s` (guard: CTR cao nhưng khán giả bỏ đi sớm là clickbait)."""
    x1, n1 = int(control.ctr * control.impressions), control.impressions
    x2, n2 = int(variant.ctr * variant.impressions), variant.impressions
    z = _z_two_proportions(x1, n1, x2, n2); conf = round(_confidence(z), 4)
    lift = round((variant.ctr - control.ctr) / control.ctr, 4) if control.ctr else None
    guard = variant.avg_view_duration_s >= control.avg_view_duration_s - retention_tolerance_s
    variants = [control.variant_id or "control", variant.variant_id or "variant"]
    winner = None
    if conf >= CONFIDENCE_MIN and z > 0 and guard: winner = variants[1]
    elif conf >= CONFIDENCE_MIN and z < 0: winner = variants[0]
    return Experiment(experiment_id=experiment_id, kind=kind, variants=variants, winner=winner, ctr_lift=lift,
                      confidence=conf, retention_guard_ok=guard)
