"""Lớp gọi model TEXT, trung lập provider (ADR-0003). Runner chỉ biết interface `ModelClient`; provider và model cấu
hình bằng biến môi trường hoặc file `llm.yaml`, không hard-code vào code hay prompt của phòng ban.

Cấu hình (ưu tiên: biến môi trường > llm.yaml > mặc định):
    STUDIO_LLM_PROVIDER    anthropic | openai | claude-code | fake
                           (openai = mọi server OpenAI-compatible: OpenAI, OpenRouter, Gemini OpenAI-compat, Ollama,
                            Groq, vLLM, LM Studio, Kimi, GLM...; claude-code = CLI `claude -p` đã đăng nhập trên máy)
    STUDIO_MODEL_STRONG    model cho tier `strong`
    STUDIO_MODEL_STANDARD  model cho tier `standard`
    STUDIO_MODEL_LIGHT     model cho tier `light` (rẻ/nhanh; thiếu thì dùng standard)
    STUDIO_LLM_BASE_URL    base URL cho provider openai
    STUDIO_LLM_API_KEY     key cho provider openai (Anthropic dùng ANTHROPIC_API_KEY)
    STUDIO_LLM_BACKENDS    lọc/sắp thứ tự backend của `backends:` trong llm.yaml (vd. "claude-sub,antigravity")
    STUDIO_SEARCH_URL      endpoint tìm kiếm cho tool web_search (tools.py, ADR-0007); không đặt thì tool báo chưa cấu hình

ADR-0006 — nhiều tài khoản subscription thay vì API: `backends:` trong llm.yaml khai báo từng gói (Claude Max qua
claude-code, Antigravity qua gateway, model local...) với model theo tier; `routing.py` gộp thành một client, chọn backend
theo tier và tự chuyển khi một gói hết quota. Không có `backends:` thì `provider`/`models` là một backend duy nhất.

Token trả về là số thật từ `usage` của provider, để runner ghi vào `audit-log.tokens` và supervisor cộng dồn.
Mọi hành động có tác dụng phụ (TTS, ảnh, ghép video, đăng) là code xác định trong `media.py` / `renderer.py`; tool-use
chỉ mở cho tool CHỈ ĐỌC (web, ADR-0007) qua `tools`/`messages` trung lập provider — provider `claude-code` uỷ quyền
vòng tool cho CLI (`--tools WebFetch,WebSearch`), các provider khác chạy vòng lặp trong runner.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from .tools import ToolCall, ToolSpec

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "llm.yaml"
TIERS = ("strong", "standard", "light")   # light: việc cơ học/ngắn (publisher, supervisor...) — model rẻ nhất


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
    tool_calls: list[ToolCall] = field(default_factory=list)  # model muốn gọi tool (rỗng = trả lời cuối)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cache_hit_ratio(self) -> float:
        return self.cached_input_tokens / self.input_tokens if self.input_tokens else 0.0

    def json(self) -> dict[str, Any]:
        """JSON object trong câu trả lời: chấp nhận JSON trần, JSON trong code fence, hoặc văn xuôi + fence/object
        (model có tool hay CLI hay kể lại quá trình trước khi trả JSON). Không có object nào → LLMError."""
        text = self.text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)  # fence đầu tiên
        if m:
            try: return json.loads(m.group(1))
            except json.JSONDecodeError: pass
        start = text.find("{")
        if start >= 0:  # object ngoài cùng đầu tiên: từ `{` đầu tới `}` cuối, rồi lùi dần
            end = text.rfind("}")
            while end > start:
                try: return json.loads(text[start:end + 1])
                except json.JSONDecodeError: end = text.rfind("}", start, end)
        raise LLMError(f"đầu ra không phải JSON:\n{self.text[:500]}")


class ModelClient(Protocol):
    """Một lời gọi = system + user + JSON Schema đầu ra + tier. Provider nào cũng phải trả `Completion`.
    `cache_key` (agent id) giúp provider định tuyến request cùng system prompt vào cùng cache.

    Tool-use (ADR-0007): `tools` là bảng tool trung lập; `messages` là hội thoại nhiều lượt thay cho `user`:
    {"role": "user", "content"}, {"role": "assistant", "content", "tool_calls": [{id, name, args}]},
    {"role": "tool", "tool_call_id", "content"}. Model muốn gọi tool thì `Completion.tool_calls` khác rỗng.
    `user` vẫn luôn là message lượt đầu (khoá ghi/phát lại eval)."""
    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None) -> Completion: ...


def neutral_messages(user: str, messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return list(messages) if messages else [{"role": "user", "content": user}]


# ---------- cấu hình ----------

@dataclass
class LLMConfig:
    provider: str = "fake"
    models: dict[str, str] = field(default_factory=lambda: {"strong": "", "standard": "", "light": ""})
    base_url: str | None = None
    api_key: str | None = None
    max_tokens: int = 16_000
    effort: dict[str, str] = field(default_factory=lambda: {"strong": "high", "standard": "medium", "light": "low"})
    extra: dict[str, Any] = field(default_factory=dict)
    name: str = "default"            # tên backend (ADR-0006), hiện trong ghi chú audit khi xoay
    backends: list[dict[str, Any]] = field(default_factory=list)   # mỗi phần tử = một backend, cùng khoá như cấp trên
    routing: dict[str, Any] = field(default_factory=dict)          # cooldown_s, transient_cooldown_s, prefer{tier: backend}

    def model_for(self, tier: str) -> str:
        """light → standard → strong: backend không có model rẻ thì dùng model tầm trung, không bao giờ lùi lên tier cao
        hơn yêu cầu trừ khi đó là model duy nhất."""
        m = self.models.get(tier) or self.models.get("standard") or self.models.get("strong") or ""
        if not m:
            raise LLMError(f"chưa cấu hình model cho tier `{tier}` (STUDIO_MODEL_{tier.upper()} hoặc llm.yaml)")
        return m

    def tiers_configured(self) -> frozenset[str]:
        return frozenset(t for t in TIERS if self.models.get(t))

    def backend_config(self, data: dict[str, Any]) -> LLMConfig:
        """Cấu hình cho một phần tử `backends:`: thừa kế khoá dùng chung từ cấp trên, ghi đè provider / models /
        base_url / api_key / effort / extra / max_tokens theo phần tử."""
        cfg = LLMConfig(**{k: v for k, v in self.__dict__.items() if k not in {"backends", "routing"}})
        cfg.models = dict(self.models) if data.get("inherit_models") else {t: "" for t in TIERS}
        cfg.effort, cfg.extra = dict(self.effort), dict(self.extra)
        _apply_yaml(cfg, data)
        cfg.name = str(data.get("name") or cfg.provider)
        if data.get("api_key"): cfg.api_key = str(data["api_key"])
        if data.get("api_key_env"): cfg.api_key = os.environ.get(str(data["api_key_env"]), cfg.api_key)
        return cfg


def _apply_yaml(cfg: LLMConfig, data: dict[str, Any]) -> None:
    cfg.provider = data.get("provider", cfg.provider)
    cfg.models.update({k: str(v) for k, v in (data.get("models") or {}).items()})
    cfg.effort.update(data.get("effort") or {})
    cfg.base_url = data.get("base_url", cfg.base_url)
    cfg.max_tokens = int(data.get("max_tokens", cfg.max_tokens))
    if "extra" in data: cfg.extra = dict(data.get("extra") or {})


def load_config(path: Path | None = None) -> LLMConfig:
    cfg = LLMConfig()
    p = path or CONFIG_FILE
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        _apply_yaml(cfg, data)
        cfg.backends = [dict(b) for b in (data.get("backends") or []) if isinstance(b, dict)]
        cfg.routing = dict(data.get("routing") or {})
    env = os.environ
    cfg.provider = env.get("STUDIO_LLM_PROVIDER", cfg.provider)
    for t in TIERS:
        if env.get(f"STUDIO_MODEL_{t.upper()}"): cfg.models[t] = env[f"STUDIO_MODEL_{t.upper()}"]
    cfg.base_url = env.get("STUDIO_LLM_BASE_URL", cfg.base_url)
    cfg.api_key = env.get("STUDIO_LLM_API_KEY", cfg.api_key)
    if env.get("STUDIO_LLM_BACKENDS"):
        wanted = [s.strip() for s in env["STUDIO_LLM_BACKENDS"].split(",") if s.strip()]
        by_name = {str(b.get("name") or b.get("provider")): b for b in cfg.backends}
        missing = [w for w in wanted if w not in by_name]
        if missing: raise LLMError(f"STUDIO_LLM_BACKENDS nhắc backend không có trong llm.yaml: {missing}")
        cfg.backends = [by_name[w] for w in wanted]
    return cfg


def _single_client(cfg: LLMConfig) -> ModelClient:
    if cfg.provider == "anthropic": return AnthropicClient(cfg)
    if cfg.provider == "openai": return OpenAICompatClient(cfg)
    if cfg.provider == "claude-code": return ClaudeCodeClient(cfg)
    if cfg.provider == "fake": return FakeClient()
    raise LLMError(f"provider lạ: {cfg.provider} (anthropic | openai | claude-code | fake)")


def make_client(cfg: LLMConfig | None = None) -> ModelClient:
    """Client theo cấu hình. Có `backends:` → `RoutingClient` gộp nhiều gói tài khoản (ADR-0006)."""
    cfg = cfg or load_config()
    if not cfg.backends:
        return _single_client(cfg)
    from .routing import Backend, RoutingClient
    bs = []
    for data in cfg.backends:
        bc = cfg.backend_config(data)
        bs.append(Backend(name=bc.name, client=_single_client(bc), tiers=bc.tiers_configured()))
    r = cfg.routing
    return RoutingClient(bs, cooldown_s=float(r.get("cooldown_s", 3600)),
                         transient_cooldown_s=float(r.get("transient_cooldown_s", 60)),
                         prefer={str(k): str(v) for k, v in (r.get("prefer") or {}).items()})


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

    @staticmethod
    def _messages(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Định dạng trung lập → content block của Anthropic (tool_use / tool_result)."""
        out: list[dict[str, Any]] = []
        for m in msgs:
            if m["role"] == "assistant":
                blocks: list[dict[str, Any]] = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                blocks += [{"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["args"]} for t in m.get("tool_calls", [])]
                out.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None) -> Completion:
        kwargs: dict[str, Any] = dict(
            model=self.cfg.model_for(model_tier), max_tokens=self.cfg.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=self._messages(neutral_messages(user, messages)),
            thinking={"type": "adaptive"},
            output_config={"effort": self.cfg.effort.get(model_tier, "medium"),
                           "format": {"type": "json_schema", "schema": strict_schema(schema)}},
            **self.cfg.extra,
        )
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
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
        calls = [ToolCall(id=b.id, name=b.name, args=dict(b.input or {})) for b in msg.content if b.type == "tool_use"]
        inp, read = anthropic_input_tokens(msg.usage)
        return Completion(text=text, input_tokens=inp, output_tokens=msg.usage.output_tokens, model=msg.model,
                          stop_reason=msg.stop_reason or "end_turn", cached_input_tokens=read, tool_calls=calls)


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

    @staticmethod
    def _messages(system: str, msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in msgs:
            if m["role"] == "assistant":
                a: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    a["tool_calls"] = [{"id": t["id"], "type": "function", "function": {
                        "name": t["name"], "arguments": json.dumps(t["args"], ensure_ascii=False)}} for t in m["tool_calls"]]
                out.append(a)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None) -> Completion:
        model = self.cfg.model_for(model_tier)
        msgs = self._messages(system, neutral_messages(user, messages))
        base: dict[str, Any] = {"model": model, "max_tokens": self.cfg.max_tokens, **self.cfg.extra, "messages": msgs}
        if tools:
            base["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.parameters}} for t in tools]
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
            fb = [*msgs]; i = max(k for k, m in enumerate(fb) if m["role"] == "user")
            fb[i] = {**fb[i], "content": fb[i]["content"] + hint}
            # json_object ép mọi lượt là JSON, kể cả lượt model muốn gọi tool → có tool thì không ép; runner chốt JSON sau
            data = self._post_cacheable({**base, "messages": fb, **({} if tools else {"response_format": {"type": "json_object"}})})
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or "stop"
        if finish == "content_filter":
            raise Refused("model từ chối (content_filter)")
        calls: list[ToolCall] = []
        for tc in (choice.get("message") or {}).get("tool_calls") or []:
            fn = tc.get("function") or {}
            try: args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError: args = {"_raw": fn.get("arguments")}
            calls.append(ToolCall(id=tc.get("id") or f"call_{len(calls)}", name=fn.get("name", ""), args=args))
        usage = data.get("usage") or {}
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        return Completion(text=(choice.get("message") or {}).get("content") or "",
                          input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0)),
                          model=data.get("model", model), stop_reason=finish, cached_input_tokens=cached, tool_calls=calls)


# ---------- provider: Claude Code CLI (dùng đăng nhập sẵn có của máy, không cần API key) ----------

CLI_WEB_TOOLS = "WebFetch,WebSearch"  # tool sẵn có của CLI, bản đồ 1-1 của web_fetch/web_search (ADR-0007)
CLI_TOOL_TURNS = 8


class ClaudeCodeClient:
    """Gọi `claude -p --output-format json` như một model backend: mỗi lượt là một tiến trình con, system prompt
    truyền qua `--system-prompt`, schema nhúng vào user message (CLI không có structured output).
    Không `tools` → `--tools ""` một lượt. Có `tools` (web) → uỷ quyền vòng tool cho CLI: `--tools WebFetch,WebSearch`
    nhiều lượt, CLI tự tìm/đọc rồi trả kết quả cuối; `Completion.tool_calls` luôn rỗng nên runner không lặp thêm.
    Token thật lấy từ `usage` trong JSON trả về (input + cache read + cache creation, cùng nghĩa với adapter Anthropic).
    Dùng khi máy đã đăng nhập Claude Code mà không có ANTHROPIC_API_KEY (vd. ghi bản ghi eval tại chỗ)."""

    def __init__(self, cfg: LLMConfig | None = None, binary: str = "claude", timeout: float = 900.0,
                 runner: Callable[[list[str]], str] | None = None):
        import shutil
        self.cfg = cfg or load_config()
        self.binary = shutil.which(binary) or binary
        self.timeout = timeout
        self._run = runner or self._subprocess  # test thay bằng hàm giả
        self.delegated_tools = False  # lần gọi gần nhất có uỷ quyền vòng tool cho CLI không (runner ghi audit)

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
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None) -> Completion:
        model = self.cfg.model_for(model_tier)
        hint = "\n\n# JSON Schema bắt buộc cho câu trả lời\n```json\n" + json.dumps(schema, ensure_ascii=False) + "\n```"
        if messages:  # CLI không nhận hội thoại nhiều lượt: chỉ dùng message lượt đầu (vòng tool là của CLI)
            user = next((m["content"] for m in messages if m["role"] == "user"), user)
        self.delegated_tools = bool(tools)
        tool_args = (["--tools", CLI_WEB_TOOLS, "--allowedTools", CLI_WEB_TOOLS, "--max-turns", str(CLI_TOOL_TURNS)]
                     if tools else ["--tools", "", "--max-turns", "1"])
        args = [self.binary, "-p", "--output-format", "json", "--model", model, *tool_args, "--system-prompt", system, user + hint]
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
        # modelUsage có thể gồm cả model phụ CLI dùng cho tool (WebFetch tóm tắt bằng haiku): lấy model đã yêu cầu,
        # không có thì model sinh nhiều output nhất.
        mu = data.get("modelUsage") or {}
        used = next((k for k in mu if k.startswith(model) or str(mu[k].get("canonicalModel", "")).startswith(model)),
                    max(mu, key=lambda k: int(mu[k].get("outputTokens", 0) or 0), default=model))
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
    # tool_handler(messages, tools) → danh sách ToolCall; rỗng = model trả lời cuối (qua handler/responses như thường)
    tool_handler: Callable[[list[dict[str, Any]], list[ToolSpec]], list[ToolCall]] | None = None

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None) -> Completion:
        msgs = neutral_messages(user, messages)
        self.calls.append({"system": system, "user": user, "schema": schema, "model_tier": model_tier, "cache_key": cache_key,
                           "tools": [t.name for t in tools or []], "messages": msgs})
        if tools and self.tool_handler:
            wanted = self.tool_handler(msgs, tools)
            if wanted:
                return Completion(text="", input_tokens=self.tokens_per_call[0], output_tokens=self.tokens_per_call[1],
                                  model=f"fake-{model_tier}", stop_reason="tool_use", tool_calls=list(wanted))
        if self.handler:
            payload = self.handler(system, user)
        elif self.responses:
            payload = self.responses.pop(0)
        else:
            raise LLMError("FakeClient hết câu trả lời")
        return Completion(text=json.dumps(payload, ensure_ascii=False), input_tokens=self.tokens_per_call[0],
                          output_tokens=self.tokens_per_call[1], model=f"fake-{model_tier}")
