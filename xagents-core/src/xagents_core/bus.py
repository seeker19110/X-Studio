"""Bus chung của hai công ty (K3.5b của ADR gốc 0001).

**Vì sao bước này khác K3.5a.** `difflib` trên hai file `bus.py` cho **0.06** — gần như không có gì chung về
mặt văn bản. Nhưng con số ấy không nói "hai bus khác nhau về bản chất"; nó nói **bus studio 61 dòng chưa làm
phần lớn việc mà bus company 206 dòng đã làm**: không validate toàn bộ JSON Schema, không có ACL topic, không
có `latest`, không khoá, không `_notify_safely`. Lệch vì MỘT BÊN THIẾU — khác hẳn K3.5a, nơi lệch vì mỗi miền
có trường riêng. Nên ở đây core giữ **toàn bộ cơ chế**, và cái mỗi công ty đưa vào là **dữ liệu**: `CoreConfig`.

Điều đó có một hệ quả không thoải mái phải nói thẳng: studio nhận một lớp kiểm quyền **nó chưa từng chạy**.
Bảng `topic_acl` của studio vì thế không viết từ front matter `writes` của agent (thiếu mọi actor là CODE:
`renderer`, `desk`, `orchestrator`, `adapter:youtube`, `chapters`) mà **đo từ 76 cặp `(actor, topic)` thật**
mà suite studio phát ra — xem `studio/core.py`.

`_extra_publish_checks` là điểm mở duy nhất: company có một luật không suy ra được từ bảng nào (`audit-log` mở
cho mọi actor, nhưng `action="gate.decide"` chỉ người ghi). Một luật riêng của một công ty thì là một hook, chứ
không phải một câu `if env.topic == ...` trong core — core không được biết tên topic của ai.
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any, Generic, TypeVar

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from .config import CoreConfig, TopicACL
from .events import Envelope

__all__ = ["BusError", "InMemoryBus", "PermissionDenied", "is_human", "producer_allowed"]

#: Lớp `Envelope` của công ty. Bus generic theo nó chứ không viết cứng `Envelope`: nếu không, `replay()` và
#: `latest()` của company trả về `Envelope` CORE, và mọi nơi nhận kết quả mất `topic: Topic` — cùng lý do
#: `child()` phải dùng `type(self)` ở K3.5a, chỉ khác là ở đây nó lộ ra qua mypy chứ không qua test.
E = TypeVar("E", bound=Envelope)


class BusError(Exception): ...
class PermissionDenied(BusError): ...


def is_human(actor: str) -> bool:
    """`human` hoặc `human:<tên>`. Cả hai công ty dùng đúng quy ước này cho actor là NGƯỜI."""
    return actor == "human" or actor.startswith("human:")


def producer_allowed(acl: TopicACL, topic: str, actor: str) -> bool:
    """Actor này có được phát topic kia không. Hàm thuần, không đụng bus — để test được một mình."""
    if topic in acl.open_topics: return True
    if is_human(actor): return topic in acl.human_topics
    return actor in acl.producers.get(topic, frozenset())


class InMemoryBus(Generic[E]):
    """Bus tối giản: partition theo key, validate payload, subscriber theo topic.
    Thay bằng Redis Streams / Kafka bằng cách giữ nguyên interface publish/subscribe/replay.

    `publish` giữ một RLock: subscriber (delivery-lead, supervisor, orchestrator) chạy tuần tự dù nhiều thread
    gọi model song song (ADR-0012); handler được phép publish lồng nhau (RLock)."""

    #: Lớp con đặt lại; core tự ghi audit nên phải dựng envelope ĐÚNG LỚP CON, nếu không audit của bus tụt về
    #: `Envelope` core và mất `topic: Topic` — cùng cái bẫy `child()` ở K3.5a.
    envelope_cls: type[E]

    def __init__(self, cfg: CoreConfig, enforce_owners: bool = True):
        self.cfg = cfg
        self._lock = threading.RLock()
        self._log: list[E] = []
        self._subs: dict[str, list[Callable[[E], None]]] = defaultdict(list)
        self.enforce_owners = enforce_owners
        self._schemas = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in cfg.schema_dir.glob("*.json")}
        self._validators = {t: Draft202012Validator(s, format_checker=FormatChecker()) for t, s in self._schemas.items()}
        self._payload_validators = {t: Draft202012Validator(s["properties"]["payload"], format_checker=FormatChecker())
                                    for t, s in self._schemas.items()}

    def _check(self, topic: str, validator: Draft202012Validator | None, data: dict[str, Any]) -> None:
        if validator is None:
            raise BusError(f"không có schema cho topic {topic}")
        errs = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
        if errs:
            detail = "; ".join(f"{'/'.join(str(x) for x in e.absolute_path) or '$'}: {e.message}" for e in errs[:5])
            raise BusError(f"{topic} không hợp lệ theo JSON Schema: {detail}")

    def nullable_fields(self, topic: str) -> frozenset[str]:
        """Trường ở tầng đầu của payload mà schema cho phép giá trị `null`.

        Dùng để nhận ra một kiểu trượt cố hữu của model: nó muốn nói "không đo được" nhưng viết CHUỖI `"null"`
        thay vì JSON `null`. Chỉ những trường được liệt kê ở đây mới được sửa (xem `normalize_nulls`)."""
        props = (self._schemas.get(topic, {}).get("properties", {}).get("payload", {}).get("properties", {}))
        out = set()
        for name, spec in props.items():
            if not isinstance(spec, dict): continue
            t = spec.get("type")
            alts = spec.get("anyOf") or spec.get("oneOf") or []
            enum = spec.get("enum") or []
            if t == "null" or (isinstance(t, list) and "null" in t) \
                    or any(isinstance(x, dict) and x.get("type") == "null" for x in alts) \
                    or None in enum:
                out.add(name)
        return frozenset(out)

    def validate(self, topic: str, payload: dict[str, Any]) -> None:
        """Kiểm payload theo pydantic model (nếu có) và TOÀN BỘ JSON Schema của topic (type, enum, required...);
        ném BusError. Schema là nguồn sự thật; pydantic là lớp tiện dụng cho code."""
        model: Any = self.cfg.payload_models.get(topic)
        if model is not None:
            try:
                model.model_validate(payload)
            except ValidationError as e:
                raise BusError(f"payload không hợp lệ cho {topic}: {e}") from e
        self._check(topic, self._payload_validators.get(topic), payload)

    def validate_envelope(self, env: E) -> None:
        """Kiểm cả envelope (event_id, key, actor, ts, schema_version, correlation/causation) theo schema topic."""
        self._check(env.topic, self._validators.get(env.topic), json.loads(env.model_dump_json()))

    def _audit(self, action: str, evidence: dict[str, Any]) -> E:
        return self.envelope_cls(topic="audit-log", key="bus", actor="bus", payload={
            "actor": "bus", "action": action, "evidence": json.dumps(evidence, ensure_ascii=False)})

    def _deny(self, env: E, reason: str) -> None:
        """Từ chối publish: ghi audit (actor=bus) rồi ném PermissionDenied — vượt quyền phải hiện ra, không im lặng."""
        self.publish(self._audit("publish_denied",
                                 {"topic": env.topic, "key": env.key, "actor": env.actor, "reason": reason}))
        raise PermissionDenied(reason)

    def _extra_publish_checks(self, env: E) -> None:
        """Luật riêng của một công ty, chạy sau validate và TRƯỚC kiểm ACL. Mặc định không có."""

    def _check_publish(self, env: E) -> None:
        """Validate payload + envelope và kiểm quyền producer; dùng chung cho mọi bus (bộ nhớ, SQLite)."""
        self.validate(env.topic, env.payload)
        self.validate_envelope(env)
        if not self.enforce_owners: return
        self._extra_publish_checks(env)
        acl = self.cfg.topic_acl
        if env.topic == "shared-context":
            ns = env.payload["namespace"]
            if env.actor not in self.cfg.namespace_owners.get(ns, set()):
                raise PermissionDenied(f"{env.actor} không được ghi namespace {ns}")
        elif not producer_allowed(acl, env.topic, env.actor):
            who = "người" if is_human(env.actor) else "agent"
            self._deny(env, f"{who} {env.actor} không được phát topic {env.topic} "
                            f"(producer hợp lệ: {sorted(acl.human_topics) if is_human(env.actor) else sorted(acl.producers.get(env.topic, ()))})")

    def _notify(self, subs: dict[str, list[Callable[[E], None]]], env: E) -> None:
        for fn in list(subs.get(env.topic, [])) + list(subs.get("*", [])):
            fn(env)

    def _persist_only(self, env: E) -> E:
        """Ghi log nhưng không báo subscriber: audit về handler hỏng không được đi qua chính handler đó."""
        self._check_publish(env)
        with self._lock: self._log.append(env)
        return env

    def _notify_safely(self, env: E, reraise: bool = False) -> None:
        """Báo MỌI subscriber dù một handler ném lỗi: event đã ghi rồi, subscriber sau (supervisor, orchestrator)
        không được mất nó — nếu không, trạng thái lúc chạy khác trạng thái dựng lại từ log (poll/replay báo đủ).
        Lỗi ghi audit `subscriber_error`; `reraise=True` (publish) ném lại lỗi đầu tiên cho người phát biết."""
        first: Exception | None = None
        for fn in list(self._subs.get(env.topic, [])) + list(self._subs.get("*", [])):
            try:
                fn(env)
            except Exception as e:  # mọi lỗi handler đều phải hiện ra audit, không nuốt im lặng
                first = first or e
                self._persist_only(self._audit("subscriber_error", {
                    "event_id": env.event_id, "topic": env.topic, "key": env.key,
                    "handler": getattr(fn, "__qualname__", repr(fn)), "error": str(e)[:300]}))
        if reraise and first is not None: raise first

    def publish(self, env: E) -> E:
        self._check_publish(env)
        with self._lock:
            self._log.append(env)
            self._notify_safely(env, reraise=True)
        return env

    def subscribe(self, topic: str, fn: Callable[[E], None]) -> None:
        self._subs[topic].append(fn)

    def replay(self, topic: str | None = None, key: str | None = None) -> Iterable[E]:
        with self._lock:
            snapshot = list(self._log)  # thread khác có thể publish trong lúc duyệt
        for e in snapshot:
            if (topic is None or e.topic == topic) and (key is None or e.key == key):
                yield e

    def latest(self, topic: str, key: str) -> E | None:
        """Event mới nhất của một (topic, key). Tách riêng khỏi `replay` vì đây là đường nóng: orchestrator hỏi
        "bản draft/PR/RC gần nhất" cho gần như mọi event, và dựng cả danh sách chỉ để lấy phần tử cuối là O(N)
        mỗi lần — trên bus bền vững còn kèm parse lại từng envelope."""
        with self._lock:
            for e in reversed(self._log):
                if e.topic == topic and e.key == key:
                    return e
        return None

    def __len__(self) -> int:
        return len(self._log)
