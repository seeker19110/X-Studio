"""`xagents_core` — lõi chung của mọi công ty AI trong X-Agents (ADR gốc `docs/adr/0001-loi-chung-xagents-core.md`).

Ở K3.0 package này còn **rỗng có chủ ý**: chỉ có khung, CI và `CoreConfig`. Mã thật chuyển sang theo bảy bước
K3.1–K3.7, mỗi bước một PR, để mỗi bước tự đứng được và `main` không bao giờ đỏ giữa chừng.

Luật của chỗ này, đọc trước khi thêm bất cứ gì:

1. **Core không biết tên công ty nào.** Mọi điểm khác nhau giữa `company` và `studio` đi qua `CoreConfig`.
   Một `if cfg.prefix == "COMPANY"` trong core là fork mọc lại dưới dạng câu điều kiện — nếu thấy mình sắp
   viết nó, thứ đang thiếu là một trường trong `CoreConfig`.
2. **Cơ chế ở core, nghĩa ở package.** Bus/llm/runner/guard là cơ chế. `Topic` Literal, `Task` vs `VideoBrief`,
   `tools_prompt` là nghĩa — chúng ở lại `company`/`studio`.
3. **Company là gốc.** Với mỗi module, bản company là bản vào core: nó dày hơn vì đã ăn nhiều sự cố thật.
   Studio được nâng theo, kể cả khi điều đó đổi hành vi studio — đổi có chủ ý thì ghi CHANGELOG, không lặng lẽ.
"""
from __future__ import annotations

from .config import CoreConfig, TopicACL

__version__ = "0.1.0"
__all__ = ["CoreConfig", "TopicACL", "__version__"]
