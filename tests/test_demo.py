"""Smoke test cho `studio.demo.main()`: chạy hết kịch bản offline (client + media giả) và không lỗi."""

from __future__ import annotations

from studio import demo


def test_demo_main_runs_end_to_end(capsys):
    assert demo.main() == 0
    out = capsys.readouterr().out
    assert "gate plan" in out
    assert "báo cáo" in out
