"""Nạp agent và skill từ đĩa, dùng chung hai công ty (K3.6a của ADR gốc 0001).

**Hình dạng lệch ở đây: một bên là TẬP CON của bên kia.** `difflib` cho **0.429**, và ba hàm chỉ company có
(`_load_phases`, `owned_skills`, `reads_full`) đều là *thêm vào*, không phải *khác đi*: `_split`, `load_skill`,
`CORE_SECTIONS`, thân `load_agents` của studio giống company gần như từng ký tự. Studio không có `phases`
(ADR-0037), không có `context_namespace_read` (ADR-0020), không kiểm chủ quản skill (ADR-0008) — nó chưa cần,
chứ không phải nó làm khác. Nên core = bản company, và studio chỉ khai thêm cái nó có riêng.

Hai thứ mỗi công ty đưa vào, cả hai là **dữ liệu**, không phải nhánh `if`:

1. **`spec_cls`** — lớp `AgentSpec` của công ty. Studio có một trường core không được biết: `tools` (toolset chỉ
   đọc bật cho agent, ADR-0007 của studio; company cấp tool theo cách khác). Vì `load_agents` dựng spec từ front
   matter, nó phải dựng ĐÚNG lớp của công ty, nếu không trường ấy rơi mất. Cùng lý do `envelope_cls` ở bus.
2. **`check_owners`** — mặc định `True` (luật ADR-0008 của company: mọi skill trên đĩa phải có ít nhất một agent
   nạp nó đầy đủ). **Studio truyền `False`, và đó là một sự thật đo được, không phải khẩu vị**: `skills/` của
   studio hiện có ba skill không agent nào nạp đầy đủ — `content-policy`, `cost-estimation`, `finops`. Bật cổng
   ấy cho studio là làm studio đỏ ngay lần nạp đầu. Ba skill ấy là một khoản nợ có thật của studio; chỗ trả nợ
   là `Studio-creators/agents/`, không phải một tham số ở đây.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml

__all__ = ["CORE_SECTIONS", "AgentSpec", "Phase", "load_agents", "load_skill", "split_front_matter"]

_FM = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
#: ADR-0008: phần bắt buộc của mọi skill — cũng là phần duy nhất còn lại ở bản rút gọn.
CORE_SECTIONS = ("## Quy trình", "## Checklist")


@dataclass
class Phase:
    """Skill nạp THÊM cho một pha của agent (ADR-0037).

    `skills`/`skills_core` ở cấp agent nạp ở **mọi** pha; phần khai trong `phases.<tên>` chỉ nạp khi lượt chạy
    khai đúng pha đó (route khai `phase=`, hoặc `stack` của ticket với route sửa code). Một agent gộp nhiều vai
    cũ vì thế không phải mang toàn bộ skill của mọi vai vào mọi lượt — đó là rủi ro "prompt loãng" của ADR-0037.
    """
    skills: list[str] = field(default_factory=list)
    skills_core: list[str] = field(default_factory=list)


@dataclass
class AgentSpec:
    id: str
    block: str
    model_tier: str
    reads: list[str]
    writes: list[str]
    context_namespace_write: str | list[str] | None  # một hoặc nhiều namespace (ADR-0006)
    skills: list[str]
    budget_tokens_per_task: int
    max_retries: int
    timeout_minutes: int
    prompt: str
    version: int = 1  # ADR-0004: tăng mỗi khi nội dung prompt đổi
    # Đường dẫn THẬT của file nguồn, tương đối gốc công ty (`agents/<thư mục>/<id>.md`), do `load_agents` đặt.
    # Không ghép lại từ `block`: `block` là NHÃN nghiệp vụ, không phải tên thư mục — `supervisor.md` khai
    # `block: supervision` mà nằm ở `agents/supervisor/`, nên mọi chỗ ghép `agents/{block}/{id}.md` đều trỏ
    # vào file không tồn tại (bản dẫn xuất bảo người ta "sửa nguồn" ở chỗ trống, và `keeper.drift` phép (a)
    # mù hẳn với agent đó). Ai cần đường dẫn nguồn thì đọc trường này.
    source_rel: str = ""
    skills_core: list[str] = field(default_factory=list)  # ADR-0008: skill phụ, chỉ nạp quy trình + checklist
    skill_text: str = field(default="")
    skill_core_text: str = field(default="")
    # ADR-0020: blackboard theo vai trò. None = đọc toàn văn mọi namespace (như trước); danh sách = chỉ các namespace này
    # (cộng namespace mình sở hữu) mang `content`, namespace khác chỉ còn `summary` + `content_ref`.
    context_namespace_read: list[str] | None = None
    max_input_chars: int | None = None  # trần prompt riêng của agent, không vượt trần chung llm.yaml (ADR-0020)
    phases: dict[str, Phase] = field(default_factory=dict)  # ADR-0037: skill theo pha
    # tên pha → (toàn văn skill của pha, phần "skill phụ" của LƯỢT đó). Phần phụ tính sẵn theo pha vì skill được
    # pha nạp đầy đủ phải BIẾN MẤT khỏi bản rút gọn cấp agent — gửi cả hai là gửi cùng một skill hai lần.
    _phase_text: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def all_skills(self) -> list[str]:
        """Hợp của skill cấp agent và mọi pha (thứ tự khai, không trùng)."""
        names = [*self.skills, *self.skills_core]
        for ph in self.phases.values():
            names += [*ph.skills, *ph.skills_core]
        return list(dict.fromkeys(names))

    @property
    def owned_skills(self) -> list[str]:
        """Skill được agent này nạp ĐẦY ĐỦ ở đâu đó (cấp agent hoặc một pha) — dùng để kiểm chủ quản (ADR-0008)."""
        names = list(self.skills)
        for ph in self.phases.values():
            names += ph.skills
        return list(dict.fromkeys(names))

    def reads_full(self, namespace: str) -> bool:
        """Agent có được đọc toàn văn namespace này không (ADR-0020)."""
        if self.context_namespace_read is None: return True
        return namespace in self.context_namespace_read or namespace in self.namespaces_write

    @property
    def namespaces_write(self) -> list[str]:
        ns = self.context_namespace_write
        return [] if ns is None else ([ns] if isinstance(ns, str) else list(ns))

    def system_prompt(self, phase: str | None = None) -> str:
        """Prompt hệ thống của một lượt. `phase` = pha của lượt (ADR-0037): phần chung như cũ, skill của pha nối sau.

        Pha lạ ném lỗi thay vì im lặng trả prompt chung: route khai sai pha thì lượt chạy với đúng bộ skill của
        vai khác mà không ai thấy — `check_routes` bắt lúc khởi động, đây là hàng rào cuối lúc chạy."""
        if phase is not None and phase not in self.phases:
            raise KeyError(f"{self.id}: không có pha {phase!r} (có: {sorted(self.phases)})")
        phase_text, core_text = self._phase_text[phase] if phase is not None else ("", self.skill_core_text)
        out = f"{self.prompt}\n\n# Skills\n{self.skill_text}"
        if core_text:
            out += ("\n\n# Skills phụ (chỉ quy trình + checklist)\n"
                    "Bản rút gọn: bạn vẫn phải đạt checklist bên dưới, nhưng KHÔNG sở hữu các lĩnh vực này — "
                    "phần chuyên sâu thuộc agent chủ quản, cần chi tiết thì hỏi qua topic thay vì tự quyết.\n\n"
                    f"{core_text}")
        if phase_text:
            out += f"\n\n# Skills của pha {phase}\n{phase_text}"
        return out


S = TypeVar("S", bound=AgentSpec)


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Tách front matter YAML khỏi thân Markdown. Thiếu front matter là lỗi, không phải "coi như rỗng"."""
    m = _FM.match(text)
    if not m:
        raise ValueError("thiếu front matter")
    return yaml.safe_load(m.group(1)), text[m.end():]


def load_skill(skills_dir: Path, name: str, core_only: bool = False) -> str:
    """Toàn văn skill, hoặc chỉ phần lõi (H1 + quy trình + checklist) khi `core_only`.

    Phần lõi là thứ agent phải làm theo; phần bị cắt (tiêu chuẩn tham chiếu, quy tắc chi tiết, ví dụ)
    là kiến thức chuyên sâu chỉ agent chủ quản của skill cần nạp đầy đủ."""
    p = skills_dir / f"{name}.md"
    _, body = split_front_matter(p.read_text(encoding="utf-8"))
    body = body.strip()
    if not core_only:
        return body
    parts = re.split(r"\n(?=## )", body)
    keep = [parts[0].split("\n## ", 1)[0].strip()]  # H1 "# Skill: <name>"
    keep += [s.strip() for s in parts if s.startswith(CORE_SECTIONS)]
    if len(keep) == 1:
        raise ValueError(f"skill {name}: không tìm thấy mục lõi {CORE_SECTIONS}")
    return "\n\n".join(keep)


def load_agents(agents_dir: Path, skills_dir: Path, spec_cls: type[S],
                check_owners: bool = True) -> dict[str, S]:
    """Nạp mọi agent. `check_owners`: mỗi skill trên đĩa phải có ít nhất một agent chủ quản (nạp đầy đủ) — ADR-0008.
    Skill chỉ xuất hiện ở `skills_core` khắp nơi thì phần Quy tắc/Ví dụ của nó không bao giờ đến tay model nào."""
    out: dict[str, S] = {}
    for p in sorted(agents_dir.rglob("*.md")):
        fm, body = split_front_matter(p.read_text(encoding="utf-8"))
        fm["phases"] = {str(name): Phase(**(cfg or {})) for name, cfg in (fm.get("phases") or {}).items()}
        spec = spec_cls(prompt=body.strip(), **fm)
        spec.source_rel = p.relative_to(agents_dir.parent).as_posix()
        dup = set(spec.skills) & set(spec.skills_core)
        if dup:
            raise ValueError(f"{spec.id}: skill vừa đầy đủ vừa rút gọn: {sorted(dup)}")
        spec.skill_text = "\n\n".join(load_skill(skills_dir, s) for s in spec.skills)
        spec.skill_core_text = "\n\n".join(load_skill(skills_dir, s, core_only=True) for s in spec.skills_core)
        _load_phases(skills_dir, spec)
        out[spec.id] = spec
    if check_owners:
        # ADR-0037: skill chỉ khai ở một pha VẪN có chủ quản — nó được nạp đầy đủ ở lượt của pha đó.
        owned = {sk for spec in out.values() for sk in spec.owned_skills}
        orphan = sorted({p.stem for p in skills_dir.glob("*.md")} - owned)
        if orphan:
            raise ValueError("skill không có agent chủ quản (chỉ được nạp rút gọn nên phần chuyên sâu bị bỏ): "
                             + ", ".join(orphan))
    return out


def _load_phases(skills_dir: Path, spec: AgentSpec) -> None:
    """Nạp `phases` của một agent: kiểm trùng trong CÙNG một cấp rồi tính sẵn văn bản skill cho từng pha."""
    for name, ph in spec.phases.items():
        dup = set(ph.skills) & set(ph.skills_core)
        if dup:
            raise ValueError(f"{spec.id}[{name}]: skill vừa đầy đủ vừa rút gọn: {sorted(dup)}")
        over = set(ph.skills) & set(spec.skills)
        if over:
            raise ValueError(f"{spec.id}[{name}]: skill đã nạp đầy đủ ở cấp agent, khai lại ở pha: {sorted(over)}")
        # Pha thắng: skill pha nạp đầy đủ thì bỏ bản rút gọn cấp agent (vd. `observability` của builder).
        core = [s for s in spec.skills_core if s not in set(ph.skills)] + list(ph.skills_core)
        spec._phase_text[name] = ("\n\n".join(load_skill(skills_dir, s) for s in ph.skills),
                                  "\n\n".join(load_skill(skills_dir, s, core_only=True) for s in core))
