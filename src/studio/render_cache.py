"""Content-addressed checkpoints for expensive media calls (single orchestrator).

Only hashes and result metadata are persisted, never prompts or credentials. Each
successful component is committed before the next one starts. Outputs are copies,
so an editor changing a scene cannot corrupt the reusable cache entry.
"""
from __future__ import annotations

import hashlib
import json
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

    def produce(self, key: Any, out: Path, generate: Callable[[Path], MediaResult], *,
                force: bool = False) -> tuple[MediaResult, bool]:
        self.root.mkdir(parents=True, exist_ok=True)
        name = fingerprint({"format": 1, "input": key})
        blob = self.root / (name + out.suffix)
        meta = self.root / (name + ".json")
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
