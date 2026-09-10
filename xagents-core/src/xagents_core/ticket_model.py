"""Hình dạng chung của "một đơn vị công việc có ngân sách" (K3.7 của ADR gốc 0001).

`Task` của company và `VideoBrief` của studio KHÔNG được gộp: chúng là hai miền (ticket phần mềm vs video), và
`Task` có `project_id` mà `VideoBrief` cố ý không có. Thứ duy nhất mã chung cần biết là "vật này có trần
token" — nên đây là một `Protocol` cấu trúc, không phải lớp cha: không ai phải kế thừa gì, và mọi hàm chung
nhận `Budgeted` vẫn nhận đúng cả hai."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["Budgeted"]


@runtime_checkable
class Budgeted(Protocol):
    """Đơn vị công việc mang trần token (`Task.budget_tokens`, `VideoBrief.budget_tokens`)."""

    budget_tokens: int
