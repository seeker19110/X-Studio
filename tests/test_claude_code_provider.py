import json

import pytest

from studio.llm import ClaudeCodeClient, LLMConfig, LLMError, Refused, make_client, reported_model


def _cfg():
    return LLMConfig(provider="claude-code", models={"strong": "claude-opus-5", "standard": "claude-sonnet-5"})


def test_claude_code_client_parses_print_json_and_counts_cache_tokens():
    seen: list[tuple[list[str], str]] = []

    def runner(args, prompt):
        seen.append((args, prompt))
        return "Warning: no stdin\n" + json.dumps({"result": '{"video_id": "V1"}', "stop_reason": "end_turn",
                                                   "usage": {"input_tokens": 100, "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10, "output_tokens": 7},
                                                   "modelUsage": {"claude-sonnet-5": {}}})

    c = ClaudeCodeClient(_cfg(), runner=runner).complete(system="SYS", user="USER", schema={"type": "object"}, model_tier="standard")
    assert c.json() == {"video_id": "V1"} and c.input_tokens == 150 and c.cached_input_tokens == 40 and c.output_tokens == 7
    assert c.model == "claude-sonnet-5"
    args, prompt = seen[0]
    assert args[1:3] == ["-p", "--output-format"] and "--model" in args and args[args.index("--model") + 1] == "claude-sonnet-5"
    assert args[args.index("--tools") + 1] == "" and args[-2:] == ["--system-prompt", "SYS"]
    assert prompt.startswith("USER") and "JSON Schema" in prompt and "USER" not in " ".join(args)  # user message qua stdin, không qua argv


def test_claude_code_client_errors():
    with pytest.raises(LLMError):
        ClaudeCodeClient(_cfg(), runner=lambda a, p: "not json").complete(system="s", user="u", schema={}, model_tier="strong")
    with pytest.raises(LLMError):
        ClaudeCodeClient(_cfg(), runner=lambda a, p: json.dumps({"is_error": True, "result": "boom"})).complete(system="s", user="u", schema={}, model_tier="strong")
    with pytest.raises(Refused):
        ClaudeCodeClient(_cfg(), runner=lambda a, p: json.dumps({"result": "", "stop_reason": "refusal"})).complete(system="s", user="u", schema={}, model_tier="strong")
    with pytest.raises(LLMError):
        ClaudeCodeClient(_cfg(), binary="claude-binary-khong-ton-tai-xyz").complete(system="s", user="u", schema={}, model_tier="strong")


def test_claude_code_argv_guard_and_oserror_become_llm_error(monkeypatch):
    import subprocess

    from studio import llm
    with pytest.raises(LLMError, match="argv"):
        ClaudeCodeClient(_cfg(), runner=lambda a, p: "{}").complete(system="S" * (llm.CLI_ARGV_MAX + 1), user="u", schema={}, model_tier="strong")

    def boom(*a, **k): raise OSError(7, "Argument list too long")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(LLMError, match="không chạy được"):
        ClaudeCodeClient(_cfg(), binary="claude").complete(system="s", user="u", schema={}, model_tier="strong")


def test_make_client_knows_claude_code():
    assert isinstance(make_client(_cfg()), ClaudeCodeClient)


def test_claude_code_reports_requested_model_not_internal_haiku():
    """`claude -p` liệt kê Haiku (helper nội bộ) trước model chính trong modelUsage; audit phải ghi model chính."""
    out = json.dumps({"result": "{}", "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1},
                      "modelUsage": {"claude-haiku-4-5-20251001": {"outputTokens": 40}, "claude-opus-5": {"outputTokens": 9}}})
    c = ClaudeCodeClient(_cfg(), runner=lambda a, p: out).complete(system="s", user="u", schema={}, model_tier="strong")
    assert c.model == "claude-opus-5"
    assert reported_model({"claude-haiku-4-5-20251001": {"outputTokens": 40}, "x": {"outputTokens": 90}}, "claude-opus-5") == "x"
    assert reported_model({}, "claude-opus-5") == "claude-opus-5"
