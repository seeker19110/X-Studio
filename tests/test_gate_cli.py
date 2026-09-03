"""CLI `studio.gate_cli.main()`: list, request, và các nhánh lỗi (gate không tồn tại, không đủ quyền)."""

from __future__ import annotations

from studio.events import Envelope
from studio.gate_cli import main
from studio.sqlite_bus import SQLiteBus


def test_cli_request_then_list_then_approve(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("STUDIO_GATE_APPROVERS", raising=False)
    db = str(tmp_path / "s.sqlite")

    rc = main(["--db", db, "request", "publish", "PUB-V1", "--by", "desk", "--checklist", "review:fact:pass,review:rights:pass"])
    assert rc == 0
    assert "requested publish PUB-V1" in capsys.readouterr().out

    rc = main(["--db", db, "list"])
    out = capsys.readouterr().out
    assert rc == 0 and "PUB-V1" in out and "publish" in out

    rc = main(["--db", db, "approve", "PUB-V1", "--by", "human:editor", "--reason", "ok"])
    out = capsys.readouterr().out
    assert rc == 0 and "PUB-V1: approve by human:editor" in out


def test_cli_list_full_does_not_truncate_long_checklist_items(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("STUDIO_GATE_APPROVERS", raising=False)
    db = str(tmp_path / "s.sqlite")
    long_item = "x" * 100
    main(["--db", db, "request", "publish", "PUB-V2", "--by", "desk", "--checklist", long_item])
    capsys.readouterr()
    main(["--db", db, "list", "--full"])
    out = capsys.readouterr().out
    assert long_item in out and "…" not in out


def test_cli_list_prints_placeholder_when_nothing_pending(tmp_path, capsys):
    db = str(tmp_path / "s.sqlite")
    rc = main(["--db", db, "list"])
    assert rc == 0
    assert "không có gate chờ" in capsys.readouterr().out


def test_cli_decide_unknown_gate_returns_2(tmp_path, capsys):
    db = str(tmp_path / "s.sqlite")
    rc = main(["--db", db, "approve", "PUB-khong-ton-tai", "--by", "human:editor"])
    assert rc == 2
    assert "không có gate chờ" in capsys.readouterr().err


def test_cli_rollback_without_publish_event_returns_4(tmp_path, capsys):
    db = str(tmp_path / "s.sqlite")
    rc = main(["--db", db, "rollback", "PUB-V-none", "--by", "human:editor"])
    assert rc == 4
    assert "không có gì để rollback" in capsys.readouterr().err


def test_cli_rollback_with_scheduled_publish_event_proceeds(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("STUDIO_GATE_APPROVERS", raising=False)
    db = tmp_path / "s.sqlite"
    bus = SQLiteBus(db)
    bus.publish(Envelope(topic="publish-events", key="V4", actor="publisher",
                         payload={"video_id": "V4", "kind": "video", "status": "scheduled", "platform_ref": "yt-1"}))
    bus.close()
    main(["--db", str(db), "request", "publish", "PUB-V4", "--by", "desk"])
    capsys.readouterr()
    rc = main(["--db", str(db), "rollback", "PUB-V4", "--by", "human:editor"])
    assert rc == 0
    assert "PUB-V4: rollback" in capsys.readouterr().out


def test_cli_decide_permission_error_returns_3(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("STUDIO_GATE_APPROVERS", raising=False)
    db = str(tmp_path / "s.sqlite")
    main(["--db", db, "request", "publish", "PUB-V3", "--by", "desk"])
    capsys.readouterr()
    monkeypatch.setenv("STUDIO_GATE_APPROVERS", "human:editor")
    rc = main(["--db", db, "approve", "PUB-V3", "--by", "ai:not-allowed"])
    assert rc == 3
    assert capsys.readouterr().err
