"""Gateway: proxy OpenAI-compatible xoay vòng tài khoản Google Antigravity.

Một daemon cục bộ nhận request theo chuẩn OpenAI `/v1/chat/completions`, dịch sang
Google Code Assist (Gemini/Claude qua đăng nhập Antigravity), và tự động:

- xoay sang tài khoản Google khác khi tài khoản hiện tại bị 401/402/403/429 hoặc hết quota;
- ghi cooldown từng tài khoản (tôn trọng `Retry-After`), tự làm mới access token;
- thử model dự phòng trên cùng tài khoản trước khi xoay (in-account model fallback);
- đổi endpoint dự phòng khi endpoint chính trả 5xx.

Mọi công ty trong X-Agents chỉ cần cấu hình provider `openai` với `base_url`
trỏ vào gateway, không đổi code hay prompt (xem `software-company/llm.py`).
"""

from __future__ import annotations

from gateway.auth import AntigravityAuthManager, AntigravityCredentials, UpstreamError
from gateway.client import ANTIGRAVITY_SUPPORTED_MODELS, AntigravityClient
from gateway.server import GatewayServer, run_server

__all__ = [
    "ANTIGRAVITY_SUPPORTED_MODELS",
    "AntigravityAuthManager",
    "AntigravityClient",
    "AntigravityCredentials",
    "GatewayServer",
    "UpstreamError",
    "run_server",
]
