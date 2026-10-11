"""Signed automation approvals, distinct from human signatures and agent text."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

ACTOR = "policy:studio"


def key_file() -> Path:
    return Path(os.environ.get("STUDIO_POLICY_KEY_FILE", str(Path(__file__).resolve().parents[2] / "state" / "policy.key")))


def _message(data: dict[str, Any]) -> bytes:
    return json.dumps({k: v for k, v in data.items() if k != "signature"}, sort_keys=True, ensure_ascii=False).encode()


def sign(data: dict[str, Any]) -> dict[str, Any]:
    path = key_file(); path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream: stream.write(secrets.token_bytes(32))
    return {**data, "signature": hmac.new(path.read_bytes(), _message(data), hashlib.sha256).hexdigest()}


def verify(data: dict[str, Any]) -> bool:
    signature = data.get("signature")
    if not isinstance(signature, str) or not key_file().is_file(): return False
    try: message = _message(data)
    except (ValueError, TypeError): return False
    expected = hmac.new(key_file().read_bytes(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.encode("utf-8", errors="surrogatepass"), expected.encode("ascii"))
