"""refresh_access_token, resolve_project_id, login_pkce (OAuth PKCE qua trình duyệt)."""

from __future__ import annotations

import contextlib
import http.client
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from gateway import auth as gw_auth


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((gw_auth.REDIRECT_HOST, 0))
        return s.getsockname()[1]


class _FakeResponse:
    """Mô phỏng đối tượng trả về từ `urllib.request.urlopen` dùng trong `with ... as resp`."""

    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def manager(tmp_path):
    return gw_auth.AntigravityAuthManager(auth_file=tmp_path / "tokens.json")


def _creds(**kw) -> gw_auth.AntigravityCredentials:
    base = {"access_token": "tok", "email": "a@example.com", "refresh_token": "r"}
    base.update(kw)
    return gw_auth.AntigravityCredentials(**base)


# ---------- refresh_access_token ----------


def test_refresh_access_token_updates_and_saves(manager, monkeypatch):
    creds = _creds(expires_at=1)
    manager.save_credentials(creds)

    def fake_urlopen(req, timeout=None):
        assert req.full_url == gw_auth.TOKEN_ENDPOINT
        return _FakeResponse({"access_token": "new-tok", "expires_in": 1800, "refresh_token": "new-r"})

    monkeypatch.setattr(gw_auth.urllib.request, "urlopen", fake_urlopen)
    updated = manager.refresh_access_token(creds)

    assert updated.access_token == "new-tok"
    assert updated.refresh_token == "new-r"
    assert updated.expires_at > gw_auth.time.time()
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]["a@example.com"]
    assert stored["access_token"] == "new-tok"


def test_refresh_access_token_keeps_old_refresh_token_if_not_returned(manager, monkeypatch):
    creds = _creds(refresh_token="keep-me", expires_at=1)

    monkeypatch.setattr(
        gw_auth.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse({"access_token": "new-tok"}),
    )
    updated = manager.refresh_access_token(creds)
    assert updated.refresh_token == "keep-me"


def test_refresh_access_token_without_refresh_token_raises(manager):
    creds = _creds(refresh_token="")
    with pytest.raises(RuntimeError, match="refresh token"):
        manager.refresh_access_token(creds)


def test_refresh_access_token_missing_access_token_in_response_raises(manager, monkeypatch):
    creds = _creds()
    monkeypatch.setattr(
        gw_auth.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse({"error": "invalid_grant"}),
    )
    with pytest.raises(RuntimeError, match="thất bại"):
        manager.refresh_access_token(creds)


def test_refresh_access_token_propagates_http_error(manager, monkeypatch):
    creds = _creds()

    def raise_http_error(req, timeout=None):
        raise urllib.error.HTTPError(gw_auth.TOKEN_ENDPOINT, 400, "invalid_grant", {}, None)

    monkeypatch.setattr(gw_auth.urllib.request, "urlopen", raise_http_error)
    with pytest.raises(urllib.error.HTTPError):
        manager.refresh_access_token(creds)


# ---------- resolve_project_id ----------


def test_resolve_project_id_env_override_wins(manager, monkeypatch):
    monkeypatch.setenv(gw_auth.ENV_PROJECT_ID, "env-project")
    creds = _creds(project_id="stored-project")
    assert manager.resolve_project_id(creds) == "env-project"


def test_resolve_project_id_returns_cached_value(manager, monkeypatch):
    monkeypatch.delenv(gw_auth.ENV_PROJECT_ID, raising=False)
    creds = _creds(project_id="cached-project")
    assert manager.resolve_project_id(creds) == "cached-project"


def test_resolve_project_id_calls_load_code_assist(manager, monkeypatch):
    monkeypatch.delenv(gw_auth.ENV_PROJECT_ID, raising=False)
    creds = _creds(project_id="")

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _FakeResponse({"cloudaicompanionProject": "resolved-project", "tierId": "free"})

    monkeypatch.setattr(gw_auth.urllib.request, "urlopen", fake_urlopen)
    pid = manager.resolve_project_id(creds)

    assert pid == "resolved-project"
    assert creds.managed_project_id == "resolved-project"
    assert creds.tier_id == "free"
    assert captured["url"] == gw_auth.LOAD_CODE_ASSIST_ENDPOINT
    assert captured["auth"] == "Bearer tok"


def test_resolve_project_id_falls_back_to_default_on_error(manager, monkeypatch):
    monkeypatch.delenv(gw_auth.ENV_PROJECT_ID, raising=False)
    creds = _creds(project_id="")

    def raise_error(req, timeout=None):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(gw_auth.urllib.request, "urlopen", raise_error)
    assert manager.resolve_project_id(creds) == gw_auth.DEFAULT_PROJECT_ID


def test_resolve_project_id_falls_back_when_response_has_no_project_field(manager, monkeypatch):
    monkeypatch.delenv(gw_auth.ENV_PROJECT_ID, raising=False)
    creds = _creds(project_id="")
    monkeypatch.setattr(gw_auth.urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse({}))
    assert manager.resolve_project_id(creds) == gw_auth.DEFAULT_PROJECT_ID


# ---------- login_pkce ----------


def _drive_callback(port: int, *, code: str | None, state: str, error: str | None = None) -> int:
    """Gửi GET tới callback server như trình duyệt sẽ làm sau khi người dùng đồng ý; trả về status code.

    Dùng `http.client` thay vì `urllib.request` vì test này thường monkeypatch
    `urllib.request.urlopen` để giả lập Google's token/userinfo endpoint bên trong
    luồng `login_pkce` — gọi qua urllib ở đây sẽ vô tình bị chính mock đó chặn lại.
    """
    params = {"state": state}
    if code is not None:
        params["code"] = code
    if error is not None:
        params["error"] = error
    path = f"{gw_auth.CALLBACK_PATH}?" + urllib.parse.urlencode(params)
    conn = http.client.HTTPConnection(gw_auth.REDIRECT_HOST, port, timeout=5.0)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def test_login_pkce_full_flow_saves_credentials(manager, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(gw_auth.secrets, "token_hex", lambda n: "fixedstate")
    monkeypatch.setattr(gw_auth.webbrowser, "open", lambda url: True)

    def fake_urlopen(req, timeout=None):
        if req.full_url == gw_auth.TOKEN_ENDPOINT:
            return _FakeResponse(
                {"access_token": "final-tok", "refresh_token": "final-r", "expires_in": 3600}
            )
        if req.full_url.startswith(gw_auth.USERINFO_ENDPOINT):
            return _FakeResponse({"email": "logged-in@example.com"})
        if req.full_url == gw_auth.LOAD_CODE_ASSIST_ENDPOINT:
            return _FakeResponse({"cloudaicompanionProject": "proj-x"})
        raise AssertionError(f"unexpected urlopen: {req.full_url}")

    monkeypatch.setattr(gw_auth.urllib.request, "urlopen", fake_urlopen)

    result: dict = {}

    def run():
        result["creds"] = manager.login_pkce(port=port, open_browser=True, timeout_seconds=10.0)

    t = threading.Thread(target=run)
    t.start()
    import time as _time

    _time.sleep(0.3)  # để HTTPServer kịp bind trước khi ta gọi callback
    status = _drive_callback(port, code="auth-code-123", state="fixedstate")
    t.join(timeout=15)

    assert status == 200
    assert not t.is_alive()
    creds = result["creds"]
    assert creds.access_token == "final-tok"
    assert creds.refresh_token == "final-r"
    assert creds.email == "logged-in@example.com"
    assert creds.project_id == "proj-x"

    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]["logged-in@example.com"]
    assert stored["access_token"] == "final-tok"


def test_login_pkce_state_mismatch_raises(manager, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(gw_auth.secrets, "token_hex", lambda n: "expectedstate")
    monkeypatch.setattr(gw_auth.webbrowser, "open", lambda url: True)

    result: dict = {}

    def run():
        try:
            manager.login_pkce(port=port, open_browser=True, timeout_seconds=10.0)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    t = threading.Thread(target=run)
    t.start()
    import time as _time

    _time.sleep(0.3)
    status = _drive_callback(port, code="whatever", state="wrong-state")
    t.join(timeout=15)

    assert status == 400
    assert isinstance(result.get("error"), RuntimeError)
    assert "State không khớp" in str(result["error"])


def test_login_pkce_google_error_param_raises(manager, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(gw_auth.secrets, "token_hex", lambda n: "fixedstate2")
    monkeypatch.setattr(gw_auth.webbrowser, "open", lambda url: True)

    result: dict = {}

    def run():
        try:
            manager.login_pkce(port=port, open_browser=True, timeout_seconds=10.0)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    t = threading.Thread(target=run)
    t.start()
    import time as _time

    _time.sleep(0.3)
    status = _drive_callback(port, code=None, state="fixedstate2", error="access_denied")
    t.join(timeout=15)

    assert status == 400
    assert isinstance(result.get("error"), RuntimeError)
    assert "access_denied" in str(result["error"])


def test_login_pkce_timeout_without_callback(manager, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(gw_auth.secrets, "token_hex", lambda n: "fixedstate3")
    monkeypatch.setattr(gw_auth.webbrowser, "open", lambda url: True)

    with pytest.raises(TimeoutError):
        manager.login_pkce(port=port, open_browser=True, timeout_seconds=0.5)


def test_login_pkce_prints_url_when_open_browser_false(manager, monkeypatch, capsys):
    port = _free_port()
    monkeypatch.setattr(gw_auth.secrets, "token_hex", lambda n: "fixedstate4")
    called = {"opened": False}
    monkeypatch.setattr(gw_auth.webbrowser, "open", lambda url: called.__setitem__("opened", True))

    with pytest.raises(TimeoutError):
        manager.login_pkce(port=port, open_browser=False, timeout_seconds=0.3)

    assert called["opened"] is False
    out = capsys.readouterr().out
    assert gw_auth.AUTH_ENDPOINT in out


def test_login_pkce_userinfo_failure_still_saves_with_empty_email(manager, monkeypatch):
    port = _free_port()
    monkeypatch.setattr(gw_auth.secrets, "token_hex", lambda n: "fixedstate5")
    monkeypatch.setattr(gw_auth.webbrowser, "open", lambda url: True)

    def fake_urlopen(req, timeout=None):
        if req.full_url == gw_auth.TOKEN_ENDPOINT:
            return _FakeResponse({"access_token": "tok2", "expires_in": 3600})
        if req.full_url.startswith(gw_auth.USERINFO_ENDPOINT):
            raise urllib.error.URLError("userinfo down")
        if req.full_url == gw_auth.LOAD_CODE_ASSIST_ENDPOINT:
            return _FakeResponse({})
        raise AssertionError(f"unexpected urlopen: {req.full_url}")

    monkeypatch.setattr(gw_auth.urllib.request, "urlopen", fake_urlopen)

    result: dict = {}

    def run():
        result["creds"] = manager.login_pkce(port=port, open_browser=True, timeout_seconds=10.0)

    t = threading.Thread(target=run)
    t.start()
    import time as _time

    _time.sleep(0.3)
    _drive_callback(port, code="c", state="fixedstate5")
    t.join(timeout=15)

    creds = result["creds"]
    assert creds.access_token == "tok2"
    assert creds.email == ""
    assert creds.project_id == gw_auth.DEFAULT_PROJECT_ID


def test_login_pkce_ignores_unrelated_path_before_callback(manager, monkeypatch):
    # Trình duyệt/extension đôi khi bắn request lạ (favicon.ico...) tới cổng callback trước khi
    # người dùng bấm "Cho phép" — server phải trả 404 và tiếp tục chờ, không được vỡ luồng.
    port = _free_port()
    monkeypatch.setattr(gw_auth.secrets, "token_hex", lambda n: "fixedstate6")
    monkeypatch.setattr(gw_auth.webbrowser, "open", lambda url: True)

    def fake_urlopen(req, timeout=None):
        if req.full_url == gw_auth.TOKEN_ENDPOINT:
            return _FakeResponse({"access_token": "tok3", "expires_in": 3600})
        if req.full_url.startswith(gw_auth.USERINFO_ENDPOINT):
            return _FakeResponse({"email": "u@example.com"})
        if req.full_url == gw_auth.LOAD_CODE_ASSIST_ENDPOINT:
            return _FakeResponse({"cloudaicompanionProject": "proj-y"})
        raise AssertionError(f"unexpected urlopen: {req.full_url}")

    monkeypatch.setattr(gw_auth.urllib.request, "urlopen", fake_urlopen)

    result: dict = {}

    def run():
        result["creds"] = manager.login_pkce(port=port, open_browser=True, timeout_seconds=10.0)

    t = threading.Thread(target=run)
    t.start()
    import time as _time

    _time.sleep(0.3)
    conn = http.client.HTTPConnection(gw_auth.REDIRECT_HOST, port, timeout=5.0)
    try:
        conn.request("GET", "/favicon.ico")
        stray_status = conn.getresponse().status
    finally:
        conn.close()
    assert stray_status == 404
    assert t.is_alive()

    status = _drive_callback(port, code="c3", state="fixedstate6")
    t.join(timeout=15)

    assert status == 200
    assert result["creds"].access_token == "tok3"


def test_resolve_credential_candidates_fills_missing_project_id(manager, monkeypatch):
    creds = _creds(project_id="")
    manager.save_credentials(creds)
    monkeypatch.delenv(gw_auth.ENV_PROJECT_ID, raising=False)
    monkeypatch.setattr(
        gw_auth.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse({"cloudaicompanionProject": "filled-project"}),
    )

    candidates = manager.resolve_credential_candidates()

    assert candidates[0].project_id == "filled-project"
    stored = json.loads(manager.token_file.read_text(encoding="utf-8"))["accounts"]["a@example.com"]
    assert stored["project_id"] == "filled-project"
