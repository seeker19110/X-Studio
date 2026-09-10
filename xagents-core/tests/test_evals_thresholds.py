"""p3.3: cơ chế cổng điểm eval (`Threshold`, `load_thresholds`, `check_thresholds`) ở CORE.

Trước bản vá này cơ chế chỉ có ở `company.evals`, nên `studio.evals` không có cổng nào cho điểm chấm: 14/20 agent
của repo có ca chấm sai nội dung mà CI vẫn xanh. Theo ADR-0001 (core giữ cơ chế, package giữ nghĩa) cơ chế lên core,
còn SỐ và ĐƯỜNG DẪN `evals/thresholds.yaml` ở lại từng công ty — core không được hằng hoá đường dẫn của một công ty.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from xagents_core.evals import Threshold, check_thresholds, load_thresholds
from xagents_core.llm import LLMError


@dataclass(frozen=True)
class _Outcome:
    """Duck-typed: core chỉ cần ba thứ này. `company._AgentOutcome` còn `gate_ok`/`cases_ok` — nghĩa riêng
    của company, không lên core; ca test này chính là bằng chứng Protocol không đòi hỏi chúng."""
    agent_id: str
    passed: int
    total: int


def _write(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    return p


# ---------- load_thresholds ----------

def test_load_thresholds_doc_duoc_file_that(tmp_path: Path) -> None:
    p = _write(tmp_path / "t.yaml", yaml.safe_dump({"qa": {"min_pass_ratio": 0.95, "cases": 2}}))
    assert load_thresholds(p) == {"qa": Threshold(min_pass_ratio=0.95, cases=2)}


def test_load_thresholds_file_khong_ton_tai_tra_rong(tmp_path: Path) -> None:
    assert load_thresholds(tmp_path / "khong-co.yaml") == {}


def test_load_thresholds_file_rong_tra_rong(tmp_path: Path) -> None:
    assert load_thresholds(_write(tmp_path / "t.yaml", "")) == {}


def test_load_thresholds_khong_phai_mapping_nem_llmerror(tmp_path: Path) -> None:
    p = _write(tmp_path / "t.yaml", "- qa\n- builder\n")
    with pytest.raises(LLMError, match="mapping"):
        load_thresholds(p)


def test_load_thresholds_thieu_truong_nem_llmerror_neu_ten_agent(tmp_path: Path) -> None:
    p = _write(tmp_path / "t.yaml", yaml.safe_dump({"qa": {"cases": 2}}))
    with pytest.raises(LLMError, match="qa"):
        load_thresholds(p)


def test_load_thresholds_gia_tri_khong_phai_so_nem_llmerror(tmp_path: Path) -> None:
    p = _write(tmp_path / "t.yaml", yaml.safe_dump({"qa": {"min_pass_ratio": "cao", "cases": 2}}))
    with pytest.raises(LLMError, match="không phải số"):
        load_thresholds(p)


def test_load_thresholds_sai_cu_phap_yaml_nem_llmerror(tmp_path: Path) -> None:
    p = _write(tmp_path / "t.yaml", "qa: [\n")
    with pytest.raises(LLMError, match="YAML"):
        load_thresholds(p)


# ---------- check_thresholds ----------

def test_check_duoi_san_thi_fail() -> None:
    th = {"qa": Threshold(min_pass_ratio=0.95, cases=2)}
    fails = check_thresholds([_Outcome("qa", 1, 2)], th)  # 0.50 < 0.95
    assert len(fails) == 1 and "FAIL qa" in fails[0] and "0.50" in fails[0]


def test_check_bang_san_thi_dat() -> None:
    th = {"qa": Threshold(min_pass_ratio=0.95, cases=2)}
    assert check_thresholds([_Outcome("qa", 2, 2)], th) == []


def test_check_bo_ca_bi_thu_nho_thi_fail() -> None:
    th = {"qa": Threshold(min_pass_ratio=0.95, cases=2)}
    fails = check_thresholds([_Outcome("qa", 1, 1)], th)  # 1/1 = 1.00 nhưng chỉ còn 1 ca
    assert len(fails) == 1 and "bộ ca bị thu nhỏ" in fails[0]


def test_check_agent_khong_co_nguong_thi_khong_ap() -> None:
    th = {"qa": Threshold(min_pass_ratio=0.95, cases=2)}
    assert check_thresholds([_Outcome("editor", 0, 2)], th) == []


def test_check_total_0_thi_khong_ap_khong_do_oan() -> None:
    """Agent chưa có bộ ca: `total == 0` → không chia 0, không đỏ oan."""
    th = {"qa": Threshold(min_pass_ratio=0.95, cases=2)}
    assert check_thresholds([_Outcome("qa", 0, 0)], th) == []
