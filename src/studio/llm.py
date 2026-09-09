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
from xagents_core.llm import AnthropicClient as AnthropicClient

# K3.3a: nền chung ở `xagents_core.llm`. Re-export TỪNG tên vì các module khác của studio nhập chúng từ
# `studio.llm`. `CLI_ARGV_MAX` là tên cũ của studio cho cùng hằng số mà company gọi là `ARGV_LIMIT` — giữ cả hai
# để không phải sửa nơi gọi trong PR chuyển mã.
from xagents_core.llm import ClaudeCodeClient as CoreClaudeCodeClient
from xagents_core.llm import CodexClient as CodexClient
from xagents_core.llm import Completion as Completion
from xagents_core.llm import FakeClient as FakeClient
from xagents_core.llm import LLMConfig as CoreLLMConfig
from xagents_core.llm import LLMError as LLMError
from xagents_core.llm import ModelClient as ModelClient
from xagents_core.llm import OpenAICompatClient as OpenAICompatClient
from xagents_core.llm import Refused as Refused
from xagents_core.llm import TransientError as TransientError
from xagents_core.llm import anthropic_input_tokens as anthropic_input_tokens
from xagents_core.llm import check_argv as check_argv
from xagents_core.llm import cli_effort_args as cli_effort_args
from xagents_core.llm import cli_env as cli_env
from xagents_core.llm import cli_exit_error as cli_exit_error
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
from .tools import ToolSpec

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

# ---------- provider: Claude Code CLI (dùng đăng nhập sẵn có của máy, không cần API key) ----------

CLI_WEB_TOOLS = "WebFetch,WebSearch"  # tool sẵn có của CLI, bản đồ 1-1 của web_fetch/web_search (ADR-0007)
CLI_TOOL_TURNS = 8
# Lượt cho đường KHÔNG tool. Không phải 1: `--json-schema` (ADR-0026) ép JSON bằng một lượt nội bộ nữa của
# CLI, nên `--max-turns 1` cắt đúng lượt ép đó → `error_max_turns`, không có `result`. Company đã đo và vá
# 2026-09-05 (`company.llm.CLI_NO_TOOL_TURNS`, TRAPS.md của company); studio giữ nguyên `1` tới khi đo lại
# 2026-09-09 trên `seo-optimizer/kich-ban-phai-ra-metadata-dung-gioi-han`. 6 = 1 trả lời + ép + dư cho 2-3
# vòng model tự sửa JSON.
CLI_NO_TOOL_TURNS = 6
class ClaudeCodeClient(CoreClaudeCodeClient):
    """Gọi `claude -p --output-format json` như một model backend: mỗi lượt là một tiến trình con, system prompt
    truyền qua `--system-prompt-file` (ADR-0026: argv có trần ~32 KB trên Windows), schema đi cả `--json-schema`
    (CLI ép và kiểm, trả `structured_output`) lẫn phần nhúng trong user message; user message đưa qua STDIN,
    không qua argv (argv lộ trong `ps`, có trần độ dài và không được chứa nội dung không tin cậy).
    Không `tools` → `--tools ""` một lượt. Có `tools` (web) → uỷ quyền vòng tool cho CLI: `--tools WebFetch,WebSearch`
    nhiều lượt, CLI tự tìm/đọc rồi trả kết quả cuối; `Completion.tool_calls` luôn rỗng nên runner không lặp thêm.
    Token thật lấy từ `usage` trong JSON trả về (input + cache read + cache creation, cùng nghĩa với adapter Anthropic).
    Dùng khi máy đã đăng nhập Claude Code mà không có ANTHROPIC_API_KEY (vd. ghi bản ghi eval tại chỗ)."""

    def __init__(self, cfg: LLMConfig, binary: str = "claude", timeout: float = 900.0,
                 runner: Callable[..., str] | None = None):
        super().__init__(cfg, binary=binary, timeout=timeout, runner=runner)
        self.delegated_tools = False  # lần gọi gần nhất có uỷ quyền vòng tool cho CLI không (runner ghi audit)

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
                     if tools else ["--tools", "", "--max-turns", str(CLI_NO_TOOL_TURNS)])
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

# ---------- provider: Codex CLI (gói ChatGPT Plus/Pro đã `codex login` trên máy, không cần API key) ----------

# ---------- provider: giả (test / eval offline) ----------

