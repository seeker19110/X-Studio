"""HTTP server: mã lỗi trả về, SSE, /auth/status, /v1/models (không gọi mạng)."""

from __future__ import annotations

import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway import auth as gw_auth
from gateway.server import GatewayServer, upstream_status


class StubClient:
    def __init__(self, result=None, error: Exception | None = None, stream_chunks=None):
        self.result, self.error, self.stream_chunks = result, error, stream_chunks or []
        self.bearers: list[str] = []

    async def close(self):
        pass

    async def create_chat_completion(self, payload, bearer_token=""):
        self.bearers.append(bearer_token)
        if self.error:
            raise self.error
        return self.result

    async def stream_chat_completion(self, payload, bearer_token=""):
        self.bearers.append(bearer_token)
        if self.error:
            raise self.error
        for c in self.stream_chunks:
            yield c


@pytest.fixture
def manager(tmp_path):
    return gw_auth.AntigravityAuthManager(auth_file=tmp_path / "tokens.json")


async def _client(manager, stub) -> TestClient:
    server = GatewayServer(auth_manager=manager, client=stub)
    tc = TestClient(TestServer(server.app))
    await tc.start_server()
    return tc


@pytest.mark.asyncio
async def test_health_and_models(manager):
    tc = await _client(manager, StubClient())
    try:
        assert (await (await tc.get("/health")).json())["service"] == "gateway"
        models = await (await tc.get("/v1/models")).json()
        assert "gemini-3.7-flash" in {m["id"] for m in models["data"]}
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_chat_completion_passes_bearer_and_strips_dummy(manager):
    stub = StubClient(result={"id": "x", "choices": []})
    tc = await _client(manager, stub)
    try:
        body = {"model": "m", "messages": []}
        r = await tc.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer gateway-local"})
        assert r.status == 200 and (await r.json())["id"] == "x"
        r = await tc.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer b@example.com"})
        assert r.status == 200
    finally:
        await tc.close()
    assert stub.bearers == ["", "b@example.com"]


@pytest.mark.asyncio
async def test_upstream_429_maps_to_rate_limit_error(manager):
    stub = StubClient(error=gw_auth.UpstreamError("Mọi tài khoản đều cooldown", 429))
    tc = await _client(manager, stub)
    try:
        r = await tc.post("/v1/chat/completions", json={"model": "m", "messages": [], "stream": False})
        assert r.status == 429
        assert (await r.json())["error"]["type"] == "rate_limit_error"
        r = await tc.post("/v1/chat/completions", json={"model": "m", "messages": [], "stream": True})
        assert r.status == 429
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_invalid_json_is_400(manager):
    tc = await _client(manager, StubClient())
    try:
        r = await tc.post("/v1/chat/completions", data=b"{not json")
        assert r.status == 400
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_stream_passthrough(manager):
    chunks = ['data: {"a": 1}\n\n', "data: [DONE]\n\n"]
    tc = await _client(manager, StubClient(stream_chunks=chunks))
    try:
        r = await tc.post("/v1/chat/completions", json={"model": "m", "messages": [], "stream": True})
        assert r.status == 200 and r.headers["Content-Type"].startswith("text/event-stream")
        assert (await r.text()) == "".join(chunks)
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_auth_status_lists_pool(manager):
    a = gw_auth.AntigravityCredentials(access_token="t", email="a@example.com", project_id="p")
    b = gw_auth.AntigravityCredentials(access_token="t", email="b@example.com", project_id="p")
    manager.save_credentials(a)
    manager.save_credentials(b)
    manager.mark_account_unavailable(a, 429, retry_after="60")
    tc = await _client(manager, StubClient())
    try:
        data = await (await tc.get("/auth/status")).json()
    finally:
        await tc.close()
    assert data["total"] == 2 and data["available"] == 1
    by_email = {x["email"]: x for x in data["accounts"]}
    assert by_email["a@example.com"]["cooldown_remaining"] > 0
    assert by_email["a@example.com"]["last_failure_status"] == 429
    assert by_email["b@example.com"]["cooldown_remaining"] == 0
    assert "access_token" not in json.dumps(data)


def test_upstream_status_guesses():
    assert upstream_status(gw_auth.UpstreamError("x", 403)) == 403
    assert upstream_status(RuntimeError("quota exhausted")) == 429
    assert upstream_status(RuntimeError("boom")) == 500
