"""p3.3: cổng điểm eval cho studio (`evals/thresholds.yaml`).

Trước bản vá này `_one` khi `--replay` chỉ trả `not any(r.errored for r in res)`: ca CHẠY ĐƯỢC nhưng chấm SAI NỘI DUNG
không làm CI đỏ. 14 agent của studio có thể tụt điểm dần qua từng lần ghi lại mà không ai thấy. Cơ chế ngưỡng nằm ở
`xagents_core.evals` (dùng chung với company); file này đo phần NGHĨA của studio: file ngưỡng thật, và `main`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from studio import evals


def _stub(monkeypatch: pytest.MonkeyPatch, ids: list[str], **over: Any) -> None:
    monkeypatch.setattr(evals, "load_agents", lambda: {i: object() for i in ids})
    monkeypatch.setattr(evals, "load_cases", over.get("load_cases", lambda aid: [object()]))
    monkeypatch.setattr(evals, "outdated_versions", lambda i: {})
    monkeypatch.setattr(evals, "required_agents", lambda: [])
    monkeypatch.setattr(evals, "ReplayClient", over.get("ReplayClient", lambda aid: object()))
    monkeypatch.setattr(evals, "run_eval", over.get("run_eval",
                        lambda aid, *a: [evals.CaseResult(name="c", passed=True, failures=[], tokens=1)]))


def _res(passed: int, total: int) -> list[Any]:
    return [evals.CaseResult(name=f"c{i}", passed=i < passed, failures=[]) for i in range(total)]


# ---------- chiều ngược 1: tụt dưới sàn → đỏ ----------

def test_main_do_khi_agent_tut_duoi_san(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    th_path = tmp_path / "thresholds.yaml"
    th_path.write_text(yaml.safe_dump({"editor": {"min_pass_ratio": 0.95, "cases": 2}}), encoding="utf-8")
    _stub(monkeypatch, ["editor"], run_eval=lambda aid, *a: _res(1, 2))  # 0.50 < 0.95
    rc = evals.main(["all", "--replay", "--thresholds", str(th_path)])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "FAIL editor" in out and "0.50" in out


def test_main_xanh_khi_dat_san(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    th_path = tmp_path / "thresholds.yaml"
    th_path.write_text(yaml.safe_dump({"editor": {"min_pass_ratio": 0.95, "cases": 2}}), encoding="utf-8")
    _stub(monkeypatch, ["editor"], run_eval=lambda aid, *a: _res(2, 2))
    assert evals.main(["all", "--replay", "--thresholds", str(th_path)]) == 0


def test_main_bo_ca_thu_nho_thi_do(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    th_path = tmp_path / "thresholds.yaml"
    th_path.write_text(yaml.safe_dump({"editor": {"min_pass_ratio": 0.95, "cases": 2}}), encoding="utf-8")
    _stub(monkeypatch, ["editor"], run_eval=lambda aid, *a: _res(1, 1))
    assert evals.main(["all", "--replay", "--thresholds", str(th_path)]) == 1
    assert "bộ ca bị thu nhỏ" in capsys.readouterr().out


def test_main_no_thresholds_tat_cong(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Chiều ngược: `--no-thresholds` trả về hành vi CŨ — điểm thấp không làm CI đỏ."""
    th_path = tmp_path / "thresholds.yaml"
    th_path.write_text(yaml.safe_dump({"editor": {"min_pass_ratio": 0.95, "cases": 2}}), encoding="utf-8")
    _stub(monkeypatch, ["editor"], run_eval=lambda aid, *a: _res(1, 2))
    assert evals.main(["all", "--replay", "--thresholds", str(th_path), "--no-thresholds"]) == 0


def test_main_agent_khong_co_ca_khong_do_oan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`total == 0`: agent chưa có bộ ca thì cổng không áp (`load_cases` rỗng → thoát sớm, `res` rỗng)."""
    th_path = tmp_path / "thresholds.yaml"
    th_path.write_text(yaml.safe_dump({"editor": {"min_pass_ratio": 0.95, "cases": 2}}), encoding="utf-8")
    _stub(monkeypatch, ["editor"], load_cases=lambda aid: [])
    assert evals.main(["all", "--replay", "--thresholds", str(th_path)]) == 0


# ---------- chiều ngược 2: không có file ngưỡng → cổng không áp; nhưng file THẬT phải tồn tại ----------

def test_khong_co_file_nguong_thi_cong_khong_ap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(evals, "DEFAULT_THRESHOLDS_PATH", tmp_path / "khong-co.yaml")
    _stub(monkeypatch, ["editor"], run_eval=lambda aid, *a: _res(0, 2))
    assert evals.main(["all", "--replay"]) == 0


def test_thresholds_yaml_that_ton_tai_va_khop_required() -> None:
    """Xoá `Studio-creators/evals/thresholds.yaml` là CI đỏ: file ấy chính là cổng, mất nó là mất cổng lặng lẽ."""
    assert evals.DEFAULT_THRESHOLDS_PATH.exists(), "thiếu Studio-creators/evals/thresholds.yaml"
    th = evals.load_thresholds()
    required = set(evals.required_agents())
    assert len(th) == 14, f"phải đủ 14 agent, đang có {len(th)}"
    assert set(th) == required, f"thresholds.yaml phải khớp đúng REQUIRED.txt: {set(th) ^ required}"
    for aid, t in th.items():
        assert t.min_pass_ratio == 0.95, aid
        assert t.cases == 2, aid


def test_load_thresholds_mac_dinh_va_duong_dan_tuong_minh(tmp_path: Path) -> None:
    """`load_thresholds()` không tham số dùng đường dẫn của studio; truyền path thì dùng path đó."""
    assert set(evals.load_thresholds()) == set(evals.load_thresholds(evals.DEFAULT_THRESHOLDS_PATH))
    assert evals.load_thresholds(tmp_path / "khong-co.yaml") == {}


# ---------- nghiệm thu: bản ghi hiện tại phải xanh với --strict ----------

def test_strict_voi_ban_ghi_hien_tai_xanh(capsys: pytest.CaptureFixture[str]) -> None:
    rc = evals.main(["all", "--replay", "--strict"])
    assert rc == 0, capsys.readouterr().out
