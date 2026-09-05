"""README của phòng ban nói bao nhiêu test thì phải đúng bấy nhiêu.

`software-company` đã có cổng này (`test_review_fixes_2026_09.py::test_readme_khop_so_lieu_that`); Studio thì
chưa, và hệ quả đo được ngày 2026-09-05: README ghi `164 ca / 12 file` cho một suite thật sự có 394 ca / 30 file
— lệch hơn gấp đôi, tích tụ lặng lẽ qua nhiều PR vì không cổng nào đọc con số ấy.

Cùng quy ước đếm với công ty kia: `file` = mọi `.py` trong `tests/` (kể cả `conftest.py`), và `ca` chỉ chặn
khoảng [số hàm test, gấp đôi] vì test tham số hoá làm số ca thu được lớn hơn số hàm.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _count_cases() -> int:
    """Đếm hàm test trong tests/ (xấp xỉ số ca; test tham số hoá tính là một)."""
    return sum(len(re.findall(r"^def test_", p.read_text(encoding="utf-8"), re.M))
               for p in (ROOT / "tests").glob("*.py"))


def test_readme_khop_so_lieu_that() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    n_files = len(list((ROOT / "tests").glob("*.py")))
    last_adr = max(int(p.name[:4]) for p in (ROOT / "docs" / "adr").glob("*.md"))

    m = re.search(r"pytest (\d+) ca / (\d+) file", readme)
    assert m, "README phải nói số ca test và số file test"
    assert int(m.group(2)) == n_files, f"README ghi {m.group(2)} file test, thực tế {n_files}"
    n_funcs = _count_cases()
    assert n_funcs <= int(m.group(1)) <= n_funcs * 2, f"README ghi {m.group(1)} ca, có {n_funcs} hàm test"

    m2 = re.search(r"- Test: (\d+) ca pytest", readme)
    assert m2 and m2.group(1) == m.group(1), (
        f"hai chỗ trong README nói khác nhau: 'pytest {m.group(1)} ca' và 'Test: {m2 and m2.group(1)} ca'")
    assert f"0001–{last_adr:04d}" in readme, f"README phải nhắc ADR mới nhất (0001–{last_adr:04d})"
