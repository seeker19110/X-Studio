"""Registry của studio — cơ chế ở `xagents_core.registry` (K3.6a của ADR gốc 0001).

Hai chỗ studio khác company, cả hai đã đo:

- **`AgentSpec` của studio có thêm `tools`** (toolset chỉ đọc bật cho agent, ADR-0007 của studio: hiện chỉ
  `web`, cho `fact-checker` và `trend-researcher`). Company cấp tool theo cách khác nên core không biết trường
  này; `load_agents` dựng đúng lớp con nên nó không rơi mất.
- **`check_owners=False` mặc định.** Cổng ADR-0008 của company đòi mọi skill trên đĩa có ít nhất một agent nạp
  đầy đủ; `skills/` của studio hiện có **ba skill không đạt**: `content-policy`, `cost-estimation`, `finops`.
  Bật cổng ở đây là làm studio đỏ ngay lần nạp đầu — đó là một khoản nợ có thật, và chỗ trả nó là
  `Studio-creators/agents/`, không phải một tham số ở đây. `load_agents(check_owners=True)` gọi được bất cứ lúc
  nào để xem nợ còn không.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from xagents_core.registry import CORE_SECTIONS as CORE_SECTIONS
from xagents_core.registry import AgentSpec as CoreAgentSpec
from xagents_core.registry import Phase as Phase
from xagents_core.registry import load_agents as _load_agents
from xagents_core.registry import load_skill as _load_skill
from xagents_core.registry import split_front_matter as split_front_matter

from .core import CORE

_split = split_front_matter  # tên cũ (có gạch dưới) mà test và script cũ nhập

ROOT = CORE.root
AGENTS_DIR, SKILLS_DIR = CORE.agents_dir, CORE.skills_dir


@dataclass
class AgentSpec(CoreAgentSpec):
    tools: list[str] = field(default_factory=list)  # toolset chỉ đọc được bật cho agent (ADR-0007): hiện chỉ `web`


def load_skill(name: str, core_only: bool = False) -> str:
    return _load_skill(SKILLS_DIR, name, core_only)


def load_agents(check_owners: bool = False) -> dict[str, AgentSpec]:
    return _load_agents(AGENTS_DIR, SKILLS_DIR, AgentSpec, check_owners=check_owners)
