from __future__ import annotations

from .bus import InMemoryBus
from .events import Envelope, Namespace, SharedContext


class Blackboard:
    """Đọc/ghi shared-context. Ghi đi qua bus nên được kiểm quyền owner."""
    def __init__(self, bus: InMemoryBus):
        self.bus = bus
        self._latest: dict[str, SharedContext] = {}
        bus.subscribe("shared-context", self._on)

    def _on(self, env: Envelope) -> None:
        sc = SharedContext.model_validate(env.payload)
        cur = self._latest.get(sc.namespace)
        if cur is None or sc.version > cur.version:
            self._latest[sc.namespace] = sc

    def write(self, actor: str, namespace: Namespace, content_ref: str, summary: str = "") -> SharedContext:
        v = (self._latest[namespace].version + 1) if namespace in self._latest else 1
        sc = SharedContext(namespace=namespace, version=v, content_ref=content_ref, summary=summary)
        self.bus.publish(Envelope(topic="shared-context", key=namespace, actor=actor, payload=sc.model_dump()))
        return sc

    def read(self, namespace: str) -> SharedContext | None:
        return self._latest.get(namespace)

    def snapshot(self) -> dict[str, SharedContext]:
        return dict(self._latest)
