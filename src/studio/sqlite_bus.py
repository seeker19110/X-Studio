"""Bus bền vững SQLite của studio — cơ chế ở `xagents_core.sqlite_bus` (K3.5c của ADR gốc 0001).

Bản trước K3.5c ghi đĩa **không khoá** và tháo `_subs` ra để bảo đảm "ghi trước, báo sau"; bản core làm cùng
việc ấy bên trong một `RLock` và không cần mẹo tháo bảng. Studio vì thế nhận thêm: khoá,
`check_same_thread=False` (bus truyền được sang thread khác), `latest()` tìm trên index thay vì quét log,
`_persist_only` ghi đĩa, và `__del__` đóng kết nối. Xem docstring core cho ba quyết định hợp nhất.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from xagents_core.sqlite_bus import BUSY_TIMEOUT_S as BUSY_TIMEOUT_S
from xagents_core.sqlite_bus import DDL as DDL
from xagents_core.sqlite_bus import SQLiteBus as CoreSQLiteBus

from .bus import InMemoryBus
from .core import CORE
from .events import Envelope

_DDL = DDL  # tên cũ (có gạch dưới) mà test và script cũ nhập


class SQLiteBus(CoreSQLiteBus[Envelope], InMemoryBus):
    def __init__(self, path: str | Path | None = None, enforce_owners: bool = True, cfg: Any = CORE):
        super().__init__(cfg, path, enforce_owners=enforce_owners)
