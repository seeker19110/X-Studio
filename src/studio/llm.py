"""Lớp gọi model TEXT, trung lập provider (ADR-0003). Runner chỉ biết interface `ModelClient`; provider và model cấu
hình bằng biến môi trường hoặc file `llm.yaml`, không hard-code vào code hay prompt của phòng ban.

Cấu hình (ưu tiên: biến môi trường > llm.yaml > mặc định):
    STUDIO_LLM_PROVIDER    anthropic | openai | claude-code | fake
                           (openai = mọi server OpenAI-compatible: OpenAI, OpenRouter, Gemini OpenAI-compat, Ollama,
                            Groq, vLLM, LM Studio, Kimi, GLM...; claude-code = CLI `claude -p` đã đăng nhập trên máy)
    STUDIO_MODEL_STRONG    model cho tier `strong`
    STUDIO_MODEL_STANDARD  model cho tier `standard`
    STUDIO_LLM_BASE_URL    base URL cho provider openai
    STUDIO_LLM_API_KEY     key cho provider openai (Anthropic dùng ANTHROPIC_API_KEY)

Token trả về là số thật từ `usage` của provider, để runner ghi vào `audit-log.tokens` và supervisor cộng dồn.
Phòng ban video không cần tool-use trong vòng lặp model: mọi hành động có tác dụng phụ (TTS, ảnh, ghép video, đăng)
là code xác định trong `media.py` / `renderer.py`, model chỉ ra quyết định có cấu trúc.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "llm.yaml"
TIERS = ("strong", "standard")


class LLMError(Exception): ...
class Refused(LLMError):
    """Model từ chối trả lời. Không retry mù; để supervisor escalate."""


@dataclass
class Completion:
    """`input_tokens` LUÔN là tổng input đã tính tiền, kể cả phần cache (Anthropic tách cache ra khỏi `input_tokens`,
    OpenAI gộp vào `prompt_tokens`; mỗi adapter tự quy đổi)."""
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str = "end_turn"
    cached_input_tokens: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cache_hit_ratio(self) -> float:
        return self.cached_input_tokens / self.input_tokens if self.input_tokens else 0.0

    def json(self) -> dict[str, Any]:
        text = self.text.strip()
        if text.startswith("```"):  # model nhỏ hay bọc JSON trong code fence
            text = text.strip("`").split("\n", 1)[1] if "\n" in text else text.strip("`")
            text = text.rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"đầu ra không phải JSON: {e}\n{self.text[:500]}") from e


class ModelClient(Protocol):
    """Một lời gọi = system + user + JSON Schema đầu ra + tier. Provider nào cũng phải trả `Completion`.
    `cache_key` (agent id) giúp provider định tuyến request cùng system prompt vào cùng cache."""
    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None) -> Completion: ...


# ---------- cấu hình ----------

@dataclass
class LLMConfig:
    provider: str = "fake"
    models: dict[str, str] = field(default_factory=lambda: {"strong": "", "standard": ""})
    base_url: str | None = None
    api_key: str | None = None
    max_tokens: int = 16_000
    effort: dict[str, str] = field(default_factory=lambda: {"strong": "high", "standard": "medium"})
    extra: dict[str, Any] = field(default_factory=dict)

    def model_for(self, tier: str) -> str:
        m = self.models.get(tier) or self.models.get("standard") or ""
        if not m:
            raise LLMError(f"chưa cấu hình model cho tier `{tier}` (STUDIO_MODEL_{tier.upper()} hoặc llm.yaml)")
        return m


def load_config(path: Path | None = None) -> LLMConfig:
    cfg = LLMConfig()
    p = path or CONFIG_FILE
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        cfg.provider = data.get("provider", cfg.provider)
        cfg.models.update({k: str(v) for k, v in (data.get("models") or {}).items()})
        cfg.effort.update(data.get("effort") or {})
        cfg.base_url = data.get("base_url", cfg.base_url)
        cfg.max_tokens = int(data.get("max_tokens", cfg.max_tokens))
        cfg.extra = dict(data.get("extra") or {})
    env = os.environ
    cfg.provider = env.get("STUDIO_LLM_PROVIDER", cfg.provider)
    for t in TIERS:
        if env.get(f"STUDIO_MODEL_{t.upper()}"): cfg.models[t] = env[f"STUDIO_MODEL_{t.upper()}"]
    cfg.base_url = env.get("STUDIO_LLM_BASE_URL", cfg.base_url)
    cfg.api_key = env.get("STUDIO_LLM_API_KEY", cfg.api_key)
    return cfg


def make_client(cfg: LLMConfig | None = None) -> ModelClient:
    cfg = cfg or load_config()
    if cfg.provider == "anthropic": return AnthropicClient(cfg)
    if cfg.provider == "openai": return OpenAICompatClient(cfg)
    if cfg.provider == "claude-code": return ClaudeCodeClient(cfg)
    if cfg.provider == "fake": return FakeClient()
    raise LLMError(f"provider lạ: {cfg.provider} (anthropic | openai | claude-code | fake)")


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured output ở nhiều provider cần `additionalProperties: false` ở mọi object; bản sao, không đổi schema gốc."""
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k not in {"$schema", "$id", "format"}}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out.setdefault("properties", {})
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(schema)


# ---------- provider: Anthropic ----------

def anthropic_input_tokens(usage: Any) -> tuple[int, int]:
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return usage.input_tokens + read + write, read


class AnthropicClient:
    """Claude qua SDK chính thức: streaming, adaptive thinking, structured output theo JSON Schema."""

    def __init__(self, cfg: LLMConfig | None = None):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("cài SDK: uv sync --extra anthropic") from e
        self.cfg = cfg or load_config()
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None) -> Completion:
        kwargs: dict[str, Any] = dict(
            model=self.cfg.model_for(model_tier), max_tokens=self.cfg.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.cfg.effort.get(model_tier, "medium"),
                           "format": {"type": "json_schema", "schema": strict_schema(schema)}},
            **self.cfg.extra,
        )
        try:
            with self._client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"lỗi mạng: {e}") from e
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"API {e.status_code}: {e.message}") from e
        if msg.stop_reason == "refusal":
            raise Refused("model từ chối")
        text = next((b.text for b in msg.content if b.type == "text"), "")
        inp, read = anthropic_input_tokens(msg.usage)
        return Completion(text=text, input_tokens=inp, output_tokens=msg.usage.output_tokens, model=msg.model,
                          stop_reason=msg.stop_reason or "end_turn", cached_input_tokens=read)


# ---------- provider: OpenAI-compatible (không cần SDK) ----------

class OpenAICompatClient:
    """POST {base_url}/chat/completions. Dùng `response_format: json_schema` nếu server hỗ trợ; nếu server từ chối
    (400) thì lùi về `json_object` + schema nhúng trong prompt."""

    def __init__(self, cfg: LLMConfig | None = None, timeout: float = 600.0):
        self.cfg = cfg or load_config()
        self.base_url = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = self.cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
        self.timeout = timeout
        self._json_schema_ok: bool | None = None
        self._cache_key_ok: bool | None = None

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LLMError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"lỗi mạng: {e.reason}") from e

    def _post_cacheable(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            data = self._post(body)
        except LLMError as e:
            if "prompt_cache_key" not in body or not str(e).startswith("HTTP 400"):
                raise
            self._cache_key_ok = False
            data = self._post({k: v for k, v in body.items() if k != "prompt_cache_key"})
        else:
            if "prompt_cache_key" in body: self._cache_key_ok = True
        return data

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None) -> Completion:
        model = self.cfg.model_for(model_tier)
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        base: dict[str, Any] = {"model": model, "max_tokens": self.cfg.max_tokens, **self.cfg.extra, "messages": msgs}
        if cache_key and self._cache_key_ok is not False:
            base["prompt_cache_key"] = cache_key
        data: dict[str, Any] | None = None
        if self._json_schema_ok is not False:
            try:
                data = self._post_cacheable({**base, "response_format": {"type": "json_schema", "json_schema": {
                    "name": "payload", "strict": True, "schema": strict_schema(schema)}}})
                self._json_schema_ok = True
            except LLMError as e:
                if not str(e).startswith("HTTP 400"): raise
                self._json_schema_ok = False
        if data is None:
            hint = "\n\n# JSON Schema bắt buộc\n```json\n" + json.dumps(schema, ensure_ascii=False) + "\n```"
            fb = [msgs[0], {"role": "user", "content": user + hint}]
            data = self._post_cacheable({**base, "messages": fb, "response_format": {"type": "json_object"}})
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or "stop"
        if finish == "content_filter":
            raise Refused("model từ chối (content_filter)")
        usage = data.get("usage") or {}
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        return Completion(text=(choice.get("message") or {}).get("content") or "",
                          input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0)),
                          model=data.get("model", model), stop_reason=finish, cached_input_tokens=cached)


# ---------- provider: Claude Code CLI (dùng đăng nhập sẵn có của máy, không cần API key) ----------

class ClaudeCodeClient:
    """Gọi `claude -p --output-format json` như một model backend: mỗi lượt là một tiến trình con, không tool,
    system prompt truyền qua `--system-prompt`, schema nhúng vào user message (CLI không có structured output).
    Token thật lấy từ `usage` trong JSON trả về (input + cache read + cache creation, cùng nghĩa với adapter Anthropic).
    Dùng khi máy đã đăng nhập Claude Code mà không có ANTHROPIC_API_KEY (vd. ghi bản ghi eval tại chỗ)."""

    def __init__(self, cfg: LLMConfig | None = None, binary: str = "claude", timeout: float = 900.0,
                 runner: Callable[[list[str]], str] | None = None):
        import shutil
        self.cfg = cfg or load_config()
        self.binary = shutil.which(binary) or binary
        self.timeout = timeout
        self._run = runner or self._subprocess  # test thay bằng hàm giả

    def _subprocess(self, args: list[str]) -> str:
        import subprocess
        try:
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               stdin=subprocess.DEVNULL, timeout=self.timeout)
        except FileNotFoundError as e:
            raise LLMError(f"không tìm thấy `{self.binary}` (cài Claude Code hoặc đổi provider)") from e
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude -p quá {self.timeout}s") from e
        if r.returncode != 0:
            raise LLMError(f"claude -p thoát mã {r.returncode}: {(r.stderr or r.stdout)[-500:]}")
        return r.stdout

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None) -> Completion:
        model = self.cfg.model_for(model_tier)
        hint = "\n\n# JSON Schema bắt buộc cho câu trả lời\n```json\n" + json.dumps(schema, ensure_ascii=False) + "\n```"
        args = [self.binary, "-p", "--output-format", "json", "--model", model, "--tools", "", "--max-turns", "1",
                "--system-prompt", system, user + hint]
        out = self._run(args)
        try:
            data = json.loads(out[out.index("{"):]) if "{" in out else {}
        except json.JSONDecodeError as e:
            raise LLMError(f"claude -p trả về không phải JSON: {out[:300]}") from e
        if not isinstance(data, dict) or "result" not in data:
            raise LLMError(f"claude -p thiếu trường result: {out[:300]}")
        if data.get("is_error"):
            raise LLMError(f"claude -p lỗi: {str(data.get('result'))[:300]}")
        if data.get("stop_reason") == "refusal":
            raise Refused("model từ chối")
        u = data.get("usage") or {}
        read = int(u.get("cache_read_input_tokens", 0) or 0); write = int(u.get("cache_creation_input_tokens", 0) or 0)
        used = next(iter((data.get("modelUsage") or {}).keys()), model)
        return Completion(text=str(data["result"]), input_tokens=int(u.get("input_tokens", 0) or 0) + read + write,
                          output_tokens=int(u.get("output_tokens", 0) or 0), model=used,
                          stop_reason=str(data.get("stop_reason") or "end_turn"), cached_input_tokens=read)


# ---------- provider: giả (test / eval offline) ----------

@dataclass
class FakeClient:
    """`responses` là hàng đợi dict trả về theo thứ tự, hoặc `handler(system, user)` sinh payload."""
    responses: list[dict[str, Any]] = field(default_factory=list)
    handler: Callable[[str, str], dict[str, Any]] | None = None
    tokens_per_call: tuple[int, int] = (1_000, 300)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None) -> Completion:
        self.calls.append({"system": system, "user": user, "schema": schema, "model_tier": model_tier, "cache_key": cache_key})
        if self.handler:
            payload = self.handler(system, user)
        elif self.responses:
            payload = self.responses.pop(0)
        else:
            raise LLMError("FakeClient hết câu trả lời")
        return Completion(text=json.dumps(payload, ensure_ascii=False), input_tokens=self.tokens_per_call[0],
                          output_tokens=self.tokens_per_call[1], model=f"fake-{model_tier}")
