"""Shim có RÀNG BUỘC: `studio.guard` = `xagents_core.guard` gắn sẵn `CORE` của phòng ban (K3.4).

Trước K3.4 studio KHÔNG có file này — bộ mẫu injection nằm lẫn trong `runner.py` (`INJECTION_PATTERNS`,
`has_injection`, `sanitize_text`, `sanitize_obj`). Đó là bản sao thứ hai của cùng một thiết kế, và nó đã trôi
khỏi bản company theo cả hai chiều; `xagents_core/guard.py` ghi đủ ba quyết định hợp nhất kèm số đo.

Ba điều studio ĐƯỢC NÂNG ở bước này, đều đổi hành vi có chủ ý:

1. **Chuẩn hoá ký tự vô hình trước khi so** (`normalize`). Bản cũ không có, nên `igno​re previous instructions`
   đi thẳng qua bộ lọc — một lỗ hổng thật, không phải khác biệt phong cách.
2. **Bảng mẫu rộng hơn 8 mẫu**: `[INST]`, `<<SYS>>`, `SYSTEM:` đầu dòng, `act as the system`, `new instructions:`,
   lệnh cho tool (`run the shell command rm -rf`), và hai biến thể tiếng Việt.
3. **Mẫu tiếng Việt chặt hơn**: bản cũ khớp `quên\\s+hướng dẫn` trần nên báo nhầm những câu hoàn toàn bình thường
   ("tôi quên hướng dẫn cài đặt rồi" — đo được 3/3 câu lành bị báo nhầm). Bản chung đòi hoặc từ chỉ lượng hoặc
   từ bổ nghĩa sau.

Hai mẫu RIÊNG của studio (`developer mode`, `jailbreak`) giữ nguyên qua `CORE.extra_injection_patterns`.

`sanitize_text`/`sanitize_obj` nay trả **danh sách tên mẫu** thay vì một con số; nơi gọi cần số thì `len(...)`.
Đổi vì audit `injection_sanitized` của company ghi được *mẫu nào* đã khớp, còn studio chỉ ghi "3 đoạn" — người
trực đọc log không biết chuyện gì đã xảy ra.

Xoá shim ở chân trời 3, xem `docs/DAC-TA-KICH-BAN-B.md` K3.a.
"""
from __future__ import annotations

import functools

from xagents_core.guard import LABEL as LABEL
from xagents_core.guard import PATTERNS as PATTERNS
from xagents_core.guard import ScanResult as ScanResult
from xagents_core.guard import compile_patterns
from xagents_core.guard import guard_payload as _guard_payload
from xagents_core.guard import is_external as _is_external
from xagents_core.guard import normalize as normalize
from xagents_core.guard import sanitize as _sanitize
from xagents_core.guard import sanitize_text as _sanitize_text
from xagents_core.guard import sanitize_tool_output as _sanitize_tool_output
from xagents_core.guard import scan as _scan
from xagents_core.guard import scan_obj as _scan_obj

from .core import CORE

_PATTERNS = compile_patterns(CORE.extra_injection_patterns)

scan = functools.partial(_scan, patterns=_PATTERNS)
scan_obj = functools.partial(_scan_obj, patterns=_PATTERNS)
sanitize = functools.partial(_sanitize, patterns=_PATTERNS)
sanitize_text = functools.partial(_sanitize_text, patterns=_PATTERNS)
sanitize_tool_output = functools.partial(_sanitize_tool_output, patterns=_PATTERNS)
guard_payload = functools.partial(_guard_payload, core=CORE, patterns=_PATTERNS)
is_external = functools.partial(_is_external, external_topics=CORE.external_topics)


def has_injection(text: str) -> bool:
    """Tên cũ của studio, giữ lại vì `_filter_comments` hỏi đúng câu hỏi này: lô bình luận thì bỏ TỪNG bình luận
    khớp, chứ không lọc nội dung của nó (bình luận đã bị chèn lệnh thì phần còn lại cũng không đáng trả lời)."""
    return not scan(text).clean


__all__ = ["LABEL", "PATTERNS", "ScanResult", "guard_payload", "has_injection", "is_external", "normalize",
           "sanitize", "sanitize_text", "sanitize_tool_output", "scan", "scan_obj"]
