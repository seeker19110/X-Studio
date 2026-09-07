"""Lớp gọi model TEXT, trung lập provider (ADR-0003). Runner chỉ biết interface `ModelClient`; provider và model cấu
hình bằng biến môi trường hoặc file `llm.yaml`, không hard-code vào code hay prompt của phòng ban.

Cấu hình (ưu tiên: biến môi trường > llm.yaml > mặc định):
    STUDIO_LLM_PROVIDER    anthropic | openai | claude-code | codex | fake
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
from typing import Any, ClassVar, Protocol

# K3.3a: nền chung ở `xagents_core.llm`. Re-export TỪNG tên vì các module khác của studio nhập chúng từ
# `studio.llm`. `CLI_ARGV_MAX` là tên cũ của studio cho cùng hằng số mà company gọi là `ARGV_LIMIT` — giữ cả hai
# để không phải sửa nơi gọi trong PR chuyển mã.
from xagents_core.llm import ARGV_LIMIT as ARGV_LIMIT
from xagents_core.llm import CLAUDE_EFFORT as CLAUDE_EFFORT
from xagents_core.llm import CLI_BASE_FLAGS as CLI_BASE_FLAGS
from xagents_core.llm import CLI_SUBTYPE_ERRORS as CLI_SUBTYPE_ERRORS
from xagents_core.llm import CODEX_EFFORT as CODEX_EFFORT
from xagents_core.llm import TIERS as TIERS
from xagents_core.llm import TRANSIENT_HTTP as TRANSIENT_HTTP
from xagents_core.llm import LLMConfig as CoreLLMConfig
from xagents_core.llm import LLMError as LLMError
from xagents_core.llm import Refused as Refused
from xagents_core.llm import TransientError as TransientError
from xagents_core.llm import cli_effort_args as cli_effort_args
from xagents_core.llm import find_codex_binary as find_codex_binary
from xagents_core.llm import load_config as core_load_config
from xagents_core.llm import neutral_messages as neutral_messages
from xagents_core.llm import reported_model as reported_model
from xagents_core.llm import strict_schema as strict_schema
from xagents_core.llm import system_prompt_args as system_prompt_args

# `SECRET_ENV` là bản THỨ BA của cùng một regex (workspace của company và sandbox đã có); K3.2 ghi nhận, K3.3a xoá.
from xagents_core.sandbox import SECRET_ENV as SECRET_ENV

from .core import CORE
from .tools import ToolCall, ToolSpec

CLI_ARGV_MAX = ARGV_LIMIT


# Giữ tên cũ vì console (`collect.py`) và test đọc chúng từ module này; nguồn nay là `CORE`.
ROOT = CORE.root
CONFIG_FILE = CORE.config_file
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


# ---------- cấu hình ----------

@dataclass
class LLMConfig(CoreLLMConfig):
    """Cấu hình của studio = ĐÚNG khung chung, không thêm trường nào.

    K3.3b: cả 13 trường studio từng khai đều là khoá hai công ty dùng chung, nên chúng lên `xagents_core.llm`
    nguyên vẹn; ở lại đây chỉ còn hai điểm studio thật sự khác company — tiền tố biến môi trường và provider
    mặc định. Nếu một ngày lớp này lại mọc trường riêng, hãy hỏi trước: đó là nhu cầu của studio, hay là company
    đã có sẵn thứ đó và cái cần làm là kéo nó lên core.

    `provider` mặc định `fake` (company: `anthropic`): studio chạy được toàn bộ đường ống offline bằng provider
    giả, còn company mặc định gọi model thật.
    """
    PREFIX: ClassVar[str] = CORE.prefix

    provider: str = "fake"


def load_config(path: Path | None = None) -> LLMConfig:
    """`llm.yaml` của studio + biến `STUDIO_*`. Chữ ký giữ nguyên (`path` vị trí) vì nơi gọi đang dùng."""
    return core_load_config(CORE, path, cls=LLMConfig)


def _single_client(cfg: LLMConfig) -> ModelClient:
    if cfg.provider == "anthropic": return AnthropicClient(cfg)
    if cfg.provider == "openai": return OpenAICompatClient(cfg)
    if cfg.provider == "codex": return CodexClient(cfg)
    if cfg.provider == "claude-code": return ClaudeCodeClient(cfg)
    if cfg.provider == "fake": return FakeClient()
    raise LLMError(f"provider lạ: {cfg.provider} (anthropic | openai | claude-code | codex | fake)")


def make_client(cfg: LLMConfig | None = None) -> ModelClient:
    """Client theo cấu hình, gắn `max_input_chars` để runner đọc mà không cần biết cấu hình.
    Có `backends:` → `RoutingClient` gộp nhiều gói tài khoản (ADR-0006)."""
    cfg = cfg or load_config()
    client: Any
    if not cfg.backends:
        client = _single_client(cfg)
    else:
        from .routing import Backend, RoutingClient
        bs = []
        for data in cfg.backends:
            bc = cfg.backend_config(data)
            bs.append(Backend(name=bc.name, client=_single_client(bc), tiers=bc.tiers_configured(),
                              supports_tools=bool(data.get("supports_tools", bc.provider != "codex"))))
        r = cfg.routing
        client = RoutingClient(bs, cooldown_s=float(r.get("cooldown_s", 3600)),
                               transient_cooldown_s=float(r.get("transient_cooldown_s", 60)),
                               prefer={str(k): str(v) for k, v in (r.get("prefer") or {}).items()})
    client.max_input_chars = cfg.max_input_chars
    return client


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
def cli_env(keep_prefixes: tuple[str, ...] = ()) -> dict[str, str]:
    """Env cho tiến trình CLI model (claude/codex): bỏ mọi biến trông như khoá, trừ tiền tố CLI cần để đăng nhập.
    Trước đây adapter truyền nguyên `os.environ`, nghĩa là khoá TTS/ảnh/YouTube của phòng ban đi thẳng vào tiến
    trình con — thứ không lượt gọi model nào cần tới."""
    out = {}
    for k, v in os.environ.items():
        if k.upper().startswith(("COMPANY_LLM", "STUDIO_LLM")): continue
        if SECRET_ENV.search(k) and not k.upper().startswith(tuple(p.upper() for p in keep_prefixes)): continue
        out[k] = v
    return out


class ClaudeCodeClient:
    """Gọi `claude -p --output-format json` như một model backend: mỗi lượt là một tiến trình con, system prompt
    truyền qua `--system-prompt-file` (ADR-0026: argv có trần ~32 KB trên Windows), schema đi cả `--json-schema`
    (CLI ép và kiểm, trả `structured_output`) lẫn phần nhúng trong user message; user message đưa qua STDIN,
    không qua argv (argv lộ trong `ps`, có trần độ dài và không được chứa nội dung không tin cậy).
    Không `tools` → `--tools ""` một lượt. Có `tools` (web) → uỷ quyền vòng tool cho CLI: `--tools WebFetch,WebSearch`
    nhiều lượt, CLI tự tìm/đọc rồi trả kết quả cuối; `Completion.tool_calls` luôn rỗng nên runner không lặp thêm.
    Token thật lấy từ `usage` trong JSON trả về (input + cache read + cache creation, cùng nghĩa với adapter Anthropic).
    Dùng khi máy đã đăng nhập Claude Code mà không có ANTHROPIC_API_KEY (vd. ghi bản ghi eval tại chỗ)."""

    def __init__(self, cfg: LLMConfig | None = None, binary: str = "claude", timeout: float = 900.0,
                 runner: Callable[[list[str], str], str] | None = None):
        import shutil
        self.cfg = cfg or load_config()
        self.binary = shutil.which(self.cfg.binary or binary) or self.cfg.binary or binary
        self.timeout = timeout
        # Giữ ANTHROPIC_*/CLAUDE_* vì CLI cần chúng để đăng nhập / chọn endpoint; mọi khoá khác bị lọc (ADR-0026).
        self.env = cli_env(keep_prefixes=("ANTHROPIC_", "CLAUDE_"))
        if self.cfg.config_dir:   # nhiều tài khoản Claude trên một máy: mỗi backend một thư mục đăng nhập riêng
            self.env["CLAUDE_CONFIG_DIR"] = str(Path(self.cfg.config_dir).expanduser())
        self._run = runner or self._subprocess  # test thay bằng hàm giả: (args, stdin) → stdout
        self.delegated_tools = False  # lần gọi gần nhất có uỷ quyền vòng tool cho CLI không (runner ghi audit)

    def _subprocess(self, args: list[str], prompt: str) -> str:
        import subprocess
        try:
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               input=prompt, timeout=self.timeout, env=self.env)
        except FileNotFoundError as e:
            raise LLMError(f"không tìm thấy `{self.binary}` (cài Claude Code hoặc đổi provider)") from e
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude -p quá {self.timeout}s") from e
        except OSError as e:  # argv quá dài, không có quyền chạy, pipe vỡ…
            raise LLMError(f"không chạy được `{self.binary}`: {e}") from e
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
        # ADR-0026: schema đi CẢ HAI đường — `--json-schema` để CLI ép và kiểm, `hint` trong prompt để model thấy mô
        # tả từng trường. `--effort` theo tier, và không ghi transcript ra máy.
        base = [self.binary, "-p", "--output-format", "json", "--model", model, *CLI_BASE_FLAGS,
                "--json-schema", json.dumps(schema, ensure_ascii=False), *cli_effort_args(self.cfg.effort, model_tier)]
        with system_prompt_args(system) as sp_args:
            args = [*base, *tool_args, *sp_args]
            if sum(len(a) + 1 for a in args) > CLI_ARGV_MAX:
                raise LLMError(f"claude -p: argv vượt {CLI_ARGV_MAX} ký tự — rút gọn schema/prompt của agent")
            out = self._run(args, user + hint)
        return self._parse(out, model)

    def _parse(self, out: str, model: str) -> Completion:
        """JSON của `claude -p` → Completion."""
        try:
            data = json.loads(out[out.index("{"):]) if "{" in out else {}
        except json.JSONDecodeError as e:
            raise LLMError(f"claude -p trả về không phải JSON: {out[:300]}") from e
        # `data` luôn là dict: chuỗi được cắt từ dấu `{` đầu tiên nên json.loads chỉ ra object hoặc ném lỗi;
        # nhánh "không phải object JSON" trước đây là code chết, đã bỏ.
        subtype = str(data.get("subtype") or "")
        if subtype in CLI_SUBTYPE_ERRORS:   # đọc TRƯỚC `result`: các subtype này có thể không có result
            raise LLMError(f"claude -p {subtype}: {CLI_SUBTYPE_ERRORS[subtype]}; {str(data.get('result') or '')[:200]}")
        if "result" not in data:
            raise LLMError(f"claude -p thiếu trường result (subtype={subtype or '?'}): {out[:300]}")
        if data.get("is_error"):
            raise LLMError(f"claude -p lỗi: {str(data.get('result'))[:300]}")
        if data.get("stop_reason") == "refusal":
            raise Refused("model từ chối")
        u = data.get("usage") or {}
        read = int(u.get("cache_read_input_tokens", 0) or 0); write = int(u.get("cache_creation_input_tokens", 0) or 0)
        used = reported_model(data.get("modelUsage") or {}, model)
        # `--json-schema` → `structured_output` đã parse và đã qua kiểm của CLI: ưu tiên nó, `result` chỉ là bản chữ.
        so = data.get("structured_output")
        text = json.dumps(so, ensure_ascii=False) if isinstance(so, dict) else str(data["result"])
        return Completion(text=text, input_tokens=int(u.get("input_tokens", 0) or 0) + read + write,
                          output_tokens=int(u.get("output_tokens", 0) or 0), model=used,
                          stop_reason=str(data.get("stop_reason") or "end_turn"), cached_input_tokens=read)


# ---------- provider: Codex CLI (gói ChatGPT Plus/Pro đã `codex login` trên máy, không cần API key) ----------

class CodexClient:
    """Gọi `codex exec --json` như một model backend: mỗi lượt một tiến trình con, sandbox read-only trong
    thư mục rỗng (không tool của phòng ban; router bỏ qua backend này khi agent cần tool web), system prompt ghép vào đầu prompt vì
    CLI không có cờ system riêng. Schema nhúng vào prompt, không dùng `--output-schema` (strict mode của OpenAI bắt mọi thuộc
    tính phải `required`, không hợp schema topic có trường tuỳ chọn). Đầu ra JSONL: `item.completed` (agent_message) là câu trả lời, `turn.completed` mang
    `usage` (input đã gồm phần cache như OpenAI), `error` / `turn.failed` là lỗi (CLI vẫn thoát mã 0).
    Nhiều tài khoản ChatGPT trên một máy: `config_dir` → CODEX_HOME riêng (`CODEX_HOME=~/.codex-acc2 codex login`)."""

    def __init__(self, cfg: LLMConfig | None = None, binary: str | None = None, timeout: float = 900.0,
                 runner: Callable[[list[str]], str] | None = None):
        import shutil
        import tempfile
        self.cfg = cfg or load_config()
        explicit = binary or self.cfg.binary
        self.binary = (shutil.which(explicit) or explicit) if explicit else find_codex_binary()
        self.timeout = timeout
        self.workdir = Path(tempfile.mkdtemp(prefix="codex-empty-"))
        self.env = cli_env(keep_prefixes=("OPENAI_", "CODEX_"))   # ADR-0026: khoá của phòng ban không đi theo
        if self.cfg.config_dir:
            self.env["CODEX_HOME"] = str(Path(self.cfg.config_dir).expanduser())
        self._run = runner or self._subprocess

    def _subprocess(self, args: list[str]) -> str:
        import subprocess
        try:
            r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               stdin=subprocess.DEVNULL, timeout=self.timeout, env=self.env)
        except FileNotFoundError as e:
            raise LLMError(f"không tìm thấy `{self.binary}` (cài Codex CLI hoặc đặt `binary:` cho backend)") from e
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"codex exec quá {self.timeout}s") from e
        if r.returncode != 0:
            detail = (r.stdout[-600:] + "\n" + r.stderr[-300:]).strip()
            raise LLMError(f"codex exec thoát mã {r.returncode}: {detail}")
        return r.stdout

    def _args(self, model: str, effort: str, prompt: str) -> list[str]:
        return [self.binary, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                "-C", str(self.workdir), "--json", "-m", model,
                "-c", f"model_reasoning_effort={CODEX_EFFORT.get(effort, 'medium')}", prompt]

    def complete(self, *, system: str, user: str, schema: dict[str, Any], model_tier: str,
                 cache_key: str | None = None, tools: list[ToolSpec] | None = None,
                 messages: list[dict[str, Any]] | None = None) -> Completion:
        if tools:
            raise LLMError("codex không hỗ trợ tool-use của công ty; router tự bỏ qua backend này khi agent có tool")
        model = self.cfg.model_for(model_tier)
        msgs = neutral_messages(user, messages)
        body = msgs[0]["content"] if len(msgs) == 1 else "\n\n".join(f"[{m['role']}]\n{m.get('content') or ''}" for m in msgs)
        hint = "# JSON Schema bắt buộc cho câu trả lời\n```json\n" + json.dumps(schema, ensure_ascii=False) + "\n```"
        prompt = (f"# Vai trò và quy tắc\n{system}\n\n# Yêu cầu\n{body}\n\n{hint}\n\n"
                  "Trả lời DUY NHẤT một JSON đúng schema trên, không giải thích, không đọc hay chạy gì trong thư mục làm việc.")
        out = self._run(self._args(model, self.cfg.effort.get(model_tier, "medium"), prompt))
        texts: list[str] = []; usage: dict[str, Any] = {}; errors: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"): continue
            try: ev = json.loads(line)
            except json.JSONDecodeError: continue
            t = ev.get("type")
            if t == "item.completed":
                item = ev.get("item") or {}
                if item.get("type") == "agent_message": texts.append(str(item.get("text") or ""))
                elif item.get("type") == "error": errors.append(str(item.get("message") or ""))
            elif t == "turn.completed": usage = ev.get("usage") or {}
            elif t == "error": errors.append(str(ev.get("message") or ""))
            elif t == "turn.failed": errors.append(str((ev.get("error") or {}).get("message") or ""))
        fatal = [e for e in errors if "Defaulting to fallback metadata" not in e]   # cảnh báo metadata model không phải lỗi
        if fatal and not texts:
            msg = " | ".join(fatal)[:400]
            low = msg.lower()
            if any(s in low for s in ("429", "rate", "limit", "quota", "overloaded", "usage", "503", "502", "timeout")):
                raise LLMError(f"codex exec: {msg}")
            if "not logged in" in low or "login" in low:
                raise LLMError(f"codex exec: chưa đăng nhập (CODEX_HOME={self.env.get('CODEX_HOME', '~/.codex')}): {msg}")
            raise LLMError(f"codex exec lỗi: {msg}")
        if not texts:
            raise LLMError(f"codex exec không trả agent_message: {out[:300]}")
        inp = int(usage.get("input_tokens", 0) or 0); cached = int(usage.get("cached_input_tokens", 0) or 0)
        return Completion(text=texts[-1], input_tokens=inp, output_tokens=int(usage.get("output_tokens", 0) or 0),
                          model=model, cached_input_tokens=cached)


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
