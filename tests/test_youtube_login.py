"""`_wait_for_code` (HTTP loopback thật trên cổng ngẫu nhiên) và `login` (không mở trình duyệt thật) — không chạm mạng
ra ngoài; CLI `main(["login", ...])` và nhánh sync-comments không có gì mới."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import urllib.parse
import urllib.request
from typing import ClassVar

import pytest

from studio.platform import PlatformError, TokenStore
from studio.youtube import _wait_for_code, login, main


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _drive_callback(port: int, *, code: str, state: str) -> int:
    """Gọi callback loopback qua `http.client` thay vì `urllib.request` — vài test dưới đây monkeypatch
    `urllib.request.urlopen` để giả lập endpoint token của Google bên trong luồng `login`, và vì đó là cùng
    module `urllib.request` (không phải bản sao), gọi qua urllib ở đây sẽ vô tình bị chính mock đó chặn lại."""
    path = "/?" + urllib.parse.urlencode({"code": code, "state": state})
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
    t = threading.Thread(target=lambda: _drive_callback(port, code="abc123", state="st1"))
    t.start()
    code = _wait_for_code(port, "st1", timeout_s=5.0)
    t.join()
    assert code == "abc123"


def test_wait_for_code_ignores_wrong_state_and_times_out():
    port = _free_port()

    def fire():
        try:
            _drive_callback(port, code="x", state="wrong")
        except OSError:
            pass  # server đóng cổng khi hết giờ (0.5s) trước khi thread này kịp gửi — vô hại, bỏ qua

    t = threading.Thread(target=fire, daemon=True)
    t.start()
    with pytest.raises(PlatformError, match="không nhận được mã"):
        _wait_for_code(port, "st1", timeout_s=0.5)
    t.join(timeout=5)


def test_login_exchanges_code_and_saves_token_without_opening_browser(tmp_path, monkeypatch):
    import studio.youtube as yt_mod

    cs = tmp_path / "client_secret.json"
    cs.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}), encoding="utf-8")
    store = TokenStore(tmp_path / "auth" / "tok.json")
    port = _free_port()
    monkeypatch.setattr(yt_mod.secrets, "token_urlsafe", lambda n: "fixedstate2")

    def fetcher(method, url, headers, body):
        return 200, {}, json.dumps({"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": "s1"}).encode()

    def fire_callback():
        import time
        time.sleep(0.3)
        _drive_callback(port, code="cd1", state="fixedstate2")

    t = threading.Thread(target=fire_callback)
    t.start()
    tokens = login(cs, store, port=port, fetcher=fetcher, open_browser=False)
    t.join(timeout=15)
    assert tokens.access_token == "a" and tokens.refresh_token == "r"
    assert store.load().access_token == "a"


def test_cli_sync_comments_prints_no_new_comments_when_env_is_none(tmp_path, capsys):
    from studio.events import Envelope
    from studio.sqlite_bus import SQLiteBus

    db = tmp_path / "s.sqlite"
    b = SQLiteBus(db)
    b.publish(Envelope(topic="publish-events", key="V1", actor="publisher",
                       payload={"video_id": "V1", "status": "scheduled", "platform_ref": "fake-0001"}))
    b.close()
    rc = main(["--db", str(db), "--tokens", str(tmp_path / "none.json"), "sync-comments", "V1"])
    out = capsys.readouterr().out
    assert rc == 0 and "không có gì mới" in out


def test_cli_login_end_to_end(tmp_path, monkeypatch):
    # State cố định (thay vì bóc từ stdout qua capsys — đua với thread server, dễ flaky) để tránh chạm mạng thật.
    import studio.youtube as yt_mod

    cs = tmp_path / "client_secret.json"
    cs.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}), encoding="utf-8")
    port = _free_port()
    tokens_path = tmp_path / "tok.json"

    class _Resp:
        status = 200
        headers: ClassVar[dict] = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"access_token": "a", "refresh_token": "r", "expires_in": 3600}).encode()

    monkeypatch.setattr("studio.platform.urllib.request.urlopen", lambda req, timeout=None: _Resp())
    monkeypatch.setattr(yt_mod.secrets, "token_urlsafe", lambda n: "fixedstate")

    def fire_callback():
        import time
        time.sleep(0.3)
        _drive_callback(port, code="cd1", state="fixedstate")

    t = threading.Thread(target=fire_callback)
    t.start()
    rc = main(["--tokens", str(tokens_path), "login", "--client-secrets", str(cs), "--port", str(port), "--no-browser"])
    t.join(timeout=15)
    assert rc == 0 and TokenStore(tokens_path).load().access_token == "a"
