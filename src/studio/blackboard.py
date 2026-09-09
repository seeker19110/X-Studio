"""Blackboard của studio — cơ chế ở `xagents_core.blackboard` (K3.6b của ADR gốc 0001).

Bản trước K3.6b dài 30 dòng và chỉ giữ bản mới nhất mỗi namespace trong một `dict`. Từ đây studio nhận toàn bộ
cơ chế của blackboard company: khoá khi đánh version (hai agent cùng sở hữu một namespace chạy song song không
còn mất bản ghi), `content` toàn văn, `rehydrate()` dựng lại từ bus khi mở lại SQLite, `all()`/`overview()`, và
phân vùng theo dự án.

`store` để `None` như trước: studio chưa có artifact store, nên nhánh mirror không chạy. Phân vùng theo dự án
cũng chưa dùng — studio không truyền `project_id` ở đâu cả — nhưng `global_namespaces` vẫn khai `knowledge` cho
đúng nghĩa: khi nào studio phân vùng theo kênh thì nó đã đúng sẵn, không phải nhớ sửa.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from xagents_core.blackboard import Blackboard as CoreBlackboard
from xagents_core.blackboard import Scope as Scope

from .bus import InMemoryBus
from .core import CORE
from .events import Envelope, SharedContext


class Blackboard(CoreBlackboard[Envelope, SharedContext]):
    envelope_cls = Envelope
    context_cls = SharedContext

    def __init__(self, bus: InMemoryBus, store: Path | None = None, cfg: Any = CORE):
        super().__init__(cfg, bus, store)
