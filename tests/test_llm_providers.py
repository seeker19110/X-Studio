"""Provider Anthropic / OpenAI-compatible / Codex CLI, và các nhánh còn thiếu của llm.py (json() fallback,
FakeClient hết câu trả lời, find_codex_binary, lỗi subprocess)."""

from __future__ import annotations

import json
import shutil
import sys
import types
import urllib.error

import pytest

from studio import llm
from studio.llm import (
    Completion,
    CodexClient,
    FakeClient,
    LLMConfig,
    LLMError,
    Refused,
    find_codex_binary,
)
from studio.tools import ToolCall, ToolSpec


# ---------- Completion.json() — fallback khi model không trả JSON trần ----------

def test_completion_json_object_in_fence():
    c = Completion(text='trước đó model kể lể...\n```json\n{"a": 1}\n```\nsau đó', input_tokens=1, output_tokens=1, model="m")
    assert c.json() == {"a": 1}


def test_completion_json_fence_invalid_falls_through_to_object_scan():
    # fence tồn tại nhưng nội dung trong fence không phải JSON hợp lệ → bỏ qua fence, quét `{...}` ngoài fence.
    text = '```json\nkhong phai json\n```\nvậy thì đây {"a": 1}'
    c = Completion(text=text, input_tokens=1, output_tokens=1, model="m")
    assert c.json() == {"a": 1}


def test_completion_json_object_embedded_in_prose_backtrack():
    # object đầu tiên `{` tới `}` cuối cùng không parse được (có `}` lạc ở giữa văn xuôi) → lùi dần tìm `}` hợp lệ.
    text = 'kể chuyện {"a": 1} rồi thêm } linh tinh sau'
    c = Completion(text=text, input_tokens=1, output_tokens=1, model="m")
    assert c.json() == {"a": 1}


def test_completion_json_raises_when_nothing_parses():
    c = Completion(text="không có JSON nào ở đây cả", input_tokens=1, output_tokens=1, model="m")
    with pytest.raises(LLMError, match="không phải JSON"):
        c.json()


def test_completion_cache_hit_ratio_zero_when_no_input():
    assert Completion(text="", input_tokens=0, output_tokens=0, model="m").cache_hit_ratio == 0.0


def test_completion_json_fence_invalid_falls_back_to_brace_scan():
    # fence tồn tại nhưng bên trong không phải JSON hợp lệ → bỏ qua fence, quét `{` ... `}` như bình thường
    c = Completion(text='```json\nkhông phải json\n```\n{"a": 1}', input_tokens=1, output_tokens=1, model="m")
    assert c.json() == {"a": 1}


def test_strict_schema_non_dict_list_leaf_passthrough():
    assert llm.strict_schema({"type": "array", "items": ["a", 1, None]}) == {"type": "array", "items": ["a", 1, None]}


def test_load_config_env_provider_overrides_yaml_and_drops_backends(tmp_path, monkeypatch):
    p = tmp_path / "llm.yaml"
    p.write_text("provider: anthropic\nbackends:\n  - name: b1\n    provider: fake\n", encoding="utf-8")
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "openai")
    monkeypatch.delenv("STUDIO_LLM_BACKENDS", raising=False)
    cfg = llm.load_config(p)
    assert cfg.provider == "openai" and cfg.backends == []


# ---------- FakeClient hết câu trả lời ----------

def test_fake_client_raises_when_out_of_responses():
    with pytest.raises(LLMError, match="hết câu trả lời"):
        FakeClient().complete(system="s", user="u", schema={}, model_tier="standard")


# ---------- provider: Anthropic (SDK giả lập qua sys.modules) ----------

class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _Block:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeMessage:
    def __init__(self, content, usage, model="claude-x", stop_reason="end_turn"):
        self.content = content
        self.usage = usage
        self.model = model
        self.stop_reason = stop_reason


class _FakeStream:
    def __init__(self, final):
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._final


class _APIConnectionError(Exception):
    pass


class _APIStatusError(Exception):
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _install_fake_anthropic(monkeypatch, stream_factory=None, raise_error=None):
    mod = types.ModuleType("anthropic")

    calls: dict = {}

    class _Messages:
        def stream(self, **kwargs):
            calls["kwargs"] = kwargs
            if raise_error is not None:
                raise raise_error
            return stream_factory(**kwargs)

    class Anthropic:
        def __init__(self):
            self.messages = _Messages()

    mod.Anthropic = Anthropic
    mod.APIConnectionError = _APIConnectionError
    mod.APIStatusError = _APIStatusError
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod, calls


def _cfg_anthropic(**kw):
    return LLMConfig(provider="anthropic", models={"strong": "claude-opus-5", "standard": "claude-sonnet-5"}, **kw)


def test_anthropic_client_complete_happy_path_and_messages_and_tools(monkeypatch):
    usage = _FakeUsage(input_tokens=100, output_tokens=20, cache_read_input_tokens=15, cache_creation_input_tokens=5)
    final = _FakeMessage(content=[_Block("text", text="xin chào"), _Block("tool_use", id="t1", name="web_search", input={"q": "x"})], usage=usage)
    _install_fake_anthropic(monkeypatch, stream_factory=lambda **kw: _FakeStream(final))
    from studio.llm import AnthropicClient

    c = AnthropicClient(_cfg_anthropic())
    tools = [ToolSpec(name="web_search", description="d", parameters={"type": "object"})]
    messages = [
        {"role": "user", "content": "hỏi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "web_search", "args": {"q": "x"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "kết quả"},
        {"role": "user", "content": "tiếp tục"},
    ]
    out = c.complete(system="SYS", user="user gốc", schema={"type": "object", "properties": {"a": {"type": "string"}}},
                     model_tier="standard", tools=tools, messages=messages)
    assert out.text == "xin chào"
    assert out.input_tokens == 100 + 15 + 5 and out.cached_input_tokens == 15 and out.output_tokens == 20
    assert out.tool_calls == [ToolCall(id="t1", name="web_search", args={"q": "x"})]
    assert out.stop_reason == "end_turn"


def test_anthropic_messages_merges_consecutive_tool_results_into_same_user_block(monkeypatch):
    from studio.llm import AnthropicClient

    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "a", "args": {}}, {"id": "t2", "name": "b", "args": {}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "r1"},
        {"role": "tool", "tool_call_id": "t2", "content": "r2"},  # liên tiếp → gộp vào cùng block user trước đó
    ]
    out = AnthropicClient._messages(msgs)
    assert len(out) == 2  # assistant + một user chứa cả hai tool_result
    assert [b["tool_use_id"] for b in out[1]["content"]] == ["t1", "t2"]


def test_anthropic_client_init_raises_runtime_error_without_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import anthropic -> ModuleNotFoundError (subclass ImportError)
    from studio.llm import AnthropicClient

    with pytest.raises(RuntimeError, match="uv sync"):
        AnthropicClient(_cfg_anthropic())


def test_anthropic_client_refusal_raises_refused(monkeypatch):
    usage = _FakeUsage()
    final = _FakeMessage(content=[], usage=usage, stop_reason="refusal")
    _install_fake_anthropic(monkeypatch, stream_factory=lambda **kw: _FakeStream(final))
    from studio.llm import AnthropicClient

    c = AnthropicClient(_cfg_anthropic())
    with pytest.raises(Refused):
        c.complete(system="s", user="u", schema={}, model_tier="standard")


def test_anthropic_client_connection_and_status_errors(monkeypatch):
    from studio.llm import AnthropicClient

    conn_err = _APIConnectionError("mất mạng")
    _install_fake_anthropic(monkeypatch, raise_error=conn_err)
    c = AnthropicClient(_cfg_anthropic())
    with pytest.raises(LLMError, match="lỗi mạng"):
        c.complete(system="s", user="u", schema={}, model_tier="standard")

    status_err = _APIStatusError("quá tải", status_code=529)
    _install_fake_anthropic(monkeypatch, raise_error=status_err)
    c2 = AnthropicClient(_cfg_anthropic())
    with pytest.raises(LLMError, match="API 529"):
        c2.complete(system="s", user="u", schema={}, model_tier="standard")


def test_anthropic_messages_appends_consecutive_tool_results_to_same_user_block(monkeypatch):
    from studio.llm import AnthropicClient

    msgs = [
        {"role": "user", "content": "hoi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "name": "a", "args": {}}, {"id": "t2", "name": "b", "args": {}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "r1"},
        {"role": "tool", "tool_call_id": "t2", "content": "r2"},  # liên tiếp: gộp vào cùng block user vừa tạo ở trên
    ]
    out = AnthropicClient._messages(msgs)
    user_blocks = [m for m in out if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(user_blocks) == 1 and len(user_blocks[0]["content"]) == 2
    assert [b["tool_use_id"] for b in user_blocks[0]["content"]] == ["t1", "t2"]


def test_anthropic_input_tokens_helper():
    assert llm.anthropic_input_tokens(_FakeUsage(input_tokens=10, cache_read_input_tokens=2, cache_creation_input_tokens=3)) == (15, 2)
    assert llm.anthropic_input_tokens(_FakeUsage(input_tokens=10)) == (10, 0)


def test_strict_schema_forces_additional_properties_false_recursively():
    schema = {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "string", "format": "date"}}}},
             "required": ["a"], "$schema": "http://json-schema.org/draft-07/schema#"}
    out = llm.strict_schema(schema)
    assert out["additionalProperties"] is False
    assert out["properties"]["a"]["additionalProperties"] is False
    assert "format" not in out["properties"]["a"]["properties"]["b"]
    assert "$schema" not in out
    assert out["required"] == ["a"]  # nhánh list: walk() đi qua từng phần tử
    # bản gốc không đổi
    assert "additionalProperties" not in schema


def test_load_config_env_provider_overrides_backends(tmp_path, monkeypatch):
    p = tmp_path / "llm.yaml"
    p.write_text("backends:\n  - name: a\n    provider: claude-code\n", encoding="utf-8")
    monkeypatch.setenv("STUDIO_LLM_PROVIDER", "openai")
    monkeypatch.delenv("STUDIO_LLM_BACKENDS", raising=False)
    cfg = llm.load_config(p)
    assert cfg.provider == "openai" and cfg.backends == []


# ---------- provider: OpenAI-compatible ----------

class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _cfg_openai(**kw):
    return LLMConfig(provider="openai", models={"strong": "gpt-x", "standard": "gpt-y"}, base_url="https://x.test/v1", api_key="k", **kw)


def test_openai_post_success_and_http_error_and_url_error(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout=None: _FakeHTTPResponse({"ok": True}))
    assert c._post({"a": 1}) == {"ok": True}

    def raise_http(req, timeout=None):
        raise urllib.error.HTTPError("u", 400, "bad", {}, None)

    def _read(self):
        return b"chi tiet loi"

    monkeypatch.setattr(urllib.error.HTTPError, "read", _read, raising=False)
    monkeypatch.setattr(llm.urllib.request, "urlopen", raise_http)
    with pytest.raises(LLMError, match="HTTP 400"):
        c._post({"a": 1})

    def raise_url(req, timeout=None):
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr(llm.urllib.request, "urlopen", raise_url)
    with pytest.raises(LLMError, match="lỗi mạng"):
        c._post({"a": 1})


def test_openai_post_cacheable_retries_without_prompt_cache_key_on_400(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())
    seen = []

    def fake_post(body):
        seen.append(dict(body))
        if "prompt_cache_key" in body:
            raise LLMError("HTTP 400: server không hỗ trợ prompt_cache_key")
        return {"ok": True}

    monkeypatch.setattr(c, "_post", fake_post)
    out = c._post_cacheable({"a": 1, "prompt_cache_key": "ck"})
    assert out == {"ok": True} and c._cache_key_ok is False
    assert "prompt_cache_key" not in seen[-1]


def test_openai_post_cacheable_reraises_non_cache_key_400(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())
    monkeypatch.setattr(c, "_post", lambda body: (_ for _ in ()).throw(LLMError("HTTP 400: khac")))
    with pytest.raises(LLMError, match="khac"):
        c._post_cacheable({"a": 1})


def test_openai_post_cacheable_marks_cache_key_ok_true(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())
    monkeypatch.setattr(c, "_post", lambda body: {"ok": True})
    c._post_cacheable({"prompt_cache_key": "ck"})
    assert c._cache_key_ok is True


def test_openai_complete_json_schema_success_with_cache_key_and_tools(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())
    captured = {}

    def fake_post_cacheable(body):
        captured["body"] = body
        return {"model": "gpt-y", "choices": [{"finish_reason": "tool_calls", "message": {
            "content": None, "tool_calls": [{"id": "c1", "function": {"name": "web_search", "arguments": '{"q": "x"}'}}]}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 10}}}

    monkeypatch.setattr(c, "_post_cacheable", fake_post_cacheable)
    tools = [ToolSpec(name="web_search", description="d", parameters={"type": "object"})]
    out = c.complete(system="SYS", user="hoi", schema={"type": "object"}, model_tier="standard", cache_key="ck", tools=tools,
                     messages=[{"role": "user", "content": "hoi"},
                              {"role": "assistant", "content": "", "tool_calls": [{"id": "c0", "name": "x", "args": {}}]},
                              {"role": "tool", "tool_call_id": "c0", "content": "r"}])
    assert out.tool_calls == [ToolCall(id="c1", name="web_search", args={"q": "x"})]
    assert out.cached_input_tokens == 10 and out.input_tokens == 50 and out.output_tokens == 5
    assert captured["body"]["prompt_cache_key"] == "ck"
    assert captured["body"]["response_format"]["type"] == "json_schema"


def test_openai_complete_falls_back_to_json_object_when_schema_rejected(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())
    calls = []

    def fake_post_cacheable(body):
        calls.append(body)
        if body.get("response_format", {}).get("type") == "json_schema":
            raise LLMError("HTTP 400: schema khong duoc ho tro")
        return {"model": "gpt-y", "choices": [{"finish_reason": "stop", "message": {"content": "{\"a\": 1}"}}], "usage": {}}

    monkeypatch.setattr(c, "_post_cacheable", fake_post_cacheable)
    out = c.complete(system="SYS", user="hoi", schema={"type": "object"}, model_tier="standard")
    assert out.text == '{"a": 1}'
    assert c._json_schema_ok is False
    # lượt sau: không thử json_schema nữa
    calls.clear()
    monkeypatch.setattr(c, "_post_cacheable", lambda body: (calls.append(body), {"model": "gpt-y", "choices": [
        {"finish_reason": "stop", "message": {"content": "{}"}}], "usage": {}})[1])
    c.complete(system="SYS", user="hoi 2", schema={"type": "object"}, model_tier="standard")
    assert "response_format" not in calls[0] or calls[0]["response_format"]["type"] == "json_object"


def test_openai_complete_reraises_non_400_error(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())
    monkeypatch.setattr(c, "_post_cacheable", lambda body: (_ for _ in ()).throw(LLMError("HTTP 500: server error")))
    with pytest.raises(LLMError, match="HTTP 500"):
        c.complete(system="s", user="u", schema={}, model_tier="standard")


def test_openai_complete_content_filter_raises_refused(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())
    monkeypatch.setattr(c, "_post_cacheable", lambda body: {"choices": [{"finish_reason": "content_filter", "message": {}}], "usage": {}})
    with pytest.raises(Refused):
        c.complete(system="s", user="u", schema={}, model_tier="standard")


def test_openai_complete_tool_call_args_invalid_json_falls_back_to_raw(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())
    monkeypatch.setattr(c, "_post_cacheable", lambda body: {"choices": [{"finish_reason": "tool_calls", "message": {
        "tool_calls": [{"function": {"name": "x", "arguments": "not json"}}]}}], "usage": {}})
    out = c.complete(system="s", user="u", schema={}, model_tier="standard")
    assert out.tool_calls[0].args == {"_raw": "not json"}
    assert out.tool_calls[0].id == "call_0"


def test_openai_complete_with_tools_skips_json_object_fallback_response_format(monkeypatch):
    from studio.llm import OpenAICompatClient

    c = OpenAICompatClient(_cfg_openai())
    c._json_schema_ok = False  # đã biết server không hỗ trợ json_schema
    captured = {}

    def fake_post_cacheable(body):
        captured["body"] = body
        return {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(c, "_post_cacheable", fake_post_cacheable)
    tools = [ToolSpec(name="web_search", description="d", parameters={"type": "object"})]
    c.complete(system="s", user="u", schema={"type": "object"}, model_tier="standard", tools=tools)
    assert "response_format" not in captured["body"]


# ---------- CodexClient ----------

def _cfg_codex(**kw):
    return LLMConfig(provider="codex", models={"strong": "gpt-5.6", "standard": "gpt-5.6"}, **kw)


def test_find_codex_binary_falls_back_to_localappdata_glob(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda b: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = tmp_path / "OpenAI" / "Codex" / "bin" / "1.2.3"
    d.mkdir(parents=True)
    exe = d / "codex.exe"
    exe.write_bytes(b"x")
    assert find_codex_binary() == str(exe)


def test_find_codex_binary_returns_bare_name_when_nothing_found(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda b: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert find_codex_binary("codex") == "codex"


def test_codex_client_subprocess_errors(monkeypatch):
    import subprocess

    c = CodexClient(_cfg_codex(), binary="codex")

    def raise_fnf(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", raise_fnf)
    with pytest.raises(LLMError, match="không tìm thấy"):
        c._subprocess(["codex"])

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    with pytest.raises(LLMError, match="quá"):
        c._subprocess(["codex"])

    class R:
        returncode = 2
        stdout = "out" * 300
        stderr = "err"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    with pytest.raises(LLMError, match="thoát mã 2"):
        c._subprocess(["codex"])

    class OK:
        returncode = 0
        stdout = "codex stdout that"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: OK())
    assert c._subprocess(["codex"]) == "codex stdout that"


def test_codex_complete_skips_malformed_json_lines_and_metadata_warning(monkeypatch):
    c = CodexClient(_cfg_codex(), runner=lambda args: "\n".join([
        "not-json-but-starts-plain",
        '{malformed',
        json.dumps({"type": "error", "message": "Defaulting to fallback metadata do dieu gi do"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok": true}'}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2, "cached_input_tokens": 1}}),
    ]))
    out = c.complete(system="s", user="u", schema={"type": "object"}, model_tier="standard")
    assert out.json() == {"ok": True}
    assert out.input_tokens == 5 and out.cached_input_tokens == 1


def test_codex_complete_raises_llm_error_on_rate_limit_message():
    c = CodexClient(_cfg_codex(), runner=lambda args: json.dumps({"type": "error", "message": "429 rate limited"}))
    with pytest.raises(LLMError, match="429"):
        c.complete(system="s", user="u", schema={}, model_tier="standard")


def test_codex_complete_raises_llm_error_on_login_message():
    c = CodexClient(_cfg_codex(), runner=lambda args: json.dumps({"type": "error", "message": "not logged in, run codex login"}))
    with pytest.raises(LLMError, match="chưa đăng nhập"):
        c.complete(system="s", user="u", schema={}, model_tier="standard")


def test_codex_complete_raises_generic_llm_error_on_other_message():
    c = CodexClient(_cfg_codex(), runner=lambda args: json.dumps({"type": "error", "message": "loi la"}))
    with pytest.raises(LLMError, match="codex exec lỗi"):
        c.complete(system="s", user="u", schema={}, model_tier="standard")


def test_codex_complete_raises_when_no_agent_message_at_all():
    c = CodexClient(_cfg_codex(), runner=lambda args: '{"type": "turn.completed", "usage": {}}')
    with pytest.raises(LLMError, match="không trả agent_message"):
        c.complete(system="s", user="u", schema={}, model_tier="standard")


def test_codex_complete_raises_when_tools_passed():
    c = CodexClient(_cfg_codex(), runner=lambda args: "")
    tools = [ToolSpec(name="web_search", description="d", parameters={"type": "object"})]
    with pytest.raises(LLMError, match="không hỗ trợ tool-use"):
        c.complete(system="s", user="u", schema={}, model_tier="standard", tools=tools)


# ---------- ClaudeCodeClient: nhánh lỗi subprocess/JSON còn thiếu ----------

def test_claude_code_subprocess_timeout_and_nonzero_returncode(monkeypatch):
    import subprocess

    from studio.llm import ClaudeCodeClient

    cfg = LLMConfig(provider="claude-code", models={"standard": "m"})
    c = ClaudeCodeClient(cfg, binary="claude")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    with pytest.raises(LLMError, match="quá"):
        c._subprocess(["claude"], "prompt")

    class R:
        returncode = 3
        stdout = "out"
        stderr = "loi chi tiet"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    with pytest.raises(LLMError, match="thoát mã 3"):
        c._subprocess(["claude"], "prompt")

    class OK:
        returncode = 0
        stdout = "day la stdout that"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: OK())
    assert c._subprocess(["claude"], "prompt") == "day la stdout that"


def test_claude_code_complete_raises_on_malformed_json_with_brace():
    from studio.llm import ClaudeCodeClient

    cfg = LLMConfig(provider="claude-code", models={"standard": "m"})
    c = ClaudeCodeClient(cfg, runner=lambda a, p: "chatter {not valid json")
    with pytest.raises(LLMError, match="không phải JSON"):
        c.complete(system="s", user="u", schema={}, model_tier="standard")


def test_codex_complete_multi_turn_messages_joined():
    seen = {}

    def runner(args):
        seen["prompt"] = args[-1]
        return json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}})

    c = CodexClient(_cfg_codex(), runner=runner)
    c.complete(system="s", user="u", schema={}, model_tier="standard",
              messages=[{"role": "user", "content": "hoi"}, {"role": "assistant", "content": "tra loi"}])
    assert "[user]" in seen["prompt"] and "[assistant]" in seen["prompt"]
