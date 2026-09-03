"""Nhánh còn thiếu của gate_cli.py: rollback_target khi đã rolled_back, lệnh `request`, và KeyError khi
approve/reject một subject_id không có gate chờ."""
from __future__ import annotations

from studio.bus import InMemoryBus
from studio.events import Envelope
from studio.gate_cli import main, rollback_target
from studio.sqlite_bus import SQLiteBus


def test_rollback_target_returns_none_once_already_rolled_back():
    bus = InMemoryBus()
    bus.publish(Envelope(topic="publish-events", key="V1", actor="publisher",
                         payload={"video_id": "V1", "status": "scheduled", "platform_ref": "yt:1"}))
    assert rollback_target(bus, "V1") is not None
    bus.publish(Envelope(topic="publish-events", key="V1", actor="human:editor",
                         payload={"video_id": "V1", "status": "rolled_back"}))
    assert rollback_target(bus, "V1") is None


def test_cli_request_creates_pending_gate_and_list_shows_it(tmp_path, capsys):
    db = tmp_path / "s.sqlite"
    assert main(["--db", str(db), "request", "publish", "PUB-V1", "--by", "desk", "--checklist", "review:fact:pass,review:rights:pass"]) == 0
    out = capsys.readouterr().out
    assert "requested publish PUB-V1" in out
    assert main(["--db", str(db), "list"]) == 0
    listing = capsys.readouterr().out
    assert "PUB-V1" in listing and "publish" in listing


def test_cli_decide_unknown_subject_returns_error_code(tmp_path, capsys):
    db = tmp_path / "s.sqlite"
    rc = main(["--db", str(db), "approve", "PUB-KHONG-TON-TAI", "--by", "human:editor"])
    assert rc == 2
    assert "không có gate chờ" in capsys.readouterr().err
