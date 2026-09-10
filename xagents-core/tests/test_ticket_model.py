"""`xagents_core.ticket_model` — hình dạng chung "có trần token" (K3.7).

Protocol CẤU TRÚC, không phải lớp cha: `Task` của company và `VideoBrief` của studio đi vừa chữ ký mà không
kế thừa gì, và `VideoBrief` vẫn cố ý KHÔNG có `project_id` như `Task`.
"""
from __future__ import annotations

from dataclasses import dataclass

from xagents_core.ticket_model import Budgeted


@dataclass
class VatCoTran:
    budget_tokens: int


@dataclass
class VatKhongCoTran:
    ten: str


def test_vat_co_budget_tokens_thoa_protocol_ma_khong_ke_thua_gi():
    vat = VatCoTran(budget_tokens=90000)
    assert isinstance(vat, Budgeted) and not isinstance(VatKhongCoTran(ten="x"), Budgeted)

    def tran(item: Budgeted) -> int: return item.budget_tokens
    assert tran(vat) == 90000
