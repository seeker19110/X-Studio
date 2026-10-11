"""Bounded, observational storage accounting and admission for new projects.

Logical regular-file bytes are counted once per inode. No files are deleted and
no space is reserved: a successful scan is not a filesystem quota or a guarantee
that a future render fits. Incomplete scans fail closed only with a hard limit.
"""
from __future__ import annotations

import copy
import math
import os
import stat
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .media import MediaConfig


class StorageAdmissionError(ValueError):
    """New work cannot be admitted under the configured storage policy."""


class StorageMonitor:
    def __init__(self, cfg: MediaConfig, db: Path):
        settings = cfg.production.get('storage', {})
        if not isinstance(settings, Mapping):
            raise ValueError('production.storage must be a mapping')
        self.soft = self._integer(settings, 'soft_limit_bytes', 0)
        self.hard = self._integer(settings, 'hard_limit_bytes', 0)
        self.max_entries = self._integer(settings, 'max_scan_entries', 100000, minimum=1)
        interval = settings.get('scan_interval_s', 30)
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval < 0:
            raise ValueError('production.storage.scan_interval_s must be finite and nonnegative')
        if self.hard and self.soft > self.hard:
            raise ValueError('production.storage.soft_limit_bytes cannot exceed hard_limit_bytes')
        self.interval = float(interval)
        # Keep lexical paths so a configured symlink root is detected, not followed.
        self.output = Path(os.path.abspath(cfg.output_dir))
        self.uploads = Path(os.path.abspath(cfg.upload_dir or self.output / 'uploads'))
        database = Path(os.path.abspath(db))
        self.databases = [database, *(Path(str(database) + suffix) for suffix in ('-wal', '-shm', '-journal'))]
        self._cached: dict[str, Any] | None = None
        self._scanned_at = 0.0
        self._lock = threading.RLock()

    @staticmethod
    def _integer(settings: Mapping, key: str, default: int, minimum: int = 0) -> int:
        value = settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f'production.storage.{key} must be an integer >= {minimum}')
        return value

    def _category(self, path: Path) -> str:
        if path in self.databases:
            return 'database'
        if path.is_relative_to(self.output):
            if self.uploads.is_relative_to(self.output) and path.is_relative_to(self.uploads):
                return 'uploads'
            parts = path.relative_to(self.output).parts
            if any(part.startswith(('.pending-', '.copy-', '.concat-', '.finish-', '.motion-', '.finalize-', '.assemble-', '.studio-')) for part in parts):
                return 'temp'
            if any(part in {'.cache', '.segments'} for part in parts):
                return 'cache'
            return 'outputs'
        return 'uploads'

    def snapshot(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(force)

    def _snapshot(self, force: bool) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._cached is not None and now - self._scanned_at < self.interval:
            return copy.deepcopy(self._cached)
        categories = dict.fromkeys(('cache', 'temp', 'uploads', 'outputs', 'database'), 0)
        errors: list[str] = []
        seen: set[tuple[int, int]] = set()
        pending: list[tuple[Path, str]] = []
        scanned = skipped_symlinks = 0
        exhausted = False

        def error(message: str) -> None:
            if message not in errors:
                errors.append(message)

        def inspect(path: Path, label: str, *, root: bool = False, database: bool = False) -> None:
            nonlocal scanned, skipped_symlinks, exhausted
            if scanned >= self.max_entries:
                exhausted = True
                error('Storage scan entry limit reached.')
                return
            scanned += 1
            try:
                info = path.lstat()
            except FileNotFoundError:
                if not root:
                    error(f'An entry disappeared during the {label} scan.')
                return
            except OSError:
                error(f'Cannot inspect {label} storage.')
                return
            if stat.S_ISLNK(info.st_mode):
                skipped_symlinks += 1
                error('Symlinks are excluded from storage accounting.')
                return
            inode = (info.st_dev, info.st_ino)
            if inode in seen:
                return
            seen.add(inode)
            if stat.S_ISREG(info.st_mode):
                categories[self._category(path)] += info.st_size
            elif stat.S_ISDIR(info.st_mode):
                if database:
                    error('Expected a database file, found a directory.')
                else:
                    pending.append((path, label))

        # Database first gives DB bytes a stable category even when inside output.
        roots = [(path, 'database', True) for path in self.databases]
        roots.append((self.output, 'output', False))
        if not self.uploads.is_relative_to(self.output):
            roots.append((self.uploads, 'uploads', False))
        for path, label, database in roots:
            inspect(path, label, root=True, database=database)
            if exhausted:
                break
        while pending and not exhausted:
            directory, label = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        inspect(Path(entry.path), label)
                        if exhausted:
                            break
            except OSError:
                error(f'Cannot enumerate {label} storage.')
        total = sum(categories.values())
        complete = not errors
        reasons = []
        status = 'normal'
        if self.hard and (total >= self.hard or not complete):
            status = 'blocked'
            if total >= self.hard:
                reasons.append('Storage hard limit reached; new projects are paused.')
            if not complete:
                reasons.append('Storage scan incomplete; new projects require a complete scan.')
        elif not complete or (self.soft and total >= self.soft):
            status = 'warning'
            if not complete:
                reasons.append('Storage scan incomplete; reported usage is a lower bound.')
            if self.soft and total >= self.soft:
                reasons.append('Storage soft limit reached.')
        result = {'total_bytes': total, 'categories': categories, 'complete': complete, 'errors': errors,
                  'limits': {'soft_limit_bytes': self.soft, 'hard_limit_bytes': self.hard},
                  'status': status, 'reasons': reasons, 'scanned_entries': scanned,
                  'skipped_symlinks': skipped_symlinks}
        self._cached = result
        self._scanned_at = now
        return copy.deepcopy(result)

    def check_admission(self, *, force: bool = True) -> dict[str, Any]:
        """Refresh before admission; queued work may reuse a fresh blocked result.

        Disabled hard limits need only cached observation. A cached normal result
        never authorizes work when a hard limit is enabled.
        """
        with self._lock:
            blocked_fresh = (not force and self._cached is not None and self._cached['status'] == 'blocked'
                             and time.monotonic() - self._scanned_at < self.interval)
            result = self.snapshot(force=bool(self.hard) and not blocked_fresh)
            if result['status'] == 'blocked':
                raise StorageAdmissionError(' '.join(result['reasons']))
            return result
