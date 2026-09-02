from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR, SKILLS_DIR = ROOT / "agents", ROOT / "skills"
_FM = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

@dataclass
class AgentSpec:
    id: str
    block: str
    model_tier: str
    reads: list[str]
    writes: list[str]
    context_namespace_write: str | list[str] | None
    skills: list[str]
    budget_tokens_per_task: int
    max_retries: int
    timeout_minutes: int
    prompt: str
    version: int = 1  # prompt là code: tăng mỗi khi nội dung prompt đổi
    skills_core: list[str] = field(default_factory=list)  # skill phụ, chỉ nạp quy trình + checklist
    skill_text: str = field(default="")
    skill_core_text: str = field(default="")

    @property
    def all_skills(self) -> list[str]:
        return [*self.skills, *self.skills_core]

    @property
    def namespaces_write(self) -> list[str]:
        ns = self.context_namespace_write
        return [] if ns is None else ([ns] if isinstance(ns, str) else list(ns))

    def system_prompt(self) -> str:
        out = f"{self.prompt}\n\n# Skills\n{self.skill_text}"
        if self.skill_core_text:
            out += ("\n\n# Skills phụ (chỉ quy trình + checklist)\n"
                    "Bản rút gọn: bạn vẫn phải đạt checklist bên dưới, nhưng KHÔNG sở hữu các lĩnh vực này — "
                    "phần chuyên sâu thuộc agent chủ quản, cần chi tiết thì hỏi qua topic thay vì tự quyết.\n\n"
                    f"{self.skill_core_text}")
        return out

def _split(text: str) -> tuple[dict, str]:
    m = _FM.match(text)
    if not m:
        raise ValueError("thiếu front matter")
    return yaml.safe_load(m.group(1)), text[m.end():]

CORE_SECTIONS = ("## Quy trình", "## Checklist")  # phần bắt buộc của mọi skill


def load_skill(name: str, core_only: bool = False) -> str:
    """Toàn văn skill, hoặc chỉ phần lõi (H1 + quy trình + checklist) khi `core_only`."""
    p = SKILLS_DIR / f"{name}.md"
    _, body = _split(p.read_text(encoding="utf-8"))
    body = body.strip()
    if not core_only:
        return body
    parts = re.split(r"\n(?=## )", body)
    keep = [parts[0].split("\n## ", 1)[0].strip()]
    keep += [s.strip() for s in parts if s.startswith(CORE_SECTIONS)]
    if len(keep) == 1:
        raise ValueError(f"skill {name}: không tìm thấy mục lõi {CORE_SECTIONS}")
    return "\n\n".join(keep)

def load_agents() -> dict[str, AgentSpec]:
    out: dict[str, AgentSpec] = {}
    for p in sorted(AGENTS_DIR.rglob("*.md")):
        fm, body = _split(p.read_text(encoding="utf-8"))
        spec = AgentSpec(prompt=body.strip(), **fm)
        dup = set(spec.skills) & set(spec.skills_core)
        if dup:
            raise ValueError(f"{spec.id}: skill vừa đầy đủ vừa rút gọn: {sorted(dup)}")
        spec.skill_text = "\n\n".join(load_skill(s) for s in spec.skills)
        spec.skill_core_text = "\n\n".join(load_skill(s, core_only=True) for s in spec.skills_core)
        out[spec.id] = spec
    return out
