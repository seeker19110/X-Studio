"""Shim K3.2: mã ở `xagents_core.sandbox`. File này TỪNG là bản sao gần như nguyên văn của
`software-company/src/company/sandbox.py` với ghi chú "bản tạm, sẽ xoá ở K3.2" — nay bản sao đó không còn, cả hai
công ty dùng chung một module (ADR gốc 0001).

`sandbox_from_config` ở lại đây vì nó là chỗ duy nhất biết mình phục vụ studio: biến `STUDIO_SANDBOX*`, nguồn
`media.yaml render.sandbox`, và mặc định **`subprocess` chứ không `auto`** — ffmpeg/ffprobe nhận đường dẫn tuyệt
đối trải trên nhiều thư mục (output, font, thư mục tạm) mà container chỉ thấy `cwd` được mount, nên bật container
sau lưng người dùng là làm hỏng render chứ không phải làm chặt bảo mật.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

from xagents_core.sandbox import (
    SECRET_ENV,
    ContainerSandbox,
    Handle,
    Result,
    RunSpec,
    Sandbox,
    SandboxError,
    SubprocessSandbox,
    clean_env,
    sandbox_from_settings,
    sanitize_env,
)

__all__ = ["SECRET_ENV", "ContainerSandbox", "Handle", "Result", "RunSpec", "Sandbox", "SandboxError",
           "SubprocessSandbox", "clean_env", "sandbox_from_config", "sanitize_env"]


def sandbox_from_config(cfg: Any = None, which: Any = shutil.which) -> Sandbox:
    """`STUDIO_SANDBOX` env → `cfg.render["sandbox"]` → `subprocess` (xem docstring module: media KHÔNG auto)."""
    sec = (getattr(cfg, "render", None) or {}).get("sandbox") or {}
    mode = os.environ.get("STUDIO_SANDBOX") or str(sec.get("mode") or "") or "subprocess"
    runtime = os.environ.get("STUDIO_SANDBOX_RUNTIME") or str(sec.get("runtime") or "") or "docker"
    image = os.environ.get("STUDIO_SANDBOX_IMAGE") or str(sec.get("image") or "") or "python:3.12-slim"
    return sandbox_from_settings(mode, runtime, image, "STUDIO_SANDBOX", which)
