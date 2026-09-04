"""Hành vi xoay vòng tài khoản của AntigravityClient (mock transport, không mạng)."""

from __future__ import annotations

import json

import httpx
import pytest

from gateway import auth as gw_auth
from gateway.client import AntigravityClient, cooldown_hint, reset_hint_seconds


class FakeAuthManager:
    def __init__(self) -> None:
        self.accounts = [
            gw_auth.AntigravityCredentials(access_token="token-a", email="a@example.com", project_id="project-a"),
            gw_auth.AntigravityCredentials(access_token="token-b", email="b@example.com", project_id="project-b"),
        ]
        self.marked: list[tuple[str, int]] = []
        self.hints: list[str | None] = []

    def resolve_credential_candidates(self, bearer_token: str = ""):
        return list(self.accounts)

    def mark_account_unavailable(self, creds, status_code: int, retry_after=None) -> None:
        self.marked.append((creds.email, status_code))
        self.hints.append(retry_after)


def _ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"response": {"candidates": [{"content": {"parts": [{"text": text}]}}]}})


def _client(auth: FakeAuthManager, handler) -> AntigravityClient:
    client = AntigravityClient(auth)
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def _payload(stream: bool = False) -> dict:
    p = {"model": "gemini-3.7-flash", "messages": [{"role": "user", "content": "hello"}]}
    if stream:
        p["stream"] = True
    return p


def _token(request: httpx.Request) -> str:
    return request.headers["Authorization"].removeprefix("Bearer ")


@pytest.mark.asyncio
async def test_in_account_model_fallback_before_rotating():
    auth = FakeAuthManager()
    seen: list[tuple[str, str]] = []

    def handler(request):
        token, model = _token(request), json.loads(request.content).get("model", "")
        seen.append((token, model))
        if token == "token-a" and model == "gemini-3.7-flash-high":
            return httpx.Response(429, headers={"Retry-After": "60"}, json={"error": {"message": "RESOURCE_EXHAUSTED"}})
        if token == "token-a" and model == "claude-sonnet-4-6":
            return _ok("SAME_ACCOUNT_CLAUDE_OK")
        raise AssertionError(f"unexpected {token} {model}")

    client = _client(auth, handler)
    try:
        result = await client.create_chat_completion(_payload())
    finally:
        await client.close()
    assert result["choices"][0]["message"]["content"] == "SAME_ACCOUNT_CLAUDE_OK"
    assert seen == [("token-a", "gemini-3.7-flash-high"), ("token-a", "claude-sonnet-4-6")]
    assert auth.marked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_response, expected_status",
    [
        (httpx.Response(429, headers={"Retry-After": "60"}, json={"error": {"message": "RESOURCE_EXHAUSTED"}}), 429),
        (httpx.Response(401, json={"error": "invalid_token"}), 401),
        (httpx.Response(400, json={"error": {"status": "RESOURCE_EXHAUSTED"}}), 400),
    ],
)
async def test_nonstream_rotates_to_next_account(first_response, expected_status):
    auth = FakeAuthManager()
    seen: list[str] = []

    def handler(request):
        seen.append(_token(request))
        return first_response if _token(request) == "token-a" else _ok("SECOND_ACCOUNT_OK")

    client = _client(auth, handler)
    try:
        result = await client.create_chat_completion(_payload())
    finally:
        await client.close()
    assert result["choices"][0]["message"]["content"] == "SECOND_ACCOUNT_OK"
    # token-a: model chính, rồi model anh em cùng tài khoản, rồi mới xoay sang token-b
    assert seen == ["token-a", "token-a", "token-b"]
    assert auth.marked == [("a@example.com", expected_status)]


@pytest.mark.asyncio
async def test_primary_5xx_tries_fallback_endpoint_same_account():
    auth = FakeAuthManager()
    seen: list[tuple[str, str]] = []

    def handler(request):
        seen.append((request.url.host, _token(request)))
        if request.url.host == "daily-cloudcode-pa.googleapis.com":
            return httpx.Response(503, json={"error": "temporarily unavailable"})
        return _ok("FALLBACK_OK")

    client = _client(auth, handler)
    try:
        result = await client.create_chat_completion(_payload())
    finally:
        await client.close()
    assert result["choices"][0]["message"]["content"] == "FALLBACK_OK"
    assert seen == [("daily-cloudcode-pa.googleapis.com", "token-a"), ("cloudcode-pa.googleapis.com", "token-a")]
    assert auth.marked == []


@pytest.mark.asyncio
async def test_non_account_4xx_raises_without_rotating():
    auth = FakeAuthManager()
    seen: list[str] = []

    def handler(request):
        seen.append(_token(request))
        return httpx.Response(400, json={"error": {"message": "invalid argument"}})

    client = _client(auth, handler)
    try:
        with pytest.raises(gw_auth.UpstreamError) as exc:
            await client.create_chat_completion(_payload())
    finally:
        await client.close()
    assert exc.value.status_code == 400
    assert seen == ["token-a"]
    assert auth.marked == []


@pytest.mark.asyncio
async def test_all_accounts_exhausted_raises_last_status():
    auth = FakeAuthManager()

    def handler(request):
        return httpx.Response(429, json={"error": {"message": "RESOURCE_EXHAUSTED"}})

    client = _client(auth, handler)
    try:
        with pytest.raises(gw_auth.UpstreamError) as exc:
            await client.create_chat_completion(_payload())
    finally:
        await client.close()
    assert exc.value.status_code == 429
    assert [m[0] for m in auth.marked] == ["a@example.com", "b@example.com"]


@pytest.mark.asyncio
async def test_stream_429_rotates_before_yielding():
    auth = FakeAuthManager()
    seen: list[str] = []

    def handler(request):
        seen.append(_token(request))
        if _token(request) == "token-a":
            return httpx.Response(429, headers={"Retry-After": "60"}, json={"error": {"message": "RESOURCE_EXHAUSTED"}})
        event = {"response": {"candidates": [{"content": {"parts": [{"text": "SECOND_STREAM_OK"}]}}]}}
        body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body.encode())

    client = _client(auth, handler)
    try:
        chunks = [c async for c in client.stream_chat_completion(_payload(stream=True))]
    finally:
        await client.close()
    assert "SECOND_STREAM_OK" in "".join(chunks)
    assert seen == ["token-a", "token-b"]
    assert auth.marked == [("a@example.com", 429)]


@pytest.mark.asyncio
async def test_stream_5xx_rotates_without_cooldown():
    auth = FakeAuthManager()
    seen: list[str] = []

    def handler(request):
        seen.append(_token(request))
        return httpx.Response(503, json={"error": "temporarily unavailable"})

    client = _client(auth, handler)
    try:
        with pytest.raises(gw_auth.UpstreamError, match="HTTP 503"):
            async for _ in client.stream_chat_completion(_payload(stream=True)):
                pass
    finally:
        await client.close()
    assert seen == ["token-a", "token-b"]
    assert auth.marked == []


@pytest.mark.asyncio
async def test_stream_tool_call_finish_reason_not_overwritten():
    auth = FakeAuthManager()

    def handler(request):
        event = {
            "response": {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"functionCall": {"name": "search", "args": {"q": "x"}}}]},
                    }
                ]
            }
        }
        body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body.encode())

    client = _client(auth, handler)
    try:
        chunks = [c async for c in client.stream_chat_completion(_payload(stream=True))]
    finally:
        await client.close()
    finish = [json.loads(c[6:])["choices"][0]["finish_reason"] for c in chunks if c.startswith("data: {")]
    assert finish == [None, "tool_calls"]


@pytest.mark.asyncio
async def test_usage_translated_from_gemini_metadata():
    auth = FakeAuthManager()

    def handler(request):
        return httpx.Response(
            200,
            json={
                "response": {
                    "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "MAX_TOKENS"}],
                    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 2,
                                      "cachedContentTokenCount": 4},
                }
            },
        )

    client = _client(auth, handler)
    try:
        result = await client.create_chat_completion(_payload())
    finally:
        await client.close()
    assert result["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 7,
        "total_tokens": 17,
        "prompt_tokens_details": {"cached_tokens": 4},
    }
    assert result["choices"][0]["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_stream_include_usage_emits_final_usage_only_chunk():
    auth = FakeAuthManager()

    def handler(request):
        event = {
            "response": {
                "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "hi"}]}}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 7, "totalTokenCount": 10},
            }
        }
        body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body.encode())

    payload = dict(_payload(stream=True), stream_options={"include_usage": True})
    client = _client(auth, handler)
    try:
        chunks = [c async for c in client.stream_chat_completion(payload)]
    finally:
        await client.close()
    objs = [json.loads(c[6:]) for c in chunks if c.startswith("data: {")]
    assert objs[-1]["choices"] == []
    assert objs[-1]["usage"]["total_tokens"] == 10
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_stream_without_include_usage_has_no_usage_only_chunk():
    auth = FakeAuthManager()

    def handler(request):
        event = {
            "response": {
                "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "hi"}]}}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 7, "totalTokenCount": 10},
            }
        }
        body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body.encode())

    client = _client(auth, handler)
    try:
        chunks = [c async for c in client.stream_chat_completion(_payload(stream=True))]
    finally:
        await client.close()
    objs = [json.loads(c[6:]) for c in chunks if c.startswith("data: {")]
    assert all(o["choices"] for o in objs)
    assert objs[-1]["usage"]["total_tokens"] == 10   # usage vẫn đi kèm chunk thường như trước


@pytest.mark.asyncio
async def test_upstream_error_body_is_trimmed_to_message():
    auth = FakeAuthManager()
    long_detail = "x" * 2000

    def handler(request):
        return httpx.Response(
            400, json={"error": {"message": "invalid argument", "details": [{"debug": long_detail}]}}
        )

    client = _client(auth, handler)
    try:
        with pytest.raises(gw_auth.UpstreamError) as exc:
            await client.create_chat_completion(_payload())
    finally:
        await client.close()
    assert str(exc.value) == "Code Assist lỗi HTTP 400: invalid argument"


@pytest.mark.asyncio
async def test_upstream_error_non_json_body_is_capped_at_500_chars():
    auth = FakeAuthManager()

    def handler(request):
        return httpx.Response(400, content=("<html>" + "y" * 3000).encode())

    client = _client(auth, handler)
    try:
        with pytest.raises(gw_auth.UpstreamError) as exc:
            await client.create_chat_completion(_payload())
    finally:
        await client.close()
    msg = str(exc.value).removeprefix("Code Assist lỗi HTTP 400: ")
    assert len(msg) == 500 and msg.endswith("…")


# --- log: lượt nào đi tài khoản nào -----------------------------------------
#
# Đây là thứ duy nhất cho phép người vận hành KIỂM CHỨNG việc xoay vòng thay vì phải tin vào
# test. Thiếu nó thì mọi lượt thành công trông giống hệt nhau, kể cả khi pool đã kẹt vào đúng
# một tài khoản — nên các test dưới đây coi dòng log là hành vi, không phải trang trí.

@pytest.mark.asyncio
async def test_log_ghi_tai_khoan_da_phuc_vu(caplog):
    auth = FakeAuthManager()
    client = _client(auth, lambda request: _ok("xong"))
    with caplog.at_level("INFO", logger="gateway.client"):
        await client.create_chat_completion(_payload())
    dong = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("a@example.com" in d and "gemini-3.7-flash" in d for d in dong), dong
    assert any("lần thử 1/2" in d for d in dong), dong   # đi thẳng, không phải bỏ qua ai


@pytest.mark.asyncio
async def test_log_ghi_ca_tai_khoan_bi_bo_qua_va_tai_khoan_thay_the(caplog):
    """Tài khoản đầu hết quota: log phải nói RÕ nó bị cho nghỉ, và lượt đó rơi sang tài khoản nào."""
    auth = FakeAuthManager()

    def handler(request):
        if _token(request) == "token-a":
            return httpx.Response(429, json={"error": {"message": "RESOURCE_EXHAUSTED"}})
        return _ok("xong")

    client = _client(auth, handler)
    with caplog.at_level("INFO", logger="gateway.client"):
        await client.create_chat_completion({"model": "gemini-3.8-flash-low",
                                             "messages": [{"role": "user", "content": "hi"}]})
    dong = [r.getMessage() for r in caplog.records]
    assert any("a@example.com" in d and "429" in d for d in dong), dong      # ai bị cho nghỉ
    assert any("b@example.com" in d and "lần thử 2/2" in d for d in dong), dong  # ai gánh lượt đó


@pytest.mark.asyncio
async def test_log_stream_cung_ghi_tai_khoan(caplog):
    auth = FakeAuthManager()

    def handler(request):
        return httpx.Response(200, text='data: {"response":{"candidates":[{"content":{"parts":[{"text":"hi"}]}}]}}\n\n',
                              headers={"Content-Type": "text/event-stream"})

    client = _client(auth, handler)
    with caplog.at_level("INFO", logger="gateway.client"):
        gen = client.stream_chat_completion(_payload(stream=True))
        await gen.__anext__()
        await gen.aclose()
    dong = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("a@example.com" in d and "stream" in d for d in dong), dong


# ---------- cooldown: đọc "Resets in ..." từ thân lỗi, không chỉ header Retry-After ----------


def test_reset_hint_parses_code_assist_wording():
    """Chuỗi thật Code Assist trả về khi hết hạn mức cá nhân (bắt được lúc chạy thật 2026-09-04)."""
    real = ("Code Assist lỗi HTTP 429: Individual quota reached. Please upgrade your "
            "subscription to increase your limits. Resets in 7m29s.")
    assert reset_hint_seconds(real) == 7 * 60 + 29
    assert reset_hint_seconds("Resets in 45s.") == 45
    assert reset_hint_seconds("resets in 1h5m") == 3900
    assert reset_hint_seconds("Resets in 2m") == 120
    # thông điệp gateway tự sinh khi cả pool cooldown (tiếng Việt) cũng phải đọc được
    assert reset_hint_seconds("Mọi tài khoản Antigravity đều đang cooldown. Thử lại sau khoảng 1543s.") == 1543
    assert reset_hint_seconds("thử lại sau 2m30s") == 150
    assert reset_hint_seconds("hết quota, không nói khi nào") is None
    assert reset_hint_seconds("") is None


def test_cooldown_hint_prefers_header_then_body():
    with_header = httpx.Response(429, headers={"Retry-After": "30"}, text="Resets in 7m29s.")
    assert cooldown_hint(with_header) == "30"
    body_only = httpx.Response(429, text="Individual quota reached. Resets in 7m29s.")
    assert cooldown_hint(body_only) == "449"
    assert cooldown_hint(httpx.Response(429, text="hết quota")) is None


@pytest.mark.asyncio
async def test_429_cooldown_uses_reset_hint_instead_of_one_hour_default():
    """Không đọc "Resets in" thì rơi vào COOLDOWN_DEFAULTS[429] = 3600s: tài khoản nghỉ 1 tiếng
    trong khi 7 phút nữa đã dùng lại được — lãng phí hạn mức gấp 8 lần trên pool nhiều tài khoản."""
    auth = FakeAuthManager()

    def handler(request: httpx.Request) -> httpx.Response:
        if _token(request) == "token-a":
            return httpx.Response(429, text="Individual quota reached. Resets in 7m29s.")
        return _ok("từ tài khoản b")

    client = _client(auth, handler)
    await client.create_chat_completion(_payload())
    assert auth.marked == [("a@example.com", 429)]
    assert auth.hints == ["449"], "phải truyền 449s xuống cooldown, không để mặc định 3600s"
