"""Sổ chi phí media (ADR-0016): giá NIÊM YẾT người vận hành khai trong `media.yaml`, nhân với đơn vị CODE đếm được
(ký tự đọc, số ảnh, giây clip), ghi cho mỗi lượt gọi provider THẬT — tức là khi cache trượt; cache trúng không tốn gì.

Học từ cost tracker của OpenMontage: trần `production.media_budget_usd` chặn TRƯỚC lượt gọi sẽ vượt, không phát hiện
sau khi đã tiêu. Ngân sách token (`supervisor.py`) chỉ đếm model text; TTS/ảnh/clip trước đây không có trần tiền nào.

Đây là ước tính theo bảng giá, không phải hóa đơn: provider tính theo token/ký tự/khung riêng, có bậc giá và khuyến
mãi. Không khai giá thì không có dòng sổ nào và `priced()` sai — nơi hiển thị nói "chưa khai giá" chứ không bịa 0 USD.
Provider `fake` luôn miễn phí dù cấu hình có giá: nó không tiêu tiền thật.

Khoá giá: `tts.price_per_1k_chars` và/hoặc `tts.price_per_second` (provider tính theo giây/token audio như Gemini),
`image.price_per_image` (ảnh cảnh và nền thumbnail), `scene_video.price_per_second`.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable
from typing import Any

from .bus import InMemoryBus
from .events import Envelope
from .media import MediaConfig, MediaError, MediaResult

RATES = {"tts": "price_per_1k_chars", "image": "price_per_image", "scene_video": "price_per_second"}
TTS_PER_SECOND = "price_per_second"
TTS_CHARS_PER_SECOND = 15.0   # ước lượng trước lượt gọi cho trần; sổ ghi theo thời lượng audio thật sau lượt gọi
PRICE_KEYS = frozenset(RATES.values()) | {TTS_PER_SECOND}
ACTION = "media.cost"
WARNING_ACTION = "media.cost_warning"   # một lần khi video vượt WARN_RATIO trần; không chặn
WARN_RATIO = 0.8
ACTOR = "renderer"  # sổ nằm trong audit-log của renderer: đúng nơi đã ghi `render.cache`


class MediaBudgetError(MediaError):
    """Lượt gọi sẽ vượt trần: thử lại không làm rẻ đi, orchestrator tạm dừng video ngay thay vì retry."""


def _usd(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise MediaError(f"{name} phải là số USD hữu hạn ≥ 0 (đang là {value!r})")
    return float(value)


def without_prices(section: dict[str, Any]) -> dict[str, Any]:
    """Cấu hình provider để băm khoá cache. Giá là sổ sách, không phải đầu vào provider: để giá trong khoá thì khai
    `price_*` làm mọi asset đã trả tiền bị sinh lại — chính bảng giá lại gây tốn tiền."""
    return {k: v for k, v in section.items() if k not in PRICE_KEYS}


def rate(cfg: MediaConfig, channel: str) -> float:
    key = RATES[channel]
    return _usd(getattr(cfg, channel).get(key) or 0, f"{channel}.{key}")


def tts_second_rate(cfg: MediaConfig) -> float:
    return _usd(cfg.tts.get(TTS_PER_SECOND) or 0, f"tts.{TTS_PER_SECOND}")


def priced(cfg: MediaConfig) -> bool:
    return any(rate(cfg, channel) > 0 for channel in RATES) or tts_second_rate(cfg) > 0


def budget(cfg: MediaConfig) -> float:
    """Trần chi phí media cho MỘT video; 0 = không trần."""
    return _usd(cfg.production.get("media_budget_usd") or 0, "production.media_budget_usd")


def quote(cfg: MediaConfig, channel: str, units: float, seconds: float | None = None) -> float:
    """Giá niêm yết của một lượt: `units` là ký tự (tts), số ảnh (image) hoặc giây (scene_video). TTS có thêm giá theo
    giây audio: `seconds` thật khi đã có, chưa có thì ước lượng từ số ký tự."""
    if str(getattr(cfg, channel).get("provider") or "fake") == "fake": return 0.0
    per = rate(cfg, channel)
    if channel != "tts": return round(per * units, 6)
    audio = seconds if seconds is not None else units / TTS_CHARS_PER_SECOND
    return round(per * units / 1000 + tts_second_rate(cfg) * audio, 6)


def near_cap(spent_usd: float, cap: float) -> bool:
    """Đã dùng ≥ WARN_RATIO trần (cùng ngưỡng với dòng audit `media.cost_warning`); không trần thì không cảnh báo."""
    return bool(cap) and spent_usd >= WARN_RATIO * cap - 1e-9


def totals(events: Iterable[Envelope]) -> dict[str, float]:
    """Tổng USD theo video từ các dòng `media.cost`; dòng hỏng bị bỏ qua chứ không làm sập cả tổng."""
    out: dict[str, float] = {}
    for e in events:
        p = e.payload
        if e.actor != ACTOR or p.get("action") != ACTION or not p.get("video_id"): continue
        try: usd = _usd(json.loads(p.get("evidence") or "")["usd"], "usd")
        except (ValueError, TypeError, KeyError, MediaError): continue
        out[p["video_id"]] = round(out.get(p["video_id"], 0.0) + usd, 6)
    return out


def spent(bus: InMemoryBus, video_id: str) -> float:
    return totals(bus.replay("audit-log", ACTOR)).get(video_id, 0.0)


class Ledger:
    """Sổ của một renderer. Tổng mỗi video đọc từ audit-log bền một lần rồi cộng dồn trong RAM (một orchestrator trên
    một DB — lease), nên video dài nghìn cảnh không phải đọc lại cả sổ trước mỗi lượt gọi."""

    def __init__(self, bus: InMemoryBus, cfg: MediaConfig, audit: Callable[[str, str, dict[str, Any]], None]):
        self.bus, self.cfg, self._audit = bus, cfg, audit
        self._spent: dict[str, float] = {}

    def total(self, video_id: str) -> float:
        if video_id not in self._spent: self._spent[video_id] = spent(self.bus, video_id)
        return self._spent[video_id]

    def charge(self, video_id: str, channel: str, units: float, call: Callable[[], MediaResult],
               **ref: str) -> MediaResult:
        """Kiểm trần → gọi provider → ghi sổ. Provider lỗi thì không ghi (không có gì được giao)."""
        usd = quote(self.cfg, channel, units)
        if usd <= 0: return call()
        cap, used = budget(self.cfg), self.total(video_id)
        if cap and used + usd > cap + 1e-9:
            raise MediaBudgetError(f"ngân sách media {cap:g} USD của {video_id}: đã dùng {used:.4f}, lượt {channel} "
                                   f"{' '.join(ref.values())} cần {usd:.4f} — tăng production.media_budget_usd "
                                   "hoặc dùng provider rẻ hơn")
        result = call()
        extra: dict[str, Any] = {}
        if channel == "tts" and tts_second_rate(self.cfg) > 0 and result.duration_s:
            usd = quote(self.cfg, channel, units, seconds=result.duration_s); extra["seconds"] = result.duration_s
        self._spent[video_id] = total = round(used + usd, 6)
        self._audit(ACTION, video_id, {"channel": channel, "units": units, "usd": usd,
                                       "provider": str(getattr(self.cfg, channel).get("provider")),
                                       "model": result.model, **extra, **ref})
        if cap and used < WARN_RATIO * cap <= total:
            self._audit(WARNING_ACTION, video_id, {"spent_usd": total, "cap_usd": cap, "pct": round(100 * total / cap),
                                                   "channel": channel, **ref})
        return result
