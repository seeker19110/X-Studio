"""Private upload checkpoints. Session URIs never appear in snapshots, audit or errors."""
from __future__ import annotations

import json
from pathlib import Path

from .storage import connect, migrate


class UploadSessions:
    def __init__(self, db: Path):
        self.db = db
        migrate(db)

    def get(self, id: str) -> dict | None:
        with connect(self.db) as con:
            row = con.execute('SELECT data FROM upload_sessions WHERE id=?', (id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, id: str, value: dict) -> None:
        with connect(self.db) as con:
            con.execute('INSERT OR REPLACE INTO upload_sessions VALUES (?,?)', (id, json.dumps(value)))
