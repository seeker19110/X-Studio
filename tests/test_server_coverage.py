"""Phủ nốt các nhánh chưa được test_server.py chạm tới: get_pid_file/get_log_file, handle_auth_login,
_refresh_catalog, is_server_running, và các nhánh exception khi stream bị ngắt giữa chừng."""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway import auth as gw_auth
from gateway import client as gw_client
from gateway import server as gw_server
from gateway.server import GatewayServer, get_log_file, get_pid_file, is_server_running


@pytest.fixture
def manager(tmp_path):
    return gw_auth.AntigravityAuthManager(auth_file=tmp_path / "tokens.json")


async def _client(manager, stub) -> TestClient:
    server = GatewayServer(auth_manager=manager, client=stub)
    tc = TestClient(TestServer(server.app))
    await tc.start_server()
    return tc


class StubClient:
    def __init__(self, result=None, error: Exception | None = None, stream_chunks=None, mid_stream_error=None):
        self.result, self.error, self.stream_chunks = result, error, stream_chunks or []
        self.mid_stream_error = mid_stream_error

    async def close(self):
        pass

    async def create_chat_completion(self, payload, bearer_token=""):
        if self.error:
            raise self.error
        return self.result

    async def stream_chat_completion(self, payload, bearer_token=""):
        if self.error:
            raise self.error
        for c in self.stream_chunks:
            yield c
        if self.mid_stream_error:
            raise self.mid_stream_error


# ---------- get_pid_file / get_log_file ----------


def test_get_pid_file_and_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    pid_file = get_pid_file()
    assert pid_file.name == "gateway.pid"
    log_file = get_log_file()
    assert log_file.name == "gateway.log"
    assert log_file.parent.is_dir()  # mkdir(parents=True) đã chạy


# ---------- handle_auth_login ----------


@pytest.mark.asyncio
async def test_handle_auth_login_success(manager, monkeypatch):
    creds = gw_auth.AntigravityCredentials(access_token="t", email="new@example.com", project_id="proj-z")
    monkeypatch.setattr(manager, "login_pkce", lambda: creds)
    tc = await _client(manager, StubClient())
    try:
        r = await tc.post("/auth/login")
        body = await r.json()
    finally:
        await tc.close()
    assert r.status == 200
    assert body == {"ok": True, "email": "new@example.com", "project_id": "proj-z"}


@pytest.mark.asyncio
async def test_handle_auth_login_failure(manager, monkeypatch):
    def boom():
        raise RuntimeError("state mismatch")

    monkeypatch.setattr(manager, "login_pkce", boom)
    tc = await _client(manager, StubClient())
    try:
        r = await tc.post("/auth/login")
        body = await r.json()
    finally:
        await tc.close()
    assert r.status == 500
    assert body == {"ok": False, "error": "state mismatch"}


# ---------- _refresh_catalog ----------


@pytest.mark.asyncio
async def test_refresh_catalog_short_circuits_when_not_stale(manager, monkeypatch):
    monkeypatch.setattr(gw_server, "discovery_is_stale", lambda: False)
    called = {"n": 0}
    monkeypatch.setattr(manager, "resolve_credential_candidates", lambda: called.__setitem__("n", called["n"] + 1))
    server = GatewayServer(auth_manager=manager, client=StubClient())
    await server._refresh_catalog()
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_refresh_catalog_swallows_candidate_resolution_error(manager, monkeypatch, caplog):
    monkeypatch.setattr(gw_server, "discovery_is_stale", lambda: True)

    def boom():
        raise RuntimeError("no creds")

    monkeypatch.setattr(manager, "resolve_credential_candidates", boom)
    server = GatewayServer(auth_manager=manager, client=StubClient())
    with caplog.at_level("WARNING", logger="gateway.server"):
        await server._refresh_catalog()
    assert "Không lấy được tài khoản để dò model" in caplog.text


@pytest.mark.asyncio
async def test_refresh_catalog_per_candidate_fetch_failure_then_success(manager, monkeypatch, caplog):
    monkeypatch.setattr(gw_server, "discovery_is_stale", lambda: True)
    creds_bad = gw_auth.AntigravityCredentials(access_token="t1", email="bad@example.com", project_id="p1")
    creds_good = gw_auth.AntigravityCredentials(access_token="t2", email="good@example.com", project_id="p2")
    monkeypatch.setattr(manager, "resolve_credential_candidates", lambda: [creds_bad, creds_good])

    calls: list[str] = []

    def fake_fetch(token, project, **kw):
        calls.append(token)
        if token == "t1":
            raise RuntimeError("network down")
        return [{"id": "gemini-9", "name": "G9", "code_assist_model": "gemini-9"}]

    monkeypatch.setattr(gw_server, "fetch_available_models", fake_fetch)
    server = GatewayServer(auth_manager=manager, client=StubClient())
    try:
        with caplog.at_level("WARNING", logger="gateway.server"):
            await server._refresh_catalog()
        assert calls == ["t1", "t2"]
        assert "Dò model qua bad@example.com thất bại" in caplog.text
        assert {m["id"] for m in gw_client.discovered_models()} == {"gemini-9"}
    finally:
        gw_client.set_discovered_models([])


# ---------- is_server_running ----------


def test_is_server_running_true_and_false(monkeypatch):
    import urllib.request

    class _FakeResp:
        def read(self):
            return json.dumps({"service": "gateway"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=1.0: _FakeResp())
    assert is_server_running("127.0.0.1", 1123) is True

    def raise_conn_refused(url, timeout=1.0):
        raise ConnectionRefusedError("no server")

    monkeypatch.setattr(urllib.request, "urlopen", raise_conn_refused)
    assert is_server_running("127.0.0.1", 1123) is False


# ---------- handle_chat_completions: nhánh ngắt kết nối / lỗi giữa stream ----------
#
# Ba nhánh except ở cuối handle_chat_completions (233-239) chỉ bắt exception ném ra từ
# response.prepare/write/write_eof — KHÔNG phải exception từ generator (cái đó đã bị nuốt bởi
# try/except lồng bên trong quanh `async for chunk in gen`, xem dòng 226-231). Nên phải khiến
# chính lệnh gọi write() của aiohttp ném lỗi, không phải generator của client.


class _SimpleStreamStub:
    async def close(self):
        pass

    async def stream_chat_completion(self, payload, bearer_token=""):
        yield 'data: {"a": 1}\n\n'
        yield 'data: {"b": 2}\n\n'


@pytest.mark.asyncio
async def test_stream_connection_reset_during_write_is_handled_quietly(manager, monkeypatch, caplog):
    from aiohttp import web

    async def raising_write_eof(self, *a, **kw):
        raise ConnectionResetError("client đã đóng kết nối")

    monkeypatch.setattr(web.StreamResponse, "write_eof", raising_write_eof)
    tc = await _client(manager, _SimpleStreamStub())
    try:
        with caplog.at_level("DEBUG", logger="gateway.server"):
            r = await tc.post("/v1/chat/completions", json={"model": "m", "messages": [], "stream": True})
    finally:
        await tc.close()
    assert r.status == 200
    assert "Client ngắt giữa stream" in caplog.text


@pytest.mark.asyncio
async def test_stream_generic_unexpected_error_during_write_logged_as_error(manager, monkeypatch, caplog):
    from aiohttp import web

    async def raising_write_eof(self, *a, **kw):
        raise RuntimeError("lỗi hoàn toàn khác, chẳng liên quan gì")

    monkeypatch.setattr(web.StreamResponse, "write_eof", raising_write_eof)
    tc = await _client(manager, _SimpleStreamStub())
    try:
        with caplog.at_level("DEBUG", logger="gateway.server"):
            r = await tc.post("/v1/chat/completions", json={"model": "m", "messages": [], "stream": True})
    finally:
        await tc.close()
    assert r.status == 200
    assert "Lỗi bất ngờ khi stream" in caplog.text


@pytest.mark.asyncio
async def test_stream_closing_transport_message_during_write_is_handled_quietly(manager, monkeypatch, caplog):
    """Exception không thuộc 3 lớp Connection* nhưng message chứa 'closing transport' -> vẫn nuốt êm."""
    from aiohttp import web

    async def raising_write_eof(self, *a, **kw):
        raise RuntimeError("Cannot write to closing transport")

    monkeypatch.setattr(web.StreamResponse, "write_eof", raising_write_eof)
    tc = await _client(manager, _SimpleStreamStub())
    try:
        with caplog.at_level("DEBUG", logger="gateway.server"):
            r = await tc.post("/v1/chat/completions", json={"model": "m", "messages": [], "stream": True})
    finally:
        await tc.close()
    assert r.status == 200
    assert "Mất kết nối client giữa stream" in caplog.text


# ---------- run_server ----------


def test_run_server_wires_warn_and_web_run_app(monkeypatch):
    calls = {}
    monkeypatch.setattr(gw_server, "warn_if_public_host", lambda host: calls.setdefault("warned", host))

    def fake_run_app(app, host=None, port=None):
        calls["run_app"] = (host, port)

    monkeypatch.setattr(gw_server.web, "run_app", fake_run_app)
    gw_server.run_server(host="0.0.0.0", port=9999)
    assert calls["warned"] == "0.0.0.0"
    assert calls["run_app"] == ("0.0.0.0", 9999)
