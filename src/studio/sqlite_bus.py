"""Bus bền vững trên SQLite: cùng interface với InMemoryBus, đủ cho một máy (self-hosted).
Mọi envelope append vào bảng `events`; mở lại là replay được theo topic/key — đây cũng là checkpoint để
tiếp tục sản xuất bị gián đoạn (provider timeout, tắt máy) đúng chỗ (ADR-0001)."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .bus import InMemoryBus
from .events import Envelope

_DDL = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  topic TEXT NOT NULL, key TEXT NOT NULL, actor TEXT NOT NULL, ts TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_topic_key ON events(topic, key);
"""


class SQLiteBus(InMemoryBus):
    def __init__(self, path: str | Path = "studio.sqlite", enforce_owners: bool = True):
        super().__init__(enforce_owners=enforce_owners)
        self.path = Path(path)
        self._db = sqlite3.connect(self.path)
        self._db.executescript(_DDL)
        self._seq = 0
        self._log = []
        for seq, body in self._db.execute("SELECT seq, body FROM events ORDER BY seq"):
            self._log.append(Envelope.model_validate_json(body)); self._seq = seq

    def _notify(self, subs, env: Envelope) -> None:
        for fn in list(subs.get(env.topic, [])) + list(subs.get("*", [])):
            fn(env)

    def publish(self, env: Envelope) -> Envelope:
        # Lớp cha validate + kiểm quyền. Tạm tháo subscriber để ghi đĩa TRƯỚC khi báo, tránh mất event khi handler ném lỗi.
        subs, self._subs = self._subs, defaultdict(list)
        try:
            super().publish(env)
        finally:
            self._subs = subs
        with self._db:
            cur = self._db.execute("INSERT INTO events(event_id, topic, key, actor, ts, body) VALUES (?,?,?,?,?,?)",
                                   (env.event_id, env.topic, env.key, env.actor, env.ts.isoformat(), env.model_dump_json()))
            self._seq = cur.lastrowid or self._seq
        self._notify(subs, env)
        return env

    def poll(self) -> list[Envelope]:
        """Nạp event do tiến trình KHÁC ghi vào cùng file (gate CLI, human publish) và báo subscriber như event mới."""
        rows = self._db.execute("SELECT seq, body FROM events WHERE seq > ? ORDER BY seq", (self._seq,)).fetchall()
        new: list[Envelope] = []
        for seq, body in rows:
            env = Envelope.model_validate_json(body)
            self._seq = seq; self._log.append(env); new.append(env)
            self._notify(self._subs, env)
        return new

    def replay(self, topic: str | None = None, key: str | None = None) -> Iterable[Envelope]:
        q, args, conds = "SELECT body FROM events", [], []
        if topic: conds.append("topic = ?"); args.append(topic)
        if key: conds.append("key = ?"); args.append(key)
        if conds: q += " WHERE " + " AND ".join(conds)
        for (body,) in self._db.execute(q + " ORDER BY seq", args):
            yield Envelope.model_validate_json(body)

    def close(self) -> None:
        self._db.close()
