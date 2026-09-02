"""Điều phối model theo TÀI KHOẢN SUBSCRIPTION thay vì API trả theo token (ADR-0006; cùng thiết kế với
software-company ADR-0019).

Phòng ban không mua token: nó dùng những gói đang đăng nhập trên máy — Claude Pro/Max qua CLI `claude -p`, Google
Antigravity qua `../gateway` (xoay vòng tài khoản Google), model local... Mỗi gói là một **backend** có model riêng theo
tier. `RoutingClient` gộp tất cả thành MỘT `ModelClient`:

- Chọn backend theo `routing.prefer[tier]` (vd. `light` đi Antigravity miễn phí, `strong` đi Claude Max), còn lại theo
  thứ tự khai báo.
- Backend hết quota (429, 402, "usage limit", "RESOURCE_EXHAUSTED", "thử lại sau Ns"...) → nghỉ `cooldown_s` (hoặc đúng
  số giây provider bảo), lượt này đi backend kế. Lỗi mạng / 5xx / timeout → nghỉ ngắn `transient_cooldown_s`.
  Lỗi NỘI DUNG (JSON hỏng, model từ chối) không phải lỗi backend → ném ra ngay, không xoay.
- Mọi backend đều nghỉ → `LLMError` kèm "thử lại sau Ns"; orchestrator xử lý như lỗi model thường (supervisor thấy).
- Mỗi lần xoay được ghi chú; `drain_retries()` trả về để runner ghi audit.

Tên backend không lộ trong `Completion.model` (vẫn là tên model thật)."""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .llm import Completion, LLMError, ModelClient, Refused

QUOTA_PATTERNS = re.compile(
    r"429|402|quota|rate.?limit|resource_exhausted|usage limit|hit your limit|limit reached|exhausted|cooldown|"
    r"insufficient|billing|thử lại sau|overloaded|529", re.IGNORECASE)
TRANSIENT_PATTERNS = re.compile(r"lỗi mạng|timeout|quá \d+s|HTTP 5\d\d|HTTP 408|HTTP 409|thoát mã|API 5\d\d", re.IGNORECASE)
RETRY_AFTER_PATTERNS = (re.compile(r"retry.?after[:\s]+(\d+)", re.IGNORECASE),
                        re.compile(r"thử lại sau(?: khoảng)?\s+(\d+)\s*s", re.IGNORECASE),
                        re.compile(r"resets? in\s+(\d+)\s*s", re.IGNORECASE))
MISSING_PATTERNS = re.compile(r"không tìm thấy|not found|no such file|chưa cấu hình model|chưa có tài khoản|pool trống", re.IGNORECASE)


def plain(message: str) -> str:
    """Thân lỗi HTTP thường là JSON với tiếng Việt bị escape (`Ch\u01b0a c\u00f3`); giải mã để mẫu tiếng Việt khớp."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), message)


def retry_after_seconds(message: str) -> float | None:
    for pat in RETRY_AFTER_PATTERNS:
        if m := pat.search(plain(message)):
            return float(m.group(1))
    return None


def is_quota_error(e: BaseException) -> bool:
    return bool(QUOTA_PATTERNS.search(plain(str(e))))


def is_missing_error(e: BaseException) -> bool:
    return bool(MISSING_PATTERNS.search(plain(str(e))))


def is_transient_error(e: BaseException) -> bool:
    return bool(TRANSIENT_PATTERNS.search(plain(str(e))))


@dataclass
class Backend:
    name: str
    client: ModelClient
    supports_tools: bool = True   # codex: không nhận tool web của phòng ban
    tiers: frozenset[str] = frozenset()
    cooldown_until: float = 0.0
    cooldown_reason: str = ""
    calls: int = 0
    failures: int = 0

    def ready(self, now: float) -> bool:
        return now >= self.cooldown_until


@dataclass
class RoutingClient:
    backends: list[Backend]
    cooldown_s: float = 3600.0
    transient_cooldown_s: float = 60.0
    prefer: dict[str, str] = field(default_factory=dict)   # tier → tên backend ưu tiên
    clock: Callable[[], float] = time.time
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.backends:
            raise LLMError("routing: chưa khai báo backend nào (llm.yaml `backends:`)")
        names = [b.name for b in self.backends]
        if len(set(names)) != len(names):
            raise LLMError(f"routing: tên backend trùng: {names}")
        for tier, name in self.prefer.items():
            if name not in names:
                raise LLMError(f"routing.prefer[{tier}] = `{name}` không có trong backends {names}")

    def order(self, tier: str, needs_tools: bool = False) -> list[Backend]:
        first = self.prefer.get(tier)
        ordered = sorted(self.backends, key=lambda b: 0 if b.name == first else 1)   # sort ổn định: giữ thứ tự khai báo
        return [b for b in ordered if b.supports_tools or not needs_tools]

    def status(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [{"name": b.name, "ready": b.ready(now), "cooldown_remaining": max(0, int(b.cooldown_until - now)),
                 "reason": b.cooldown_reason if not b.ready(now) else "", "tiers": sorted(b.tiers),
                 "calls": b.calls, "failures": b.failures} for b in self.backends]

    def drain_retries(self) -> list[str]:
        out: list[str] = []
        for b in self.backends:
            drain = getattr(b.client, "drain_retries", None)
            if drain: out.extend(f"[{b.name}] {n}" for n in drain())
        out.extend(self.notes); self.notes = []
        return out

    def _rest(self, b: Backend, e: BaseException, now: float) -> None:
        msg = plain(str(e))
        if MISSING_PATTERNS.search(msg):
            secs = self.cooldown_s; kind = "thiếu"
        elif (ra := retry_after_seconds(msg)) is not None:
            secs = ra; kind = "hết quota"
        elif is_quota_error(e):
            secs = self.cooldown_s; kind = "hết quota"
        else:
            secs = self.transient_cooldown_s; kind = "lỗi vận chuyển"
        b.failures += 1
        b.cooldown_until = now + secs
        b.cooldown_reason = f"{kind}: {msg[:120]}"
        self.notes.append(f"backend {b.name} {kind} → nghỉ {int(secs)}s: {msg[:120]}")

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[Any] | None = None,
                 messages: list[dict[str, Any]] | None = None) -> Completion:
        """`tools`/`messages` (ADR-0007) chuyển nguyên cho backend được chọn; xoay backend giữa chừng một vòng tool là
        chấp nhận được vì hội thoại trung lập nằm trong `messages`, không nằm ở phía provider."""
        candidates = self.order(model_tier, bool(tools))
        if not candidates:
            raise LLMError("routing: yêu cầu có tool nhưng không backend nào hỗ trợ tool-use")
        tried = 0
        for b in candidates:
            now = self.clock()
            if not b.ready(now): continue
            tried += 1
            b.calls += 1
            try:
                extra: dict[str, Any] = {}  # chỉ truyền khi có, để client không tool-use vẫn dùng được
                if tools: extra["tools"] = tools
                if messages: extra["messages"] = messages
                c = b.client.complete(system=system, user=user, schema=schema, model_tier=model_tier, cache_key=cache_key, **extra)
            except Refused:
                raise
            except LLMError as e:
                if is_quota_error(e) or is_transient_error(e) or is_missing_error(e):
                    self._rest(b, e, now); continue
                raise    # lỗi nội dung: việc của agent/supervisor, không phải của backend
            if tried > 1 or b is not candidates[0]:
                self.notes.append(f"đi backend {b.name} (model {c.model}) cho tier {model_tier}")
            return c
        now = self.clock()
        soonest = min((b.cooldown_until - now for b in candidates), default=0.0)
        why = "; ".join(f"{b.name}: {b.cooldown_reason}" for b in candidates if b.cooldown_reason)
        raise LLMError(f"mọi backend đều đang nghỉ, thử lại sau {max(1, int(soonest))}s ({why})")
