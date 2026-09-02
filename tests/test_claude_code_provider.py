import json

import pytest

from studio.llm import ClaudeCodeClient, LLMConfig, LLMError, Refused, make_client


def _cfg():
    return LLMConfig(provider="claude-code", models={"strong": "claude-opus-5", "standard": "claude-sonnet-5"})


def test_claude_code_client_parses_print_json_and_counts_cache_tokens():
    seen: list[list[str]] = []

    def runner(args):
        seen.append(args)
        return "Warning: no stdin\n" + json.dumps({"result": '{"video_id": "V1"}', "stop_reason": "end_turn",
                                                   "usage": {"input_tokens": 100, "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10, "output_tokens": 7},
                                                   "modelUsage": {"claude-sonnet-5": {}}})

    c = ClaudeCodeClient(_cfg(), runner=runner).complete(system="SYS", user="USER", schema={"type": "object"}, model_tier="standard")
    assert c.json() == {"video_id": "V1"} and c.input_tokens == 150 and c.cached_input_tokens == 40 and c.output_tokens == 7
    assert c.model == "claude-sonnet-5"
    args = seen[0]
    assert args[1:3] == ["-p", "--output-format"] and "--model" in args and args[args.index("--model") + 1] == "claude-sonnet-5"
    assert args[args.index("--tools") + 1] == "" and args[args.index("--system-prompt") + 1] == "SYS"
    assert args[-1].startswith("USER") and "JSON Schema" in args[-1]


def test_claude_code_client_errors():
    with pytest.raises(LLMError):
        ClaudeCodeClient(_cfg(), runner=lambda a: "not json").complete(system="s", user="u", schema={}, model_tier="strong")
    with pytest.raises(LLMError):
        ClaudeCodeClient(_cfg(), runner=lambda a: json.dumps({"is_error": True, "result": "boom"})).complete(system="s", user="u", schema={}, model_tier="strong")
    with pytest.raises(Refused):
        ClaudeCodeClient(_cfg(), runner=lambda a: json.dumps({"result": "", "stop_reason": "refusal"})).complete(system="s", user="u", schema={}, model_tier="strong")
    with pytest.raises(LLMError):
        ClaudeCodeClient(_cfg(), binary="claude-binary-khong-ton-tai-xyz").complete(system="s", user="u", schema={}, model_tier="strong")


def test_make_client_knows_claude_code():
    assert isinstance(make_client(_cfg()), ClaudeCodeClient)
