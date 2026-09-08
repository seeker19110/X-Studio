"""Bus của studio — cơ chế ở `xagents_core.bus`, dữ liệu ở `core.CORE` (K3.5b của ADR gốc 0001).

Bản trước K3.5b dài 61 dòng và chỉ làm hai việc: kiểm `required` của payload, và kiểm chủ namespace. Từ đây
studio nhận **toàn bộ** cơ chế của bus company: validate cả envelope theo JSON Schema, ACL topic, `latest()`,
`nullable_fields()`, `_notify_safely()`, và một `RLock`. Bảng ACL đo từ event thật — xem `core.py`.
"""
from __future__ import annotations

from typing import Any

from xagents_core.bus import BusError as BusError
from xagents_core.bus import InMemoryBus as CoreInMemoryBus
from xagents_core.bus import PermissionDenied as PermissionDenied
from xagents_core.bus import is_human as is_human
from xagents_core.bus import producer_allowed as _producer_allowed

from .core import CORE
from .core import HUMAN_TOPICS as HUMAN_TOPICS
from .core import OPEN_TOPICS as OPEN_TOPICS
from .core import TOPIC_PRODUCERS as TOPIC_PRODUCERS
from .events import Envelope

SCHEMA_DIR = CORE.schema_dir


def producer_allowed(topic: str, actor: str) -> bool:
    return _producer_allowed(CORE.topic_acl, topic, actor)


class InMemoryBus(CoreInMemoryBus[Envelope]):
    envelope_cls = Envelope

    def __init__(self, cfg: Any = CORE, enforce_owners: bool = True):
        # Thứ tự tham số theo core (`cfg` trước) — xem chú ở `company/bus.py`, cùng lý do MRO của K3.5c.
        super().__init__(cfg, enforce_owners=enforce_owners)
