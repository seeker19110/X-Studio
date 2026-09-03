"""Client Google Code Assist và bộ dịch request/response.

- Giả lập header của Antigravity IDE.
- Dịch hai chiều OpenAI Chat Completions ↔ Gemini Code Assist (tool use, thought signature, multimodal).
- Xoay vòng tài khoản: 401/402/403/429 hoặc body báo hết quota → cooldown tài khoản, thử tài khoản kế.
- In-account model fallback: model chính hết quota → thử model anh em (quota riêng) trên CÙNG tài khoản
  trước khi xoay tài khoản.
- 5xx ở endpoint chính → thử endpoint dự phòng cùng tài khoản; stream 5xx → tài khoản kế, không cooldown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from gateway.auth import DEFAULT_PROJECT_ID, AntigravityAuthManager, UpstreamError

logger = logging.getLogger(__name__)

CODE_ASSIST_BASE_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal"
FALLBACK_CODE_ASSIST_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"

# Code Assist ẨN model mới với client khai phiên bản cũ: cùng một tài khoản, `gemini-3.8-flash-medium`
# trả 404 khi Client-Metadata không có ideVersion/pluginVersion, và trả 200 khi có (id bịa vẫn 404 —
# đã đối chứng). Khai quá cũ là 3.7/3.8 vô hình mà không có lỗi nào báo.
ANTIGRAVITY_CLIENT_VERSION = os.getenv("GATEWAY_ANTIGRAVITY_CLIENT_VERSION") or "2026.9.1"

# Danh sách model THẬT do upstream tự khai qua `:fetchAvailableModels` (xem discovery bên dưới).
# Đây chỉ là lưới an toàn cho lúc chưa dò được (mất mạng, pool trống): đủ để gateway còn phục vụ,
# cố tình KHÔNG chép lại cả catalog — chép tay là lại lạc hậu như lần trước.
FALLBACK_MODELS = [
    {"id": "gemini-3.8-flash-medium", "name": "Gemini 3.8 Flash (Medium)", "code_assist_model": "gemini-3.8-flash-medium"},
    {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)", "code_assist_model": "claude-sonnet-4-6"},
]

MODEL_ALIAS_MAP = {
    **{f"gemini-{v}-flash{suffix}": f"gemini-{v}-flash{eff}"
       for v in ("3.8", "3.7", "3.6")
       for suffix, eff in (("", "-high"), ("-high", "-high"), ("-medium", "-medium"), ("-low", "-low"))},
    # Tên đã chết hoặc chưa từng có, giữ để llm.yaml cũ không gãy (probe: 404 hoặc "no longer available").
    "gemini-3-flash": "gemini-3.8-flash-high",
    "gemini-3-flash-agent": "gemini-3.8-flash-high",
    "gemini-3.5-flash": "gemini-3.8-flash-medium",
    "gemini-3.5-flash-medium": "gemini-3.8-flash-medium",
    "gemini-2.5-flash": "gemini-3.8-flash-medium",
    # Tên rút gọn hay gặp; id thật của upstream có hậu tố (đã đối chiếu :fetchAvailableModels).
    "gpt-oss-120b": "gpt-oss-120b-medium",
    "gpt-oss-120b-medium": "gpt-oss-120b-medium",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
    "gemini-pro-agent": "gemini-pro-agent",
    "gemini-3.1-pro": "gemini-3.1-pro-low",
    "gemini-3.1-pro-low": "gemini-3.1-pro-low",
    "gemini-3-pro": "gemini-3.1-pro-low",
    "gemini-3-pro-low": "gemini-3.1-pro-low",
    "gemini-3.7-pro": "gemini-3.1-pro-low",
    "gemini-3.8-pro": "gemini-3.1-pro-low",
    "gemini-2.5-pro": "gemini-3.1-pro-low",
    "gemini-pro": "gemini-3.1-pro-low",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-3-7-sonnet": "claude-sonnet-4-6",
    "claude-3.7-sonnet": "claude-sonnet-4-6",
    "claude-opus-4-6": "claude-opus-4-6-thinking",
    "claude-opus-4.6": "claude-opus-4-6-thinking",
    "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
    "claude-3-7-opus": "claude-opus-4-6-thinking",
    "claude-3.7-opus": "claude-opus-4-6-thinking",
}

# Suy ra, không khai tay: mọi đích của alias phải hợp lệ, cộng với lưới an toàn ở trên.
VALID_CODE_ASSIST_MODELS = {m["id"] for m in FALLBACK_MODELS} | set(MODEL_ALIAS_MAP.values())

DEFAULT_CODE_ASSIST_MODEL = "gemini-3.8-flash-medium"

# Gemini và Claude tính quota độc lập trên Antigravity: hết quota Gemini thì thử Claude
# trên cùng tài khoản trước khi đốt sang tài khoản khác. Khóa theo id nội bộ (sau map_model_name).
IN_ACCOUNT_MODEL_FALLBACK = {m: "claude-sonnet-4-6" for m in VALID_CODE_ASSIST_MODELS if m.startswith("gemini-")}

_QUOTA_MARKERS = ("resource_exhausted", "rate limit", "quota", "invalid_grant", "token expired")


def _should_fail_over(response: httpx.Response) -> bool:
    """Tài khoản khác có thể cứu được lỗi này không?"""
    if response.status_code in {401, 402, 403, 429} or response.status_code >= 500:
        return True
    body = response.text.lower()
    return any(marker in body for marker in _QUOTA_MARKERS)


class UnknownModelError(ValueError):
    """Client xin một model gateway không biết. Trả 400 thay vì âm thầm chạy model khác:
    cấu hình sai tên model phải nổ ngay, không được lặng lẽ tụt xuống flash."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status_code = 400


MODELS_ENDPOINT = f"{CODE_ASSIST_BASE_URL}:fetchAvailableModels"
MODEL_CATALOG_TTL_S = 3600.0
_MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{1,127}$")

# Catalog động: id upstream tự khai. Rỗng cho tới lần discovery đầu tiên thành công — khi đó
# bảng tĩnh ở trên chỉ còn là lưới an toàn cho lúc mạng hỏng.
_discovered: dict[str, dict[str, str]] = {}
_discovered_at: float = 0.0


def parse_available_models(payload: Any) -> list[dict[str, str]]:
    """Lọc catalog `:fetchAvailableModels` xuống những model thật sự chọn được.
    Bỏ model nội bộ, id không hợp lệ, và mục không có mô tả trong `models`."""
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), dict):
        return []
    selectable: set[str] = set()
    for sort in payload.get("agentModelSorts") or []:
        for group in (sort or {}).get("groups") or []:
            selectable.update(m for m in (group or {}).get("modelIds") or [] if isinstance(m, str))
    for ids in (payload.get("tieredModelIds") or {}).values():
        selectable.update(m for m in ids or [] if isinstance(m, str))
    deprecated = {m for m in payload.get("deprecatedModelIds") or [] if isinstance(m, str)}

    out: list[dict[str, str]] = []
    for model_id in sorted(selectable - deprecated):
        details = payload["models"].get(model_id)
        if not _MODEL_ID_RE.fullmatch(model_id) or not isinstance(details, dict) or details.get("isInternal") is True:
            continue
        name = str(details.get("displayName") or "").strip()
        if not name:   # mục không có tên hiển thị (vd. bí danh "-tiered") không phải model để người dùng chọn
            continue
        out.append({"id": model_id, "name": name, "code_assist_model": model_id})
    return out


def fetch_available_models(access_token: str, project_id: str, timeout: float = 30.0) -> list[dict[str, str]]:
    """Hỏi thẳng upstream xem tài khoản này được dùng model nào."""
    with httpx.Client(timeout=timeout) as http:
        resp = http.post(
            MODELS_ENDPOINT, json={"project": project_id}, headers=build_antigravity_headers(access_token, project_id)
        )
    resp.raise_for_status()
    return parse_available_models(resp.json())


def set_discovered_models(models: list[dict[str, str]]) -> None:
    global _discovered_at
    _discovered.clear()
    _discovered.update({m["id"]: m for m in models})
    _discovered_at = time.time()


def discovered_models() -> list[dict[str, str]]:
    return list(_discovered.values())


def discovery_is_stale() -> bool:
    return not _discovered or (time.time() - _discovered_at) > MODEL_CATALOG_TTL_S


def serving_models() -> list[dict[str, str]]:
    """Danh sách phục vụ `/v1/models`: catalog upstream nếu đã dò được, nếu chưa thì bảng tĩnh."""
    return discovered_models() or list(FALLBACK_MODELS)


def strict_models_enabled() -> bool:
    """Mặc định BẬT. `GATEWAY_STRICT_MODELS=0` để quay lại hành vi cũ (fallback + warning)."""
    return (os.getenv("GATEWAY_STRICT_MODELS") or "1").strip().lower() not in {"0", "false", "no", "off"}


def known_model_ids() -> list[str]:
    """Tên model client được phép gửi: id upstream tự khai (nếu đã dò được) + bảng tĩnh + mọi alias."""
    return sorted({m["id"] for m in serving_models()} | VALID_CODE_ASSIST_MODELS | set(MODEL_ALIAS_MAP))


def map_model_name(requested_model: str) -> str:
    if not requested_model:
        return DEFAULT_CODE_ASSIST_MODEL
    normalized = requested_model.lower().strip()
    if "/" in normalized:
        normalized = normalized.split("/", 1)[1]
    mapped = MODEL_ALIAS_MAP.get(normalized, normalized)
    # Id upstream vừa khai được chấp nhận ngay, không cần chờ ai sửa bảng tĩnh.
    if mapped in _discovered:
        return mapped
    if mapped not in VALID_CODE_ASSIST_MODELS:
        if strict_models_enabled():
            raise UnknownModelError(
                f"Model '{requested_model}' không được gateway hỗ trợ. "
                f"Model hợp lệ: {', '.join(known_model_ids())}. "
                "Xem `python -m gateway models --probe`; đặt GATEWAY_STRICT_MODELS=0 để tạm chấp nhận fallback."
            )
        logger.warning("Model lạ '%s', dùng %s", requested_model, DEFAULT_CODE_ASSIST_MODEL)
        return DEFAULT_CODE_ASSIST_MODEL
    return mapped


# Model đã nghỉ hưu vẫn trả 200 nhưng nội dung chỉ là một câu thông báo (không phải câu trả lời thật):
# "Gemini 3.5 Flash is no longer available. Please switch to ...". Probe phải bắt được ca này,
# nếu không sẽ tưởng model còn sống.
_RETIRED_MARKERS = ("no longer available", "please switch to")

PROBE_OK = "OK"
PROBE_RETIRED = "NGHỈ HƯU"
PROBE_MISSING = "KHÔNG TỒN TẠI"
PROBE_QUOTA = "HẾT QUOTA"
PROBE_ERROR = "LỖI"


def classify_probe(status: int, body: str) -> tuple[str, str]:
    """(kết luận, ghi chú) từ một lần gọi thử generateContent."""
    text = (body or "")
    low = text.lower()
    if status == 200:
        if any(m in low for m in _RETIRED_MARKERS):
            snippet = " ".join(text.split())
            i = low.find("no longer available")
            start = max(0, i - 60)
            return PROBE_RETIRED, snippet[start : start + 160].strip()
        return PROBE_OK, ""
    if status == 404:
        return PROBE_MISSING, ""
    if status in {429, 402, 403}:
        return PROBE_QUOTA, f"HTTP {status} — không kết luận được, thử lại sau"
    return PROBE_ERROR, f"HTTP {status}: {' '.join(text.split())[:120]}"


def build_probe_envelope(code_assist_model: str, project_id: str) -> dict[str, Any]:
    """Request nhỏ nhất có thể để hỏi upstream 'model này có tồn tại không'."""
    return {
        "model": code_assist_model,
        "project": project_id,
        "request": {
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        },
    }


def probe_code_assist_model(code_assist_model: str, access_token: str, project_id: str, timeout: float = 60.0) -> tuple[int, str]:
    """Gọi thật một request 1 token lên Code Assist để biết model có tồn tại không.
    Google không có endpoint liệt kê model, nên đây là cách duy nhất kiểm chứng."""
    with httpx.Client(timeout=timeout) as http:
        resp = http.post(
            f"{CODE_ASSIST_BASE_URL}:generateContent",
            json=build_probe_envelope(code_assist_model, project_id),
            headers=build_antigravity_headers(access_token, project_id),
        )
    return resp.status_code, resp.text


def build_antigravity_headers(access_token: str, project_id: str = "") -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": f"Antigravity/{ANTIGRAVITY_CLIENT_VERSION} (Windows NT 10.0; Win64; x64) Code-Assist/2026.x",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1 gccl/antigravity-ide",
        "Client-Metadata": json.dumps(
            {
                "ideType": "ANTIGRAVITY",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
                # Thiếu hai khoá này là server giấu Gemini 3.7/3.8 và trả 404 như thể model không tồn tại.
                "ideVersion": ANTIGRAVITY_CLIENT_VERSION,
                "pluginVersion": ANTIGRAVITY_CLIENT_VERSION,
            },
            separators=(",", ":"),
        ),
        "x-activity-request-id": str(uuid.uuid4()),
    }


# ---------- OpenAI → Gemini ----------


def _coerce_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                pieces.append(part["text"])
        return "\n".join(pieces)
    return str(content)


def _coerce_content_to_parts(content: Any) -> list[dict[str, Any]]:
    """Nội dung OpenAI/Anthropic/Gemini (text, image_url base64, inlineData) → Gemini parts."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}] if content else []
    if not isinstance(content, list):
        text = str(content)
        return [{"text": text}] if text else []
    parts: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            if part:
                parts.append({"text": part})
            continue
        if not isinstance(part, dict):
            continue
        ptype = str(part.get("type", ""))
        if ptype == "text" and isinstance(part.get("text"), str):
            if part["text"]:
                parts.append({"text": part["text"]})
        elif ptype == "image_url" or "image_url" in part:
            img = part.get("image_url")
            url = img.get("url") if isinstance(img, dict) else (img if isinstance(img, str) else "")
            if isinstance(url, str) and url.startswith("data:"):
                try:
                    header, encoded = url.split(",", 1)
                    mime = header.split(":", 1)[1].split(";", 1)[0]
                    parts.append({"inlineData": {"mimeType": mime or "image/jpeg", "data": encoded}})
                except Exception:
                    pass
        elif ptype == "image" and isinstance(part.get("source"), dict):
            src = part["source"]
            if src.get("type") == "base64" and src.get("data"):
                parts.append({"inlineData": {"mimeType": src.get("media_type") or "image/jpeg", "data": src["data"]}})
        elif isinstance(part.get("inlineData"), dict):
            parts.append({"inlineData": part["inlineData"]})
        elif isinstance(part.get("text"), str) and part["text"]:
            parts.append({"text": part["text"]})
    return parts


def _real_thought_signature(tool_call: dict[str, Any]) -> str:
    sig = tool_call.get("thoughtSignature") or ""
    if not sig or sig == "skip_thought_signature_validator":
        extra = tool_call.get("extra_content")
        if isinstance(extra, dict):
            google = extra.get("google") or extra.get("thought_signature")
            if isinstance(google, dict):
                sig = google.get("thought_signature") or google.get("thoughtSignature") or ""
    return "" if sig == "skip_thought_signature_validator" else str(sig or "")


def _translate_tool_call_to_gemini(tool_call: dict[str, Any]) -> dict[str, Any]:
    fn = tool_call.get("function") or {}
    args_raw = fn.get("arguments", "")
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) and args_raw else {}
    except Exception:
        args = {"_raw": args_raw}
    if not isinstance(args, dict):
        args = {"_value": args}
    function_call: dict[str, Any] = {"name": fn.get("name") or "", "args": args}
    if tool_call.get("id"):
        function_call["id"] = str(tool_call["id"])
    # Code Assist bắt buộc thoughtSignature ở mọi functionCall.
    return {
        "functionCall": function_call,
        "thoughtSignature": _real_thought_signature(tool_call) or "skip_thought_signature_validator",
    }


def _sanitize_gemini_schema_node(node: Any) -> Any:
    """Chuẩn hóa JSON Schema cho validator của Code Assist: gỡ nullable union (anyOf/oneOf với null,
    type dạng mảng), bỏ `default` cạnh `$ref`, bỏ `nullable`. Chạy trên mọi tool vì một schema hỏng
    làm 400 cả request."""
    if isinstance(node, list):
        return [_sanitize_gemini_schema_node(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {k: _sanitize_gemini_schema_node(v) for k, v in node.items()}
    for key in ("anyOf", "oneOf"):
        variants = out.get(key)
        if not isinstance(variants, list):
            continue
        non_null = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
        if len(non_null) == 1 and len(non_null) != len(variants):
            replacement = dict(non_null[0]) if isinstance(non_null[0], dict) else {}
            for meta_key in ("title", "description", "default"):
                if meta_key in out and meta_key not in replacement:
                    if meta_key == "default" and "$ref" in replacement:
                        continue
                    replacement[meta_key] = out[meta_key]
            out = {k: v for k, v in out.items() if k not in (key, "title", "description", "default")}
            out.update(replacement)
        elif not non_null and variants:
            out.pop(key, None)
            out.setdefault("type", "string")
    type_val = out.get("type")
    if isinstance(type_val, list):
        non_null_types = [t for t in type_val if t != "null"]
        out["type"] = non_null_types[0] if non_null_types else "string"
    if "$ref" in out:
        out.pop("default", None)
    out.pop("nullable", None)
    return out


def _translate_tools_to_gemini(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list) or not tools:
        return []
    declarations: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or {}
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        decl: dict[str, Any] = {"name": str(fn["name"])}
        if fn.get("description"):
            decl["description"] = str(fn["description"])
        if isinstance(fn.get("parameters"), dict):
            decl["parameters"] = _sanitize_gemini_schema_node(fn["parameters"])
        declarations.append(decl)
    return [{"functionDeclarations": declarations}] if declarations else []


def _translate_tool_choice_to_gemini(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        mode = {"auto": "AUTO", "required": "ANY", "none": "NONE"}.get(tool_choice)
        return {"functionCallingConfig": {"mode": mode}} if mode else None
    if isinstance(tool_choice, dict):
        name = (tool_choice.get("function") or {}).get("name")
        if name:
            return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [str(name)]}}
    return None


_TOOL_CALL_TEXT_PATTERNS = (
    re.compile(r"\[Tool call:\s*([a-zA-Z0-9_\-\.]+)\s*\((.*?)\)\]", re.DOTALL),
    re.compile(r"Action:\s*(?:Called\s+)?([a-zA-Z0-9_\-\.]+)\s*\((.*?)\)", re.DOTALL),
)


def _extract_tool_calls_from_text(text: str) -> tuple[list[dict[str, Any]], str]:
    """Model đôi khi in `[Tool call: name(args)]` dạng text thay vì functionCall — dựng lại tool call thật."""
    tool_calls: list[dict[str, Any]] = []
    cleaned = text
    for pattern in _TOOL_CALL_TEXT_PATTERNS:
        for m in list(pattern.finditer(cleaned)):
            fn_name = m.group(1).strip()
            raw_args = m.group(2).strip()
            parsed: Any = {}
            if raw_args:
                try:
                    parsed = json.loads(raw_args)
                except Exception:
                    try:
                        import ast

                        parsed = ast.literal_eval(raw_args)
                    except Exception:
                        parsed = {"_raw": raw_args}
            if not isinstance(parsed, dict):
                parsed = {"_value": parsed}
            tool_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": fn_name, "arguments": json.dumps(parsed, ensure_ascii=False)},
                }
            )
        cleaned = pattern.sub("", cleaned).strip()
    return tool_calls, cleaned


# Trần ký tự cho kết quả tool gửi lên upstream (mặc định 200k ≈ rất rộng); vượt thì cắt và đánh dấu rõ.
TOOL_RESULT_MAX_CHARS = int(os.getenv("GATEWAY_TOOL_RESULT_MAX_CHARS", "200000"))
TOOL_RESULT_TRUNCATED_MARKER = "…[đã cắt]"


def _truncate_tool_result(text: str) -> str:
    if TOOL_RESULT_MAX_CHARS > 0 and len(text) > TOOL_RESULT_MAX_CHARS:
        return text[:TOOL_RESULT_MAX_CHARS] + TOOL_RESULT_TRUNCATED_MARKER
    return text


def build_code_assist_request(openai_payload: dict[str, Any], project_id: str) -> dict[str, Any]:
    """OpenAI /v1/chat/completions payload → envelope Code Assist."""
    messages = openai_payload.get("messages") or []
    model_name = map_model_name(openai_payload.get("model") or DEFAULT_CODE_ASSIST_MODEL)
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    tool_call_names: dict[str, str] = {}  # tool_call_id → tên hàm, để functionResponse khớp functionCall

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user")
        if role == "system":
            text = _coerce_content_to_text(msg.get("content"))
            if text:
                system_parts.append(text)
            continue
        if role in {"tool", "function"}:
            # Kết quả tool → functionResponse thật (id khớp functionCall phía trên); cắt ở trần lớn có đánh dấu.
            tool_text = _truncate_tool_result(_coerce_content_to_text(msg.get("content")))
            call_id = str(msg.get("tool_call_id") or "")
            tool_name = msg.get("name") or tool_call_names.get(call_id) or call_id or "tool"
            function_response: dict[str, Any] = {"name": tool_name, "response": {"result": tool_text}}
            if call_id:
                function_response["id"] = call_id
            contents.append({"role": "user", "parts": [{"functionResponse": function_response}]})
            continue

        gemini_role = "model" if role == "assistant" else "user"
        parts = _coerce_content_to_parts(msg.get("content"))
        tool_calls = msg.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                # Không có thoughtSignature thật (lịch sử cũ) → vẫn gửi functionCall thật với chữ ký
                # "skip_thought_signature_validator" (_translate_tool_call_to_gemini tự điền), giữ nguyên id.
                part = _translate_tool_call_to_gemini(tc)
                if tc.get("id"):
                    tool_call_names[str(tc["id"])] = part["functionCall"]["name"]
                parts.append(part)
        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    joined_system = "\n".join(p for p in system_parts if p).strip()
    if not contents:
        contents.append({"role": "user", "parts": [{"text": joined_system or "Hello"}]})

    # Gemini yêu cầu xen kẽ user/model: gộp các message cùng vai liên tiếp.
    merged: list[dict[str, Any]] = []
    for entry in contents:
        if merged and merged[-1]["role"] == entry["role"]:
            merged[-1]["parts"].extend(entry["parts"])
        else:
            merged.append(entry)
    contents = merged
    if contents and contents[0]["role"] == "model":
        contents.insert(0, {"role": "user", "parts": [{"text": "(conversation context)"}]})

    generation_config: dict[str, Any] = {}
    if openai_payload.get("temperature") is not None:
        generation_config["temperature"] = float(openai_payload["temperature"])
    if openai_payload.get("top_p") is not None:
        generation_config["topP"] = float(openai_payload["top_p"])
    if openai_payload.get("max_tokens") is not None:
        generation_config["maxOutputTokens"] = int(openai_payload["max_tokens"])
    elif openai_payload.get("max_completion_tokens") is not None:
        generation_config["maxOutputTokens"] = int(openai_payload["max_completion_tokens"])

    inner: dict[str, Any] = {"contents": contents}
    if generation_config:
        inner["generationConfig"] = generation_config
    if joined_system:
        inner["systemInstruction"] = {"role": "system", "parts": [{"text": joined_system}]}
    gemini_tools = _translate_tools_to_gemini(openai_payload.get("tools"))
    if gemini_tools:
        inner["tools"] = gemini_tools
    tool_config = _translate_tool_choice_to_gemini(openai_payload.get("tool_choice"))
    if tool_config:
        inner["toolConfig"] = tool_config

    return {
        "project": project_id or DEFAULT_PROJECT_ID,
        "model": model_name,
        "user_prompt_id": str(uuid.uuid4()),
        "request": inner,
    }


# ---------- Gemini → OpenAI ----------

_FINISH_MAP = {
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "BLOCKLIST": "content_filter",
}


def _map_finish_reason(gemini_finish: str | None) -> str:
    """MAX_TOKENS/SAFETY không được báo là `stop` sạch, kẻo agent dùng nhầm output bị cắt."""
    return _FINISH_MAP.get(gemini_finish or "STOP", "stop")


def _usage_from_gemini(inner: dict[str, Any]) -> dict[str, Any]:
    meta = inner.get("usageMetadata") or {}
    prompt = int(meta.get("promptTokenCount") or 0)
    completion = int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0)
    cached = int(meta.get("cachedContentTokenCount") or 0)
    usage: dict[str, Any] = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    if cached:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return usage


def _parts_to_openai(parts: list[Any], *, with_index: bool) -> tuple[str, str, list[dict[str, Any]]]:
    content_text = ""
    reasoning_text = ""
    tool_calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("thought") is True and isinstance(part.get("text"), str):
            reasoning_text += part["text"]
        elif isinstance(part.get("text"), str):
            content_text += part["text"]
        elif "functionCall" in part:
            fc = part["functionCall"]
            ts = part.get("thoughtSignature") or (fc.get("thoughtSignature") if isinstance(fc, dict) else None)
            item: dict[str, Any] = {
                "id": fc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": fc.get("name") or "", "arguments": json.dumps(fc.get("args") or {})},
            }
            if with_index:
                item = {"index": len(tool_calls), **item}
            if ts:
                item["thoughtSignature"] = ts
                item["extra_content"] = {"google": {"thought_signature": ts}}
            tool_calls.append(item)
    return content_text, reasoning_text, tool_calls


def translate_gemini_to_openai_response(gemini_resp: dict[str, Any], requested_model: str) -> dict[str, Any]:
    inner: dict[str, Any] = gemini_resp["response"] if isinstance(gemini_resp.get("response"), dict) else gemini_resp
    candidates = inner.get("candidates") or []
    content_text, reasoning_text = "", ""
    tool_calls: list[dict[str, Any]] = []
    gemini_finish: str | None = None
    if candidates and isinstance(candidates[0], dict):
        cand = candidates[0]
        gemini_finish = cand.get("finishReason")
        content_text, reasoning_text, tool_calls = _parts_to_openai(
            (cand.get("content") or {}).get("parts") or [], with_index=False
        )
    if not tool_calls and content_text and any(p.search(content_text) for p in _TOOL_CALL_TEXT_PATTERNS):
        extracted, cleaned = _extract_tool_calls_from_text(content_text)
        if extracted:
            tool_calls, content_text = extracted, cleaned

    message: dict[str, Any] = {"role": "assistant", "content": content_text if content_text or not tool_calls else None}
    if reasoning_text:
        message["reasoning_content"] = reasoning_text
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else _map_finish_reason(gemini_finish)}
        ],
        "usage": _usage_from_gemini(inner),
    }


def translate_gemini_stream_event(
    event_data: dict[str, Any], requested_model: str, stream_id: str
) -> dict[str, Any] | None:
    inner: dict[str, Any] = event_data["response"] if isinstance(event_data.get("response"), dict) else event_data
    candidates = inner.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return None
    cand = candidates[0]
    content_piece, reasoning_piece, tool_calls = _parts_to_openai(
        (cand.get("content") or {}).get("parts") or [], with_index=True
    )
    delta: dict[str, Any] = {}
    if content_piece:
        delta["content"] = content_piece
    if reasoning_piece:
        delta["reasoning_content"] = reasoning_piece
    if tool_calls:
        delta["tool_calls"] = tool_calls
    finish = cand.get("finishReason")
    has_usage = bool(inner.get("usageMetadata"))
    if not delta and not finish and not has_usage:
        return None
    # Chunk chỉ có finishReason (MAX_TOKENS/SAFETY...) và/hoặc usageMetadata: vẫn phát với delta rỗng,
    # kẻo client thấy finish_reason "stop" sạch và mất usage.
    finish_reason = "tool_calls" if tool_calls else (_map_finish_reason(finish) if finish else None)
    chunk: dict[str, Any] = {
        "id": stream_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if inner.get("usageMetadata"):
        chunk["usage"] = _usage_from_gemini(inner)
    return chunk


_UPSTREAM_ERROR_MAX_CHARS = 500


def _upstream_error_message(status_code: int, body: str | bytes, *, stream: bool = False) -> str:
    """Thông điệp lỗi trả cho client: chỉ `error.message` của upstream (hoặc body thô nếu không phải JSON),
    cắt tối đa 500 ký tự — không dội nguyên body Google (có thể chứa chi tiết nội bộ, rất dài) ra ngoài."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    message = text.strip()
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            message = err["message"]
        elif isinstance(err, str):
            message = err
    message = " ".join(message.split())
    if len(message) > _UPSTREAM_ERROR_MAX_CHARS:
        message = message[: _UPSTREAM_ERROR_MAX_CHARS - 1] + "…"
    kind = "Code Assist stream lỗi" if stream else "Code Assist lỗi"
    return f"{kind} HTTP {status_code}: {message}"


# ---------- client ----------


class AntigravityClient:
    """Gọi Google Code Assist với pool tài khoản xoay vòng."""

    def __init__(self, auth_manager: AntigravityAuthManager | None = None, timeout: float = 120.0) -> None:
        self.auth_manager = auth_manager or AntigravityAuthManager()
        self._http = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._http.aclose()

    async def _candidates(self, bearer_token: str):
        # resolve có thể gọi mạng đồng bộ (refresh ~20s/tài khoản): chạy trong thread, không chặn event loop.
        return await asyncio.to_thread(self.auth_manager.resolve_credential_candidates, bearer_token=bearer_token)

    async def create_chat_completion(self, openai_payload: dict[str, Any], bearer_token: str = "") -> dict[str, Any]:
        requested_model = openai_payload.get("model") or DEFAULT_CODE_ASSIST_MODEL
        map_model_name(requested_model)  # model lạ → 400 ngay, trước khi chạm tài khoản/mạng
        candidates = await self._candidates(bearer_token)
        url = f"{CODE_ASSIST_BASE_URL}:generateContent"
        last_response: httpx.Response | None = None

        for creds in candidates:
            envelope = build_code_assist_request(openai_payload, creds.project_id)
            headers = build_antigravity_headers(creds.access_token, creds.project_id)
            resp = await self._http.post(url, json=envelope, headers=headers)
            last_response = resp
            if resp.status_code == 200:
                return translate_gemini_to_openai_response(resp.json(), requested_model)

            account_level_failure = resp.status_code < 500 and _should_fail_over(resp)
            if account_level_failure:
                # Thử model anh em (quota riêng) trên CÙNG tài khoản trước khi xoay.
                sibling = IN_ACCOUNT_MODEL_FALLBACK.get(str(envelope.get("model") or ""))
                if sibling:
                    sibling_resp = await self._http.post(url, json=dict(envelope, model=sibling), headers=headers)
                    last_response = sibling_resp
                    if sibling_resp.status_code == 200:
                        return translate_gemini_to_openai_response(sibling_resp.json(), requested_model)
                    if _should_fail_over(sibling_resp):
                        self.auth_manager.mark_account_unavailable(
                            creds, sibling_resp.status_code, sibling_resp.headers.get("Retry-After")
                        )
                        continue
                    raise UpstreamError(
                        _upstream_error_message(sibling_resp.status_code, sibling_resp.text), sibling_resp.status_code
                    )
                self.auth_manager.mark_account_unavailable(creds, resp.status_code, resp.headers.get("Retry-After"))
                continue

            if resp.status_code < 500:
                # Lỗi 4xx không liên quan tài khoản (payload hỏng...): tài khoản khác không cứu được.
                raise UpstreamError(_upstream_error_message(resp.status_code, resp.text), resp.status_code)

            logger.warning("Endpoint chính trả %s, thử endpoint dự phòng", resp.status_code)
            resp = await self._http.post(f"{FALLBACK_CODE_ASSIST_BASE_URL}:generateContent", json=envelope, headers=headers)
            last_response = resp
            if resp.status_code == 200:
                return translate_gemini_to_openai_response(resp.json(), requested_model)
            if _should_fail_over(resp):
                self.auth_manager.mark_account_unavailable(creds, resp.status_code, resp.headers.get("Retry-After"))
                continue
            raise UpstreamError(_upstream_error_message(resp.status_code, resp.text), resp.status_code)

        if last_response is None:
            raise UpstreamError("Không có tài khoản Antigravity nào sẵn sàng.", 429)
        raise UpstreamError(
            _upstream_error_message(last_response.status_code, last_response.text), last_response.status_code
        )

    async def stream_chat_completion(self, openai_payload: dict[str, Any], bearer_token: str = "") -> AsyncGenerator[str, None]:
        """Stream SSE; chỉ xoay tài khoản TRƯỚC khi phát chunk đầu tiên."""
        requested_model = openai_payload.get("model") or DEFAULT_CODE_ASSIST_MODEL
        map_model_name(requested_model)  # model lạ → 400 ngay, trước khi chạm tài khoản/mạng
        candidates = await self._candidates(bearer_token)
        url = f"{CODE_ASSIST_BASE_URL}:streamGenerateContent?alt=sse"
        last_error = "Không có tài khoản Antigravity nào sẵn sàng."
        last_status = 500
        stream_options = openai_payload.get("stream_options") or {}
        include_usage = isinstance(stream_options, dict) and bool(stream_options.get("include_usage"))

        for creds in candidates:
            envelope = build_code_assist_request(openai_payload, creds.project_id)
            headers = build_antigravity_headers(creds.access_token, creds.project_id)
            headers["Accept"] = "text/event-stream"
            stream_id = f"chatcmpl-{uuid.uuid4().hex}"

            async with self._http.stream("POST", url, json=envelope, headers=headers) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    last_error = _upstream_error_message(response.status_code, body, stream=True)
                    last_status = response.status_code
                    if response.status_code >= 500:
                        # Lỗi phía Google, không phải lỗi tài khoản: thử tài khoản kế, không cooldown.
                        logger.warning("Stream trả %s cho %s, xoay tài khoản", response.status_code, creds.email)
                        continue
                    if _should_fail_over(response):
                        self.auth_manager.mark_account_unavailable(
                            creds, response.status_code, response.headers.get("Retry-After")
                        )
                        continue
                    raise UpstreamError(last_error, response.status_code)

                yield "data: " + json.dumps(
                    {
                        "id": stream_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": requested_model,
                        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                    }
                ) + "\n\n"

                buffer = ""
                sent_finish: str | None = None
                last_usage: dict[str, Any] | None = None
                async for raw in response.aiter_text():
                    buffer += raw.replace("\r\n", "\n").replace("\r", "\n")
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        for line in block.split("\n"):
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            raw_json = line[5:].strip()
                            if not raw_json or raw_json == "[DONE]":
                                continue
                            try:
                                chunk = translate_gemini_stream_event(json.loads(raw_json), requested_model, stream_id)
                            except Exception as exc:
                                logger.debug("Bỏ qua SSE event không parse được: %s", exc)
                                continue
                            if chunk:
                                fr = chunk["choices"][0].get("finish_reason")
                                if fr:
                                    sent_finish = fr
                                if chunk.get("usage"):
                                    last_usage = chunk["usage"]
                                yield f"data: {json.dumps(chunk)}\n\n"

                # Chỉ phát chunk đóng tổng hợp khi upstream CHƯA gửi finish_reason thật; nếu không sẽ
                # đè lên tool_calls/length khiến client tưởng hội thoại kết thúc bình thường.
                if sent_finish is None:
                    yield "data: " + json.dumps(
                        {
                            "id": stream_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": requested_model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        }
                    ) + "\n\n"
                # `stream_options.include_usage` (OpenAI): chunk cuối chỉ có usage, `choices: []`.
                if include_usage and last_usage is not None:
                    yield "data: " + json.dumps(
                        {
                            "id": stream_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": requested_model,
                            "choices": [],
                            "usage": last_usage,
                        }
                    ) + "\n\n"
                yield "data: [DONE]\n\n"
                return

        raise UpstreamError(last_error, last_status)
