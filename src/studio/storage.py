"""Versioned application tables and revision-checked ideas; event schemas stay independent."""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


@contextmanager
def connect(db: Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(db, timeout=30)
    try:
        with con: yield con
    finally: con.close()


def _initial(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS factory_commands (id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '', created REAL DEFAULT (unixepoch()))")
    con.execute('CREATE TABLE IF NOT EXISTS website_jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    con.execute('CREATE TABLE ideas(id TEXT PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL, '
                'deleted INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL)')


def _uploads(con: sqlite3.Connection) -> None:
    con.execute('CREATE TABLE upload_sessions(id TEXT PRIMARY KEY, data TEXT NOT NULL)')


MIGRATIONS = (_initial, _uploads)


def migrate(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with connect(db) as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('CREATE TABLE IF NOT EXISTS studio_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        row = con.execute("SELECT value FROM studio_meta WHERE key='schema_version'").fetchone()
        version = int(row[0]) if row else 0
        if not 0 <= version <= len(MIGRATIONS): raise ValueError('Unsupported application schema version')
        for upgrade in MIGRATIONS[version:]: upgrade(con)
        con.execute("INSERT OR REPLACE INTO studio_meta VALUES ('schema_version',?)", (str(len(MIGRATIONS)),))
        con.execute("INSERT OR IGNORE INTO studio_meta VALUES ('database_id',?)", (uuid4().hex,))


class ConflictError(ValueError):
    pass


class Idea(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    audience: str = Field(default='', max_length=300)
    hook: str = Field(default='', max_length=1000)
    format: Literal['long', 'short'] = 'long'
    notes: str = Field(default='', max_length=6000)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(), max_length=64)
    command_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    error: str | None = Field(default=None, max_length=2000)


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0, strict=True)
    idea: Idea | None


class IdeaStore:
    def __init__(self, db: Path):
        self.db = db
        migrate(db)

    def snapshot(self) -> dict:
        with connect(self.db) as con:
            identity = con.execute("SELECT value FROM studio_meta WHERE key='database_id'").fetchone()[0]
            rows = con.execute('SELECT data,revision FROM ideas WHERE deleted=0 ORDER BY updated DESC,id DESC').fetchall()
        return {'database_id': identity, 'ideas': [{**json.loads(data), 'revision': revision} for data, revision in rows]}

    @staticmethod
    def _limit(con: sqlite3.Connection) -> None:
        if con.execute('SELECT count(*) FROM ideas WHERE deleted=0').fetchone()[0] > 200:
            raise ValueError('Kho đã có 200 ý tưởng; xóa bớt trước khi thêm')

    def import_items(self, items: list[dict]) -> dict:
        if not isinstance(items, list) or len(items) > 200: raise ValueError('Import accepts at most 200 ideas')
        parsed = TypeAdapter(list[Idea]).validate_python(items)
        with connect(self.db) as con:
            con.execute('BEGIN IMMEDIATE')
            for idea in parsed:
                con.execute('INSERT OR IGNORE INTO ideas VALUES (?,?,1,0,?)',
                            (idea.id, idea.model_dump_json(), time.time()))
            self._limit(con)
        return self.snapshot()

    def apply(self, changes: list[dict]) -> dict:
        if not isinstance(changes, list) or not 1 <= len(changes) <= 200: raise ValueError('Expected 1–200 changes')
        parsed = TypeAdapter(list[Change]).validate_python(changes)
        ids = [c.id for c in parsed]
        if len(ids) != len(set(ids)) or any(c.idea and c.idea.id != c.id for c in parsed):
            raise ValueError('Idea IDs must match and be unique')
        revisions = {}
        with connect(self.db) as con:
            con.execute('BEGIN IMMEDIATE')
            for change in parsed:
                row = con.execute('SELECT revision FROM ideas WHERE id=?', (change.id,)).fetchone()
                revision = row[0] if row else 0
                if revision != change.revision:
                    raise ConflictError('Ý tưởng đã thay đổi ở tab khác. Tải lại và giữ bản nháp trước khi lưu lại.')
                revisions[change.id] = revision + 1
                con.execute('INSERT OR REPLACE INTO ideas VALUES (?,?,?,?,?)',
                            (change.id, change.idea.model_dump_json() if change.idea else '{}',
                             revision + 1, int(change.idea is None), time.time()))
            self._limit(con)
        return {**self.snapshot(), 'revisions': revisions}

    def convert(self, id: str, revision: int, request: dict) -> dict:
        change = Change(id=id, revision=revision, idea=None)
        command_id = uuid4().hex
        with connect(self.db) as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT data,revision,deleted FROM ideas WHERE id=?', (change.id,)).fetchone()
            if row is None or row[2] or row[1] != change.revision: raise ConflictError('Ý tưởng đã thay đổi; tải lại để chuyển thành dự án')
            idea = Idea.model_validate_json(row[0])
            if idea.command_id or idea.project_id: raise ConflictError('Ý tưởng đã được chuyển thành dự án')
            idea.command_id = command_id; idea.error = None
            con.execute("INSERT INTO factory_commands(id,kind,body) VALUES (?,'create',?)", (command_id, json.dumps(request)))
            con.execute('UPDATE ideas SET data=?,revision=revision+1,updated=? WHERE id=?',
                        (idea.model_dump_json(), time.time(), id))
        return {'id': command_id, **self.snapshot()}
