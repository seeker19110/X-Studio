"""Shim: giữ tên `studio.routing`. Bản thật ở `xagents_core.routing` (K3.3d) — là bản COMPANY, nên studio được
nâng bốn điểm, liệt kê ở docstring module ấy; `TRANSIENT_PATTERNS`/`is_transient_error` đã xoá (dùng
`except TransientError`). Xoá shim ở chân trời 3, xem `docs/DAC-TA-KICH-BAN-B.md` K3.a."""
from xagents_core.routing import *  # noqa: F403
from xagents_core.routing import __all__  # noqa: F401
