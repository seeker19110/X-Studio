import json
from pathlib import Path

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
    assert args[args.index("--tools") + 1] == ""
    assert prompt.startswith("USER") and "JSON Schema" in prompt and "USER" not in " ".join(args)  # user message qua stdin, không qua argv
    # ADR-0026: system prompt qua FILE (argv có trần ~32 KB trên Windows), schema cũng đi qua `--json-schema`,
    # và `-p` không được ghi transcript (kịch bản, dossier) ra ~/.claude.
    assert "--system-prompt" not in args, "system prompt không còn đi qua argv"
    assert Path(args[args.index("--system-prompt-file") + 1]).name.startswith("claude-sp-")
    assert json.loads(args[args.index("--json-schema") + 1]) == {"type": "object"}
    assert "--no-session-persistence" in args


def test_claude_code_system_prompt_file_mang_dung_noi_dung_va_bi_xoa_sau_luot():
    """File tạm phải mang ĐÚNG system prompt lúc CLI chạy, và không được để lại rác trên đĩa sau đó."""
    paths: list[Path] = []

    def runner(args, prompt):
        p = Path(args[args.index("--system-prompt-file") + 1])
        paths.append(p)
        assert p.read_text(encoding="utf-8") == "SYS-DAI" * 100, "CLI phải đọc được đúng system prompt"
        return json.dumps({"result": "{}", "usage": {"input_tokens": 1, "output_tokens": 1}})

    ClaudeCodeClient(_cfg(), runner=runner).complete(system="SYS-DAI" * 100, user="u", schema={}, model_tier="strong")
    assert paths and not paths[0].exists(), "file tạm bị xoá sau lượt"


def test_claude_code_effort_va_structured_output_va_subtype_loi():
    """ADR-0026 đồng bộ từ software-company: `effort` xuống `--effort` (giá trị ngoài bảng hỏng to);
    `structured_output` đã qua kiểm của CLI thắng `result` dạng chữ; `subtype` lỗi thành thông báo nói đúng việc."""
    seen: list[list[str]] = []
    ok = json.dumps({"result": "{}", "usage": {"input_tokens": 1, "output_tokens": 1}})
    cfg = LLMConfig(provider="claude-code", models={"strong": "m"}, effort={"strong": "xhigh"})
    ClaudeCodeClient(cfg, runner=lambda a, p: (seen.append(a), ok)[1]).complete(
        system="s", user="u", schema={}, model_tier="strong")
    assert seen[0][seen[0].index("--effort") + 1] == "xhigh"
    # tier không khai effort → không thêm cờ, CLI dùng mặc định của nó
    ClaudeCodeClient(LLMConfig(provider="claude-code", models={"strong": "m"}, effort={}),
                     runner=lambda a, p: (seen.append(a), ok)[1]).complete(system="s", user="u", schema={}, model_tier="strong")
    assert "--effort" not in seen[1]
    bad = LLMConfig(provider="claude-code", models={"strong": "m"}, effort={"strong": "none"})
    with pytest.raises(LLMError, match=r"effort `none`.*low\|medium\|high\|xhigh\|max"):
        ClaudeCodeClient(bad, runner=lambda a, p: ok).complete(system="s", user="u", schema={}, model_tier="strong")

    so = json.dumps({"result": "Đây là JSON: ...", "structured_output": {"video_id": "V9"},
                     "usage": {"input_tokens": 1, "output_tokens": 1}})
    c = ClaudeCodeClient(_cfg(), runner=lambda a, p: so).complete(system="s", user="u", schema={}, model_tier="strong")
    assert c.json() == {"video_id": "V9"}, "structured_output thắng result văn xuôi"
    for sub, hint in (("error_max_turns", "hết lượt"), ("error_max_budget_usd", "trần chi phí"),
                      ("error_max_structured_output_retries", "JSON Schema"), ("error_during_execution", "lỗi khi đang chạy")):
        with pytest.raises(LLMError, match=f"{sub}.*{hint}"):
            ClaudeCodeClient(_cfg(), runner=lambda a, p, sub=sub: json.dumps({"subtype": sub})).complete(
                system="s", user="u", schema={}, model_tier="strong")


def test_cli_env_khong_mang_khoa_cua_phong_ban_vao_tien_trinh_con(monkeypatch):
    """Trước ADR-0026 adapter truyền nguyên `os.environ` vào `claude`/`codex`: khoá TTS/ảnh/YouTube của phòng ban đi
    thẳng vào tiến trình con dù không lượt gọi model nào cần. Giữ lại đúng thứ CLI cần để đăng nhập."""
    from studio.llm import cli_env
    monkeypatch.setenv("ELEVENLABS_API_KEY", "bi-mat")
    monkeypatch.setenv("STUDIO_LLM_API_KEY", "bi-mat")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "bi-mat")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://vi-du")
    monkeypatch.setenv("PATH_KHONG_BI_MAT", "giu-lai")
    env = cli_env(keep_prefixes=("ANTHROPIC_", "CLAUDE_"))
    assert "ELEVENLABS_API_KEY" not in env and "STUDIO_LLM_API_KEY" not in env
    assert "YOUTUBE_CLIENT_SECRET" not in env
    assert env.get("ANTHROPIC_BASE_URL") == "https://vi-du", "CLI vẫn cần biến đăng nhập/endpoint của nó"
    assert env.get("PATH_KHONG_BI_MAT") == "giu-lai"


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
    # ADR-0026: system prompt dài KHÔNG còn làm vượt trần (nó đi qua file tạm); thứ còn đi qua argv là schema.
    ClaudeCodeClient(_cfg(), runner=lambda a, p: json.dumps({"result": "{}", "usage": {}})).complete(
        system="S" * (llm.CLI_ARGV_MAX + 1), user="u", schema={}, model_tier="strong")
    big = {"type": "object", "properties": {f"f{i}": {"type": "string"} for i in range(llm.CLI_ARGV_MAX // 20)}}
    with pytest.raises(LLMError, match="argv"):
        ClaudeCodeClient(_cfg(), runner=lambda a, p: "{}").complete(system="s", user="u", schema=big, model_tier="strong")

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
