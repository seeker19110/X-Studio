"""Content-addressed checkpoints for expensive media calls (single orchestrator).

Only hashes and result metadata are persisted, never prompts or credentials. Each
successful component is committed before the next one starts. Outputs are copies,
so an editor changing a scene cannot corrupt the reusable cache entry.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .media import MediaError, MediaResult


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class RenderCache:
    def __init__(self, root: Path):
        self.root = root

    def blob_path(self, key: Any, suffix: str) -> Path:
        return self.root / (fingerprint({"format": 1, "input": key}) + suffix)

    def prune_segments(self, max_bytes: int, protected: set[Path]) -> None:
        """Bound committed scene media bytes; never evict active, foreign or symlink files.

        Only owned ffmpeg scene checkpoints are disposable. Paid media caches and
        user assets are outside this retention policy. Metadata overhead is excluded.
        """
        if not max_bytes:
            return
        entries = []
        for blob in self.root.glob("*.mkv"):
            meta = blob.with_suffix(".json")
            if not re.fullmatch(r"[0-9a-f]{64}", blob.stem) or blob.is_symlink() or meta.is_symlink():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                if data.get("provider") != "ffmpeg" or data.get("model") != "bounded-scene":
                    continue
                stat = blob.stat()
            except (OSError, ValueError, AttributeError):
                continue
            entries.append((stat.st_mtime_ns, blob, stat.st_size))
        active = sum(size for _, blob, size in entries if blob in protected)
        if active > max_bytes:
            raise MediaError(f"active segment media {active} bytes exceeds segment cache budget {max_bytes}; "
                             "increase render.segment_cache_max_bytes or split the manifest")
        total = sum(size for _, _, size in entries)
        for _, blob, size in sorted(entries):
            if total <= max_bytes:
                break
            if blob not in protected:
                try:
                    # Keep metadata if the large blob cannot be removed, so a later
                    # retention attempt can still identify and reclaim it.
                    blob.unlink()
                    blob.with_suffix(".json").unlink()
                except OSError as exc:
                    raise MediaError(f"cannot evict segment checkpoint {blob}: {exc}; "
                                     "check cache directory permissions and filesystem health") from exc
                total -= size

    def produce(self, key: Any, out: Path, generate: Callable[[Path], MediaResult], *,
                force: bool = False, materialize: bool = True) -> tuple[MediaResult, bool]:
        """Use materialize=False only for consumers that never modify the returned blob."""
        self.root.mkdir(parents=True, exist_ok=True)
        blob = self.blob_path(key, out.suffix)
        meta = blob.with_suffix(".json")
        result = None
        if not force:
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                if blob.stat().st_size > 0 and digest(blob) == data.pop("sha256"):
                    result = MediaResult(path=blob, **data)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                pass  # missing, interrupted, incompatible or corrupt checkpoint = cache miss
        hit = result is not None
        if result is None:
            with tempfile.TemporaryDirectory(prefix=".pending-", dir=self.root) as temp:
                candidate = Path(temp) / ("asset" + out.suffix)
                result = generate(candidate)
                if not result.path.is_file() or not result.path.stat().st_size:
                    raise MediaError("provider returned an empty or missing media file")
                data = asdict(result)
                data.pop("path")
                data["sha256"] = digest(result.path)
                checkpoint = Path(temp) / "metadata.json"
                checkpoint.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                result.path.replace(blob)
                checkpoint.replace(meta)  # commit marker comes last
        if not materialize:
            result.path = blob
            return result, hit
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.is_file() and digest(out) == digest(blob):
            result.path = out
            return result, hit
        # Use a unique temporary filename; replacement is atomic on Windows too.
        with tempfile.TemporaryDirectory(prefix=".copy-", dir=out.parent) as temp:
            target = Path(temp) / out.name
            shutil.copyfile(blob, target)
            target.replace(out)
        result.path = out
        return result, hit
