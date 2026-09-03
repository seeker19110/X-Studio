"""Nhánh còn thiếu của evals.py: case có `context` (ghi blackboard trước khi chạy) ở cả run_eval và stale_recordings,
_get() khi gặp giá trị không phải list/dict giữa đường, và chế độ --record của main()."""

from __future__ import annotations

import json

from studio.evals import _get, run_eval, stale_recordings
from studio.fakes import make_scripted_client


def test_get_returns_none_when_path_hits_non_container_value():
    payload = {"a": "chuoi khong phai dict"}
    assert _get(payload, "a.b") is None


def _fake_case_yaml(tmp_path, agent_id: str, case: dict):
    p = tmp_path / f"{agent_id}.yaml"
    import yaml
    p.write_text(yaml.safe_dump({"cases": [case]}, allow_unicode=True), encoding="utf-8")
    return p


_CASE = {
    "name": "c1",
    "context": [{"actor": "seo-optimizer", "namespace": "seo", "content_ref": "kw.md", "summary": "tu khoa"}],
    "topic_out": "review-results",
    "input": {
        "topic": "scripts", "key": "CH1-V1", "actor": "script-writer",
        "payload": {"video_id": "CH1-V1", "working_title": "t", "hook": "h",
                   "sections": [{"heading": "Vấn đề", "narration": "n", "claim_ids": ["C1"]}],
                   "claims": [{"claim_id": "C1", "text": "x", "source": None, "needs_verification": True}]},
    },
    "expect": {},
}


def test_run_eval_writes_context_to_blackboard_before_running_case(monkeypatch, tmp_path):
    import studio.evals as ev

    monkeypatch.setattr(ev, "EVALS_DIR", tmp_path)
    _fake_case_yaml(tmp_path, "fact-checker", _CASE)

    seen_context = {}
    orig_run_case = ev._run_case

    def spy(agent_id, case_, client, agents, bb, bus):
        seen_context["seo"] = bb.read("seo")
        return orig_run_case(agent_id, case_, client, agents, bb, bus)

    monkeypatch.setattr(ev, "_run_case", spy)
    client = make_scripted_client()
    results = run_eval("fact-checker", client, ev.load_agents())
    assert len(results) == 1
    assert seen_context["seo"] is not None and seen_context["seo"].content_ref == "kw.md"


def test_stale_recordings_writes_context_to_blackboard(monkeypatch, tmp_path):
    import studio.evals as ev

    monkeypatch.setattr(ev, "EVALS_DIR", tmp_path)
    monkeypatch.setattr(ev, "RECORDINGS_DIR", tmp_path)
    _fake_case_yaml(tmp_path, "fact-checker", _CASE)
    # không có case nào trong bản ghi → mọi case của agent bị coi là "thiếu"
    (tmp_path / "fact-checker.json").write_text(json.dumps({"agent": "fact-checker", "prompt_version": 1, "cases": {}}), encoding="utf-8")
    missing = stale_recordings(["fact-checker"])
    assert missing.get("fact-checker") == ["c1"]


def test_main_record_mode_calls_recording_client_save(monkeypatch, tmp_path):
    import studio.evals as ev
    from studio.evals import RecordingClient

    monkeypatch.setattr(ev, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr("studio.llm.make_client", lambda: make_scripted_client())
    saved = {}
    orig_save = RecordingClient.save

    def spy_save(self):
        p = orig_save(self)
        saved["path"] = p
        return p

    monkeypatch.setattr(RecordingClient, "save", spy_save)
    rc = ev.main(["fact-checker", "--record"])
    assert rc == 0
    assert saved["path"].exists()
