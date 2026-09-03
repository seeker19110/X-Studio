"""OAuth loopback (`_wait_for_code`, `login`) và các nhánh CLI của youtube.py chưa được phủ ở nơi khác."""

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import threading
import time
import urllib.parse

import pytest

from studio import youtube as yt
from studio.bus import InMemoryBus
from studio.events import Envelope, PerformanceSnapshot
from studio.platform import Comment, PlatformError, SnapshotResult, TokenStore


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeFetchResponse:
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, json.dumps(body).encode("utf-8")


def _fetcher(status=200, body=None):
    body = body or {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    def f(method, url, headers, data):
        return status, {}, json.dumps(body).encode("utf-8")
    return f


def _drive_callback(port: int, *, code: str | None, state: str) -> int:
    params = {"state": state}
    if code is not None:
        params["code"] = code
    path = "/?" + urllib.parse.urlencode(params)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def test_wait_for_code_returns_code_on_matching_state():
    port = _free_port()
    result = {}

    def run():
        result["code"] = yt._wait_for_code(port, "st1", timeout_s=10.0)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)
    status = _drive_callback(port, code="abc123", state="st1")
    t.join(timeout=10)
    assert status == 200
    assert result["code"] == "abc123"


def test_wait_for_code_state_mismatch_times_out():
    port = _free_port()

    def send_wrong():
        time.sleep(0.2)
        _drive_callback(port, code="c", state="wrong")

    threading.Thread(target=send_wrong, daemon=True).start()
    with pytest.raises(PlatformError, match="không nhận được mã"):
        yt._wait_for_code(port, "expected", timeout_s=0.6)


def test_login_full_flow_saves_tokens(tmp_path, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(yt.secrets, "token_urlsafe", lambda n: "fixedstate")
    monkeypatch.setattr(yt.webbrowser, "open", lambda url: True)
    secrets_path = tmp_path / "client_secret.json"
    secrets_path.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}), encoding="utf-8")
    store = TokenStore(tmp_path / "tokens.json")

    result: dict = {}

    def run():
        result["tokens"] = yt.login(secrets_path, store, port=port, fetcher=_fetcher(), open_browser=True)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)
    status = _drive_callback(port, code="authcode", state="fixedstate")
    t.join(timeout=10)

    assert status == 200
    toks = result["tokens"]
    assert toks.access_token == "at" and toks.refresh_token == "rt"
    assert store.path.exists()


def test_load_client_secrets_missing_fields_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"installed": {"client_id": "cid"}}), encoding="utf-8")
    with pytest.raises(PlatformError, match="client_id/client_secret"):
        yt.load_client_secrets(p)


def test_main_sync_comments_no_new_comments_prints_message(tmp_path, monkeypatch, capsys):
    db = tmp_path / "s.sqlite"
    from studio.sqlite_bus import SQLiteBus
    bus = SQLiteBus(db)
    bus.publish(Envelope(topic="video-briefs", key="V1", actor="channel-strategist", payload={
        "video_id": "V1", "channel_id": "CH1", "working_title": "t", "pillar": "p", "angle": "a", "audience": "u",
        "estimate_tokens": 1000, "budget_tokens": 2000}))
    bus.publish(Envelope(topic="publish-events", key="V1", actor="publisher", payload={
        "video_id": "V1", "kind": "video", "platform_ref": "YTID1", "status": "published"}))
    bus.close()

    rc = yt.main(["--db", str(db), "--tokens", str(tmp_path / "tok.json"), "sync-comments", "V1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "không có gì mới" in out or "bình luận" in out


def test_main_status_prints_json(tmp_path, monkeypatch, capsys):
    rc = yt.main(["--db", str(tmp_path / "s.sqlite"), "--tokens", str(tmp_path / "tok.json"), "status"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["logged_in"] is False


# ---------- sync_comments / sync_metrics / find_channel / seen_comment_ids ----------

class _FakePlatform:
    name = "fake"

    def __init__(self, comments):
        self._comments = comments

    def list_comments(self, platform_ref, since=None):
        return self._comments

    def snapshot(self, platform_ref, window_days=7, channel_id=""):
        snap = PerformanceSnapshot(video_id="V1", channel_id=channel_id, window_days=window_days, views=100, impressions=500)
        return SnapshotResult(snap, evidence="fake evidence")


def _seed_video(bus):
    bus.publish(Envelope(topic="video-briefs", key="V1", actor="channel-strategist", payload={
        "video_id": "V1", "channel_id": "CH1", "working_title": "t", "pillar": "p", "angle": "a", "audience": "u",
        "estimate_tokens": 1000, "budget_tokens": 2000}))
    bus.publish(Envelope(topic="publish-events", key="V1", actor="publisher", payload={
        "video_id": "V1", "kind": "video", "platform_ref": "YTID1", "status": "published"}))


def test_find_channel_reads_from_video_briefs():
    bus = InMemoryBus(); _seed_video(bus)
    assert yt.find_channel(bus, "V1") == "CH1"
    assert yt.find_channel(bus, "V-none") == ""


def test_seen_comment_ids_includes_synced_and_replied():
    bus = InMemoryBus(); _seed_video(bus)
    bus.publish(Envelope(topic="audience-comments", key="V1", actor=yt.ACTOR, payload={
        "video_id": "V1", "platform_ref": "YTID1", "comments": [{"comment_id": "C1", "author": "a", "text": "t", "likes": 0, "published_at": ""}]}))
    bus.publish(Envelope(topic="publish-events", key="V1", actor="community-manager", payload={
        "video_id": "V1", "kind": "reply", "status": "published", "comment_id": "C2"}))
    assert yt.seen_comment_ids(bus, "V1") == {"C1", "C2"}


def test_sync_comments_publishes_new_comments_and_skips_seen():
    bus = InMemoryBus(); _seed_video(bus)
    platform = _FakePlatform([Comment(comment_id="C1", text="hay qua", author="u1"), Comment(comment_id="C2", text="hay qua 2", author="u2")])
    bus.publish(Envelope(topic="audience-comments", key="V1", actor=yt.ACTOR, payload={
        "video_id": "V1", "platform_ref": "YTID1", "comments": [{"comment_id": "C1", "author": "u1", "text": "x", "likes": 0, "published_at": ""}]}))
    env = yt.sync_comments(bus, platform, "V1")
    assert env is not None
    assert [c["comment_id"] for c in env.payload["comments"]] == ["C2"]


def test_sync_comments_no_ref_raises():
    bus = InMemoryBus()
    with pytest.raises(PlatformError, match="chưa upload"):
        yt.sync_comments(bus, _FakePlatform([]), "V-no-ref")


def test_sync_metrics_publishes_snapshot_with_resolved_channel():
    bus = InMemoryBus(); _seed_video(bus)
    platform = _FakePlatform([])
    env = yt.sync_metrics(bus, platform, "V1")
    assert env.payload["views"] == 100 and env.payload["channel_id"] == "CH1"


def test_sync_metrics_no_ref_raises():
    bus = InMemoryBus()
    with pytest.raises(PlatformError, match="chưa upload"):
        yt.sync_metrics(bus, _FakePlatform([]), "V-no-ref")


# ---------- CLI: login / sync-metrics branches ----------

def test_main_login_invokes_login_and_returns_0(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(yt, "login", lambda secrets, store, port, open_browser: called.update(
        secrets=secrets, port=port, open_browser=open_browser) or None)
    secrets_path = tmp_path / "cs.json"
    secrets_path.write_text("{}", encoding="utf-8")
    rc = yt.main(["--db", str(tmp_path / "s.sqlite"), "--tokens", str(tmp_path / "tok.json"), "login",
                 "--client-secrets", str(secrets_path), "--port", "9999", "--no-browser"])
    assert rc == 0
    assert called == {"secrets": secrets_path, "port": 9999, "open_browser": False}


def test_main_sync_metrics_prints_summary(tmp_path, monkeypatch, capsys):
    db = tmp_path / "s.sqlite"
    from studio.sqlite_bus import SQLiteBus
    bus = SQLiteBus(db); _seed_video(bus); bus.close()

    monkeypatch.setattr(yt, "make_platform", lambda cfg: _FakePlatform([]))
    rc = yt.main(["--db", str(db), "--tokens", str(tmp_path / "tok.json"), "sync-metrics", "V1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "views=100" in out and "performance-snapshots" in out


def test_main_platform_error_prints_to_stderr_and_returns_1(tmp_path, capsys):
    rc = yt.main(["--db", str(tmp_path / "s.sqlite"), "--tokens", str(tmp_path / "tok.json"), "sync-comments", "V-khong-ton-tai"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "lỗi:" in err
