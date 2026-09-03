"""main() của evals.py với FakeClient tiêm qua monkeypatch make_client — không gọi model thật, không ghi file
ngoài tmp_path (RECORDINGS_DIR trỏ sang thư mục tạm)."""
from __future__ import annotations

import json

import pytest

from studio import evals as evals_mod
from studio.evals import main


@pytest.fixture(autouse=True)
def _isolated_recordings_dir(tmp_path, monkeypatch):
    d = tmp_path / "recordings"; d.mkdir()
    monkeypatch.setattr(evals_mod, "RECORDINGS_DIR", d)
    return d


def test_run_eval_and_stale_recordings_write_context_into_blackboard(monkeypatch, tmp_path):
    from studio.evals import run_eval, stale_recordings
    from studio.llm import FakeClient

    case = {
        "name": "voi-context",
        "topic_out": "supervisor-actions",
        "context": [{"actor": "script-writer", "namespace": "voice", "content_ref": "voice.md", "summary": "giọng thân thiện"}],
        "input": {"topic": "audit-log", "key": "CH1-V1", "actor": "desk",
                  "payload": {"actor": "desk", "action": "video.rework", "video_id": "CH1-V1", "evidence": "loi lap lai"}},
        "expect": {"equals": {"target": "CH1-V1"}},
    }
    monkeypatch.setattr(evals_mod, "load_cases", lambda aid: [case])
    client = FakeClient(responses=[{"target": "CH1-V1", "action": "escalate", "reason": "cùng lỗi lặp lại", "evidence": "e"}])
    results = run_eval("supervisor", client)
    assert results[0].passed

    rec_path = evals_mod.RECORDINGS_DIR / "supervisor.json"
    rec_path.write_text(json.dumps({"agent": "supervisor", "prompt_version": 1, "cases": {}}), encoding="utf-8")
    missing = stale_recordings(["supervisor"])
    assert missing == {"supervisor": ["voi-context"]}  # bản ghi rỗng -> case chưa có trong "cases" -> báo thiếu


def test_main_without_record_or_replay_uses_make_client(monkeypatch, capsys):
    from studio.llm import FakeClient

    calls = {"n": 0}

    def fake_make_client(cfg=None):
        calls["n"] += 1
        return FakeClient(responses=[
            {"target": "CH1-V1", "action": "escalate", "reason": "cùng lỗi lặp lại nhiều lần", "evidence": "x"},
            {"target": "CH1-V2", "action": "budget_cut", "reason": "vượt ngân sách token đáng kể", "evidence": "y"},
        ])

    monkeypatch.setattr("studio.llm.make_client", fake_make_client)
    rc = main(["supervisor"])
    out = capsys.readouterr().out
    assert calls["n"] == 1  # dòng 252: else nhánh (không --record) vẫn phải gọi make_client() đúng một lần
    assert "PASS supervisor" in out or "supervisor:" in out
    assert rc in (0, 1)


def test_main_record_saves_recording_via_recording_client(monkeypatch, tmp_path, capsys):
    from studio.llm import FakeClient

    def fake_make_client(cfg=None):
        return FakeClient(responses=[
            {"target": "CH1-V1", "action": "escalate", "reason": "cùng lỗi lặp lại nhiều lần", "evidence": "x"},
            {"target": "CH1-V2", "action": "budget_cut", "reason": "vượt ngân sách token đáng kể", "evidence": "y"},
        ])

    monkeypatch.setattr("studio.llm.make_client", fake_make_client)
    main(["supervisor", "--record"])
    out = capsys.readouterr().out
    assert "đã ghi" in out  # dòng 258-259: RecordingClient.save() được gọi và in đường dẫn
    saved = evals_mod.RECORDINGS_DIR / "supervisor.json"
    assert saved.exists()
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["agent"] == "supervisor" and len(data["cases"]) == 2
