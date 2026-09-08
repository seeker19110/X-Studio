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
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from xagents_core.llm import ARGV_LIMIT as ARGV_LIMIT
from xagents_core.llm import CLAUDE_EFFORT as CLAUDE_EFFORT
from xagents_core.llm import CLI_BASE_FLAGS as CLI_BASE_FLAGS
from xagents_core.llm import CLI_SUBTYPE_ERRORS as CLI_SUBTYPE_ERRORS
from xagents_core.llm import CODEX_EFFORT as CODEX_EFFORT
from xagents_core.llm import TIERS as TIERS
from xagents_core.llm import TRANSIENT_HTTP as TRANSIENT_HTTP

# K3.3a: nền chung ở `xagents_core.llm`. Re-export TỪNG tên vì các module khác của studio nhập chúng từ
# `studio.llm`. `CLI_ARGV_MAX` là tên cũ của studio cho cùng hằng số mà company gọi là `ARGV_LIMIT` — giữ cả hai
# để không phải sửa nơi gọi trong PR chuyển mã.
from xagents_core.llm import AnthropicClient as AnthropicClient
from xagents_core.llm import CodexClient as CodexClient
from xagents_core.llm import Completion as Completion
from xagents_core.llm import FakeClient as FakeClient
from xagents_core.llm import LLMConfig as CoreLLMConfig
from xagents_core.llm import LLMError as LLMError
from xagents_core.llm import ModelClient as ModelClient
from xagents_core.llm import Refused as Refused
from xagents_core.llm import TransientError as TransientError
from xagents_core.llm import anthropic_input_tokens as anthropic_input_tokens
from xagents_core.llm import check_argv as check_argv
from xagents_core.llm import cli_effort_args as cli_effort_args
from xagents_core.llm import cli_env as cli_env
from xagents_core.llm import find_codex_binary as find_codex_binary
from xagents_core.llm import load_config as core_load_config
from xagents_core.llm import neutral_messages as neutral_messages
from xagents_core.llm import object_before_trailing_junk as object_before_trailing_junk
from xagents_core.llm import object_in_prose as object_in_prose
from xagents_core.llm import reported_model as reported_model
from xagents_core.llm import strict_schema as strict_schema
from xagents_core.llm import strip_code_fence as strip_code_fence
from xagents_core.llm import system_prompt_args as system_prompt_args

# `SECRET_ENV` là bản THỨ BA của cùng một regex (workspace của company và sandbox đã có); K3.2 ghi nhận, K3.3a xoá.
from xagents_core.sandbox import SECRET_ENV as SECRET_ENV

from .core import CORE
from .tools import ToolCall, ToolSpec

CLI_ARGV_MAX = ARGV_LIMIT


# Giữ tên cũ vì console (`collect.py`) và test đọc chúng từ module này; nguồn nay là `CORE`.
ROOT = CORE.root
CONFIG_FILE = CORE.config_file
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
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
        # `workdir` là của company (chạy CLI trong worktree khách); studio không có worktree nên bỏ qua.
        # Có mặt vì `ModelClient` nay là MỘT giao diện chung ở core — đặc tả K3.3 đã ghi đúng điều này.
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
                 messages: list[dict[str, Any]] | None = None, workdir: str | None = None) -> Completion:
        # `workdir` là của company (chạy CLI trong worktree khách); studio không có worktree nên bỏ qua.
        # Có mặt vì `ModelClient` nay là MỘT giao diện chung ở core — đặc tả K3.3 đã ghi đúng điều này.
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

# ---------- provider: giả (test / eval offline) ----------

