"""HTTP server OpenAI-compatible của gateway (aiohttp).

Endpoint:
- GET  /health
- GET  /v1/models
- POST /v1/chat/completions   (hỗ trợ `stream: true` qua SSE)
- GET  /auth/status           (toàn bộ pool: email, cooldown, hạn token)
- POST /auth/login            (mở trình duyệt thêm tài khoản)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path

from aiohttp import web

from gateway.auth import AntigravityAuthManager, get_gateway_dir, get_home_dir
from gateway.client import ANTIGRAVITY_SUPPORTED_MODELS, AntigravityClient

logger = logging.getLogger(__name__)

DEFAULT_HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("GATEWAY_PORT", "8100"))
_DUMMY_BEARERS = {"dummy", "none", "token", "default", "antigravity", "gateway-local", "sk-gateway"}


def get_pid_file() -> Path:
    return get_gateway_dir() / "gateway.pid"


def get_log_file() -> Path:
    log_dir = get_home_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "gateway.log"


def upstream_status(exc: Exception) -> int:
    """Mã HTTP thật từ UpstreamError; lỗi khác đoán an toàn từ nội dung."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status < 600:
        return status
    text = str(exc).lower()
    if "429" in text or "exhausted" in text or "cooldown" in text:
        return 429
    return 500


def _error_response(exc: Exception) -> web.Response:
    status = upstream_status(exc)
    err_type = "rate_limit_error" if status == 429 else "api_error"
    return web.json_response({"error": {"message": str(exc), "type": err_type, "code": status}}, status=status)


class GatewayServer:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        auth_manager: AntigravityAuthManager | None = None,
        client: AntigravityClient | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.auth_manager = auth_manager or AntigravityAuthManager()
        self.client = client or AntigravityClient(self.auth_manager)
        self.app = web.Application()
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/auth/status", self.handle_auth_status)
        self.app.router.add_post("/auth/login", self.handle_auth_login)
        self.app.router.add_get("/v1/models", self.handle_list_models)
        self.app.router.add_post("/v1/chat/completions", self.handle_chat_completions)
        self.app.on_cleanup.append(self._cleanup)

    async def _cleanup(self, _app: web.Application) -> None:
        await self.client.close()

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "gateway", "version": "0.1.0", "timestamp": time.time()})

    async def handle_auth_status(self, request: web.Request) -> web.Response:
        now = time.time()
        accounts = []
        for c in self.auth_manager.load_all_stored_credentials():
            accounts.append(
                {
                    "email": c.email,
                    "project_id": c.project_id,
                    "expires_at": c.expires_at,
                    "is_expired": c.is_expired,
                    "has_refresh_token": bool(c.refresh_token),
                    "cooldown_remaining": max(0, int(c.unavailable_until - now)),
                    "last_failure_status": c.last_failure_status,
                    "source": c.source,
                }
            )
        available = sum(1 for a in accounts if a["cooldown_remaining"] == 0)
        return web.json_response(
            {"logged_in": bool(accounts), "total": len(accounts), "available": available, "accounts": accounts}
        )

    async def handle_auth_login(self, request: web.Request) -> web.Response:
        try:
            creds = await asyncio.to_thread(self.auth_manager.login_pkce)
            return web.json_response({"ok": True, "email": creds.email, "project_id": creds.project_id})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def handle_list_models(self, request: web.Request) -> web.Response:
        created = int(time.time())
        return web.json_response(
            {
                "object": "list",
                "data": [
                    {"id": m["id"], "object": "model", "created": created, "owned_by": "antigravity", "name": m["name"]}
                    for m in ANTIGRAVITY_SUPPORTED_MODELS
                ],
            }
        )

    async def handle_chat_completions(self, request: web.Request) -> web.StreamResponse:
        try:
            payload = await request.json()
        except Exception as e:
            return web.json_response(
                {"error": {"message": f"JSON không hợp lệ: {e}", "type": "invalid_request_error"}}, status=400
            )
        auth_header = request.headers.get("Authorization") or ""
        bearer = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
        if bearer in _DUMMY_BEARERS:
            bearer = ""

        if not payload.get("stream"):
            try:
                return web.json_response(await self.client.create_chat_completion(payload, bearer_token=bearer))
            except Exception as e:
                logger.error("Chat completion lỗi: %s", e)
                return _error_response(e)

        gen = self.client.stream_chat_completion(payload, bearer_token=bearer)
        try:
            first = await gen.__anext__()
        except Exception as e:
            logger.error("Không mở được stream: %s", e)
            with contextlib.suppress(Exception):
                await gen.aclose()
            return _error_response(e)

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        try:
            await response.prepare(request)
            await response.write(first.encode("utf-8"))
            try:
                async for chunk in gen:
                    await response.write(chunk.encode("utf-8"))
            except Exception as e:
                logger.error("Lỗi giữa stream: %s", e)
            await response.write_eof()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
            logger.debug("Client ngắt giữa stream: %s", e)
        except Exception as e:
            if "closing transport" in str(e).lower() or "connection" in str(e).lower():
                logger.debug("Mất kết nối client giữa stream: %s", e)
            else:
                logger.error("Lỗi bất ngờ khi stream: %s", e)
        finally:
            # Đóng generator để trả kết nối httpx về pool ngay cả khi client ngắt giữa chừng.
            with contextlib.suppress(Exception):
                await gen.aclose()
        return response


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = GatewayServer(host=host, port=port)
    web.run_app(server.app, host=host, port=port)


def is_server_running(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1.0) as resp:
            return json.loads(resp.read().decode("utf-8")).get("service") == "gateway"
    except Exception:
        return False
