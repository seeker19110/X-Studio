"""Điều phối model theo TÀI KHOẢN SUBSCRIPTION thay vì API trả theo token (K3.3d; company ADR-0019, studio ADR-0006).

Công ty ở đây không mua token: nó dùng những gói đăng ký đang có trên máy — Claude Pro/Max qua CLI `claude -p`,
Google Antigravity qua `../gateway` (xoay vòng tài khoản Google), ChatGPT/Codex, hay model local. Mỗi gói là một
**backend**; mỗi backend nói được model nào cho tier nào. `RoutingClient` bọc tất cả thành MỘT `ModelClient`:

- Chọn backend theo `routing.prefer[tier]` (vd. tier `light` đi Antigravity miễn phí, `strong` đi Claude Max),
  còn lại theo thứ tự khai báo.
- Backend hết quota / hết hạn mức ngày (429, 402, "usage limit", "RESOURCE_EXHAUSTED", "thử lại sau Ns"...) → nghỉ
  `cooldown_s` (hoặc đúng số giây provider bảo) và lượt này đi backend kế. Lỗi mạng / 5xx → nghỉ ngắn
  `transient_cooldown_s`. Lỗi NỘI DUNG (JSON hỏng, model từ chối) không phải lỗi backend → ném ra ngay, không xoay.
- Yêu cầu có `tools` chỉ đi backend hỗ trợ tool-use (CLI `claude -p` thì không).
- Mọi backend đều nghỉ → `TransientError` kèm "sớm nhất Ns" để orchestrator hoãn event, không tính lỗi agent.
- Mỗi lần xoay được ghi chú; runner lấy qua `drain_retries()` và ghi audit `llm_retry` như retry thường.

Tên backend không lộ trong `Completion.model` (vẫn là tên model thật để `Pricing` khớp giá theo tiền tố).

**Bản vào core là bản company, studio được nâng theo** (nguyên tắc 3 của `__init__.py`). Bốn điểm nâng, đều đổi
hành vi studio có chủ ý — ai đọc CHANGELOG studio của K3.3d sẽ thấy cùng danh sách này:

1. **Phân loại theo mã HTTP trước, regex chỉ là đường lùi.** Studio trước đây chỉ có regex; `LLMError.status`
   (K3.3c) nay cho phép hỏi thẳng mã. Đường lùi vẫn cần vì CLI và gateway trả text không mã.
2. **`QUOTA_PATTERNS` có ranh giới từ.** Bản studio khớp `insufficient` trần và `429` trong bất kỳ số nào, nên
   "unlimited", "billingham" hay mã lỗi 4290 đọc ra "hết quota" và cho backend còn tốt đi nghỉ một tiếng. Đây là
   **bug thật của studio**, không phải khác biệt phong cách.
3. **`is_auth_error` (401/403).** Studio không có: khoá sai đọc ra "lỗi nội dung" và ném thẳng cho agent, nên một
   backend cấu hình hỏng làm chết từng lượt thay vì tự nghỉ ra một bên.
4. **Mọi backend đều nghỉ → `TransientError`, không phải `LLMError`.** `TransientError` là con của `LLMError` nên
   chỗ nào đang `except LLMError` vẫn bắt được; điểm mới là orchestrator **phân biệt được** để hoãn event thay vì
   tính một lỗi agent (xem `studio.orchestrator._call`, K3.3d).

`is_transient_error`/`TRANSIENT_PATTERNS` của studio **không** lên đây: đoán "lỗi tạm thời" bằng regex trên
thông điệp là thứ `TransientError` sinh ra để thay thế — adapter biết chắc lỗi của mình là tạm thời hay không,
người đọc chuỗi thì không.
"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from .llm import Completion, LLMError, ModelClient, Refused, TransientError
from .tools import ToolSpec

__all__ = [
    "AUTH_STATUS",
    "MISSING_PATTERNS",
    "MISSING_STATUS",
    "QUOTA_PATTERNS",
    "QUOTA_STATUS",
    "RETRY_AFTER_PATTERNS",
    "Backend",
    "RoutingClient",
    "http_status",
    "is_auth_error",
    "is_missing_error",
    "is_quota_error",
    "plain",
    "retry_after_seconds",
]

# Regex chỉ là đường lùi khi lỗi KHÔNG mang mã HTTP (CLI, gateway trả text): có ranh giới từ để "limited edition",
# "unlimited", "billingham" hay số 4290 không bị coi là hết quota.
QUOTA_PATTERNS = re.compile(
    r"\b429\b|\b402\b|\bquota\b|\brate.?limits?\b|\bresource_exhausted\b|\busage limit\b|\bhit your limit\b|"
    r"\blimit reached\b|\bexhausted\b|\bcooldown\b|\binsufficient_quota\b|\binsufficient quota\b|\bbilling\b|"
    r"thử lại sau|\boverloaded\b|\b529\b", re.IGNORECASE)
QUOTA_STATUS = frozenset({429, 402})
MISSING_STATUS = frozenset({404})
AUTH_STATUS = frozenset({401, 403})
RETRY_AFTER_PATTERNS = (re.compile(r"retry.?after[:\s]+(\d+)", re.IGNORECASE),
                        re.compile(r"thử lại sau(?: khoảng)?\s+(\d+)\s*s", re.IGNORECASE),
                        re.compile(r"resets? in\s+(\d+)\s*s", re.IGNORECASE))
MISSING_PATTERNS = re.compile(r"không tìm thấy|\bnot found\b|\bno such file\b|chưa cấu hình model|chưa có tài khoản|pool trống",
                              re.IGNORECASE)


def http_status(e: BaseException) -> int | None:
    """Mã HTTP gắn trên LLMError (adapter API đặt); None với lỗi CLI/mạng → phân loại lùi về regex."""
    s = getattr(e, "status", None)
    return int(s) if isinstance(s, int) else None


def plain(message: str) -> str:
    """Thân lỗi HTTP thường là JSON với tiếng Việt bị escape (`Ch\u01b0a c\u00f3`); giải mã để mẫu tiếng Việt khớp."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), message)


def retry_after_seconds(message: str) -> float | None:
    for pat in RETRY_AFTER_PATTERNS:
        if m := pat.search(plain(message)):
            return float(m.group(1))
    return None


def is_quota_error(e: BaseException) -> bool:
    if (s := http_status(e)) is not None: return s in QUOTA_STATUS
    return bool(QUOTA_PATTERNS.search(plain(str(e))))


def is_missing_error(e: BaseException) -> bool:
    if (s := http_status(e)) is not None: return s in MISSING_STATUS
    return bool(MISSING_PATTERNS.search(plain(str(e))))


def is_auth_error(e: BaseException) -> bool:
    """401/403: backend cấu hình sai khoá/tài khoản — không phải lỗi nội dung, xoay backend khác và nghỉ dài."""
    return http_status(e) in AUTH_STATUS


@dataclass
class Backend:
    """Một gói tài khoản. `tiers` = tier có model riêng (thiếu thì `model_for` của client tự lùi về standard/strong)."""
    name: str
    client: ModelClient
    supports_tools: bool = True
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

    @property
    def notes(self) -> list[str]:
        """Ghi chú xoay backend theo THREAD (--workers>1): audit llm_retry của agent nào là của agent đó."""
        tls = self.__dict__.setdefault("_tls", threading.local())
        if not hasattr(tls, "notes"): tls.notes = []
        # `threading.local()` là `Any` với mypy — `cast` ở đây nói ra điều setter bên dưới bảo đảm, chứ không
        # nới lỏng cấu hình (mypy của core chặt hơn của company, và đó là chỗ mã này sống từ K3.3d).
        return cast(list[str], tls.notes)

    @notes.setter
    def notes(self, value: list[str]) -> None:
        self.__dict__.setdefault("_tls", threading.local()).notes = value

    def __post_init__(self) -> None:
        if not self.backends:
            raise LLMError("routing: chưa khai báo backend nào (llm.yaml `backends:`)")
        names = [b.name for b in self.backends]
        if len(set(names)) != len(names):
            raise LLMError(f"routing: tên backend trùng: {names}")
        for tier, name in self.prefer.items():
            if name not in names:
                raise LLMError(f"routing.prefer[{tier}] = `{name}` không có trong backends {names}")

    # ---- thứ tự thử ----
    def order(self, tier: str, needs_tools: bool) -> list[Backend]:
        first = self.prefer.get(tier)
        ordered = sorted(self.backends, key=lambda b: 0 if b.name == first else 1)   # sort ổn định: giữ thứ tự khai báo
        return [b for b in ordered if b.supports_tools or not needs_tools]

    # ---- trạng thái cho CLI/report ----
    def status(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [{"name": b.name, "ready": b.ready(now), "cooldown_remaining": max(0, int(b.cooldown_until - now)),
                 "reason": b.cooldown_reason if not b.ready(now) else "", "tools": b.supports_tools,
                 "tiers": sorted(b.tiers), "calls": b.calls, "failures": b.failures} for b in self.backends]

    def drain_retries(self) -> list[str]:
        out: list[str] = []
        for b in self.backends:
            drain = getattr(b.client, "drain_retries", None)
            if drain: out.extend(f"[{b.name}] {n}" for n in drain())
        out.extend(self.notes); self.notes = []
        return out

    # ---- gọi ----
    def _rest(self, b: Backend, e: BaseException, now: float) -> None:
        msg = plain(str(e))
        if is_missing_error(e):
            secs = self.cooldown_s; kind = "thiếu"
        elif is_auth_error(e):
            secs = self.cooldown_s; kind = "xác thực"
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

    def bind_toolbox(self, toolbox: Any | None) -> None:
        """ADR-0024: chuyển tiếp ToolBox cho mọi backend hiểu (chưa biết lượt này sẽ đi backend nào)."""
        for b in self.backends:
            if (bind := getattr(b.client, "bind_toolbox", None)) is not None: bind(toolbox)

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
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
                c = b.client.complete(system=system, user=user, schema=schema, model_tier=model_tier,
                                      cache_key=cache_key, tools=tools, messages=messages, workdir=workdir)
            except Refused:
                raise
            except TransientError as e:
                self._rest(b, e, now); continue
            except LLMError as e:
                if is_quota_error(e) or is_missing_error(e) or is_auth_error(e):
                    self._rest(b, e, now); continue
                raise    # lỗi nội dung: việc của agent/supervisor, không phải của backend
            if tried > 1 or b is not candidates[0]:
                self.notes.append(f"đi backend {b.name} (model {c.model}) cho tier {model_tier}")
            return c
        now = self.clock()
        soonest = min((b.cooldown_until - now for b in candidates), default=0.0)
        why = "; ".join(f"{b.name}: {b.cooldown_reason}" for b in candidates if b.cooldown_reason)
        raise TransientError(f"mọi backend đều đang nghỉ, thử lại sau {max(1, int(soonest))}s ({why})")
