"""`xagents_core.registry` — nạp agent/skill từ đĩa (K3.6a).

Công ty GIẢ ở đây là một cây thư mục tạm (`agents/`, `skills/`) chứ không phải `cfg` của `conftest.py`: registry
chỉ cần hai thư mục, và dựng chúng tại chỗ làm từng ca nói rõ nó phụ thuộc đúng cái gì. Không mượn `agents/`
của company hay studio — mã ở core thì ca ở core, và một ca đọc `agents/` thật sẽ đỏ theo mỗi lần sửa prompt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from xagents_core.registry import (
    AgentSpec,
    Phase,
    _load_phases,
    load_agents,
    load_skill,
    split_front_matter,
)

SKILL = "---\nten: {n}\n---\n# Skill: {n}\n\n## Bối cảnh\nphần chuyên sâu {n}\n\n## Quy trình\nb1 {n}\n\n## Checklist\n- [ ] {n}\n"


@dataclass
class SpecCoTools(AgentSpec):
    """Lớp con kiểu studio: một trường core không được biết. `load_agents` phải dựng ĐÚNG lớp này."""
    tools: list[str] = field(default_factory=list)


def _cong_ty(tmp_path: Path, agents: dict[str, str], skills: list[str]) -> tuple[Path, Path]:
    ad, sd = tmp_path / "agents", tmp_path / "skills"
    ad.mkdir(); sd.mkdir()
    for n in skills:
        (sd / f"{n}.md").write_text(SKILL.format(n=n), encoding="utf-8")
    for name, text in agents.items():
        (ad / f"{name}.md").write_text(text, encoding="utf-8")
    return ad, sd


def _agent(id_: str, **fm) -> str:
    base = {"id": id_, "block": "b", "model_tier": "light", "reads": [], "writes": [], "skills": [],
            "context_namespace_write": None, "budget_tokens_per_task": 100, "max_retries": 1, "timeout_minutes": 5}
    base.update(fm)
    dong = []
    for k, v in base.items():
        dong.append(f"{k}: {v!r}" if not isinstance(v, dict) else f"{k}: {v}")
    return "---\n" + "\n".join(dong).replace("'", '"') + "\n---\nThân prompt của " + id_ + "\n"


# ---------- front matter ----------

def test_split_front_matter_tach_yaml_va_than():
    fm, body = split_front_matter("---\nid: a\n---\nnội dung\n")
    assert fm == {"id": "a"} and body == "nội dung\n"


def test_split_front_matter_thieu_thi_bao_loi():
    with pytest.raises(ValueError, match="thiếu front matter"):
        split_front_matter("# không có front matter\n")


# ---------- load_skill ----------

def test_load_skill_toan_van_va_ban_rut_gon(tmp_path):
    _, sd = _cong_ty(tmp_path, {}, ["seo"])
    day_du = load_skill(sd, "seo")
    assert "phần chuyên sâu seo" in day_du and "## Quy trình" in day_du

    lo_i = load_skill(sd, "seo", core_only=True)
    assert lo_i.startswith("# Skill: seo")
    assert "## Quy trình" in lo_i and "## Checklist" in lo_i
    assert "phần chuyên sâu" not in lo_i, "bản rút gọn phải BỎ phần chuyên sâu, đó là toàn bộ lý do nó tồn tại"


def test_load_skill_thieu_muc_loi_thi_bao_loi(tmp_path):
    _, sd = _cong_ty(tmp_path, {}, [])
    (sd / "rong.md").write_text("---\nten: rong\n---\n# Skill: rong\n\n## Ví dụ\nchỉ có ví dụ\n", encoding="utf-8")
    with pytest.raises(ValueError, match="không tìm thấy mục lõi"):
        load_skill(sd, "rong", core_only=True)


# ---------- AgentSpec ----------

def test_namespaces_write_nhan_ca_none_mot_chuoi_va_danh_sach():
    def spec(ns):
        return AgentSpec(id="a", block="b", model_tier="light", reads=[], writes=[], context_namespace_write=ns,
                         skills=[], budget_tokens_per_task=1, max_retries=0, timeout_minutes=1, prompt="p")
    assert spec(None).namespaces_write == []
    assert spec("prd").namespaces_write == ["prd"]
    assert spec(["prd", "design"]).namespaces_write == ["prd", "design"]


def test_reads_full_none_la_doc_tat_ca_con_danh_sach_thi_cong_namespace_minh_ghi():
    s = AgentSpec(id="a", block="b", model_tier="light", reads=[], writes=[], context_namespace_write="prd",
                  skills=[], budget_tokens_per_task=1, max_retries=0, timeout_minutes=1, prompt="p")
    assert s.reads_full("bat-ky") is True                 # None = đọc toàn văn mọi namespace
    s.context_namespace_read = ["design"]
    assert s.reads_full("design") and s.reads_full("prd")  # prd: namespace mình ghi, luôn đọc được
    assert not s.reads_full("khac")


def test_all_skills_va_owned_skills_gop_moi_pha_khong_trung(tmp_path):
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"], skills_core=["y"],
                                             phases={"review": {"skills": ["z"], "skills_core": ["y"]}})},
                      ["x", "y", "z"])
    spec = load_agents(ad, sd, AgentSpec, check_owners=False)["a"]
    assert spec.all_skills == ["x", "y", "z"], "trùng chỉ tính một lần, giữ thứ tự khai"
    assert spec.owned_skills == ["x", "z"], "owned = nạp ĐẦY ĐỦ ở đâu đó, không tính skills_core"


# ---------- system_prompt ----------

def test_system_prompt_khong_pha_va_co_pha(tmp_path):
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"], skills_core=["y"],
                                             phases={"review": {"skills": ["z"]}})}, ["x", "y", "z"])
    spec = load_agents(ad, sd, AgentSpec, check_owners=False)["a"]

    chung = spec.system_prompt()
    assert "phần chuyên sâu x" in chung, "skill cấp agent nạp đầy đủ"
    assert "Skills phụ" in chung and "phần chuyên sâu y" not in chung, "skill phụ chỉ còn quy trình + checklist"
    assert "Skills của pha" not in chung

    pha = spec.system_prompt("review")
    assert "# Skills của pha review" in pha and "phần chuyên sâu z" in pha


def test_system_prompt_khong_co_skill_phu_thi_khong_in_muc_do(tmp_path):
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"])}, ["x"])
    assert "Skills phụ" not in load_agents(ad, sd, AgentSpec, check_owners=False)["a"].system_prompt()


def test_system_prompt_pha_la_nem_loi_chu_khong_im_lang_tra_prompt_chung(tmp_path):
    """Route khai sai pha thì lượt chạy với bộ skill của vai khác mà không ai thấy — đây là hàng rào cuối."""
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"])}, ["x"])
    spec = load_agents(ad, sd, AgentSpec, check_owners=False)["a"]
    with pytest.raises(KeyError, match="không có pha"):
        spec.system_prompt("khong-ton-tai")


# ---------- load_agents ----------

def test_load_agents_dung_dung_lop_con_cua_cong_ty(tmp_path):
    """Studio có trường `tools` mà core không được biết: dựng bằng `AgentSpec` core là làm rơi nó im lặng."""
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"], tools=["web"])}, ["x"])
    spec = load_agents(ad, sd, SpecCoTools, check_owners=False)["a"]
    assert type(spec) is SpecCoTools and spec.tools == ["web"]


def test_load_agents_doc_ca_thu_muc_con_va_giu_than_prompt(tmp_path):
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"])}, ["x"])
    (ad / "phu").mkdir()
    (ad / "phu" / "b.md").write_text(_agent("b", skills=["x"]), encoding="utf-8")
    got = load_agents(ad, sd, AgentSpec, check_owners=False)
    assert set(got) == {"a", "b"} and got["b"].prompt == "Thân prompt của b"


def test_skill_vua_day_du_vua_rut_gon_o_cap_agent_la_loi(tmp_path):
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"], skills_core=["x"])}, ["x"])
    with pytest.raises(ValueError, match="vừa đầy đủ vừa rút gọn"):
        load_agents(ad, sd, AgentSpec, check_owners=False)


def test_check_owners_bat_skill_khong_co_agent_chu_quan(tmp_path):
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"], skills_core=["mo-coi"])}, ["x", "mo-coi"])
    with pytest.raises(ValueError, match=r"không có agent chủ quản.*mo-coi"):
        load_agents(ad, sd, AgentSpec, check_owners=True)
    load_agents(ad, sd, AgentSpec, check_owners=False)   # tắt cổng thì nạp được — đường của studio


def test_check_owners_chap_nhan_skill_chi_khai_o_MOT_PHA(tmp_path):
    """ADR-0037: skill chỉ khai ở một pha VẪN có chủ quản — nó được nạp đầy đủ ở lượt của pha đó."""
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"], phases={"review": {"skills": ["z"]}})}, ["x", "z"])
    assert set(load_agents(ad, sd, AgentSpec, check_owners=True)) == {"a"}


# ---------- phases ----------

def test_pha_khai_trung_trong_cung_mot_cap_la_loi(tmp_path):
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", phases={"review": {"skills": ["x"], "skills_core": ["x"]}})}, ["x"])
    with pytest.raises(ValueError, match=r"a\[review\]: skill vừa đầy đủ vừa rút gọn"):
        load_agents(ad, sd, AgentSpec, check_owners=False)


def test_pha_khai_lai_skill_da_nap_day_du_o_cap_agent_la_loi(tmp_path):
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"], phases={"review": {"skills": ["x"]}})}, ["x"])
    with pytest.raises(ValueError, match="khai lại ở pha"):
        load_agents(ad, sd, AgentSpec, check_owners=False)


def test_pha_thang_bo_ban_rut_gon_cap_agent_cua_cung_skill(tmp_path):
    """Skill được pha nạp ĐẦY ĐỦ phải biến mất khỏi bản rút gọn cấp agent — gửi cả hai là gửi hai lần một skill."""
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills_core=["y"], phases={"review": {"skills": ["y"]}})}, ["y"])
    spec = load_agents(ad, sd, AgentSpec, check_owners=False)["a"]
    pha = spec.system_prompt("review")
    assert pha.count("## Quy trình\nb1 y") == 1 and "phần chuyên sâu y" in pha
    assert "phần chuyên sâu y" not in spec.system_prompt(), "không có pha thì vẫn chỉ là bản rút gọn"


def test_load_phases_goi_lai_duoc_tren_mot_spec_da_sua(tmp_path):
    """Đường mà test của company dùng: thay `phases` của một spec đã nạp rồi tính lại văn bản."""
    ad, sd = _cong_ty(tmp_path, {"a": _agent("a", skills=["x"])}, ["x", "z"])
    spec = load_agents(ad, sd, AgentSpec, check_owners=False)["a"]
    spec.phases = {"review": Phase(skills=["z"])}
    spec._phase_text = {}
    _load_phases(sd, spec)
    assert "phần chuyên sâu z" in spec.system_prompt("review")
