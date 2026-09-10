"""Ba adapter hợp nhất ở K3.3c2: `AnthropicClient`, `CodexClient`, `FakeClient` (+ `cli_env`, `check_argv`,
`anthropic_input_tokens`).

Đây là chỗ **studio được nâng**, nên mỗi điểm nâng cần một ca đứng tên nó — và cần đúng ở core, không mượn suite
của company. Đo lúc hợp nhất: đột biến "bỏ `timeout`" và "lỗi mạng về `LLMError`" làm company ĐỎ nhưng studio
XANH, tức studio không có ca nào canh chính thứ nó vừa nhận. Mã nay ở core thì ca cũng phải ở core, nếu không
một PR sau gỡ `TransientError` đi sẽ chỉ đỏ ở một trong hai suite và dễ bị đọc là "lỗi của company".

Bốn điểm nâng, mỗi điểm một ca:
1. `timeout` truyền xuống SDK — không có nó, một request treo giữ luôn orchestrator.
2. Lỗi mạng / 4xx-5xx tạm thời → `TransientError` (hoãn), không phải `LLMError` (dừng).
3. `cache_write_tokens` — Anthropic để token GHI vào cache ngoài `input_tokens`.
4. Prompt của codex đi qua **stdin**, kèm `check_argv` cho phần argv còn lại.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from xagents_core.llm import (
    ARGV_LIMIT,
    CLI_SUBTYPE_ERRORS,
    AnthropicClient,
    CodexClient,
    FakeClient,
    LLMConfig,
    LLMError,
    Refused,
    TransientError,
    anthropic_input_tokens,
    check_argv,
    cli_env,
)
from xagents_core.tools import ToolCall, ToolSpec


class _Usage:
    def __init__(self, input_tokens=10, output_tokens=5, **kw):
        self.input_tokens, self.output_tokens = input_tokens, output_tokens
        for k, v in kw.items(): setattr(self, k, v)


class _Block:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items(): setattr(self, k, v)


class _Msg:
    def __init__(self, content, usage, model="claude-x", stop_reason="end_turn", stop_details=None):
        self.content, self.usage, self.model = content, usage, model
        self.stop_reason, self.stop_details = stop_reason, stop_details


class _Stream:
    def __init__(self, final): self._final = final
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self._final


class _ConnErr(Exception): ...


class _StatusErr(Exception):
    def __init__(self, message, status_code=500):
        super().__init__(message); self.message, self.status_code = message, status_code


def _fake_sdk(monkeypatch, final=None, raise_error=None):
    mod = types.ModuleType("anthropic")
    seen: dict = {}

    class _Messages:
        def stream(self, **kw):
            seen["kwargs"] = kw
            if raise_error is not None: raise raise_error
            return _Stream(final)

    class Anthropic:
        def __init__(self, timeout=None): seen["timeout"] = timeout; self.messages = _Messages()

    mod.Anthropic, mod.APIConnectionError, mod.APIStatusError = Anthropic, _ConnErr, _StatusErr
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return seen


def _cfg(**kw):
    return LLMConfig(provider="anthropic", models={"strong": "claude-opus-5", "standard": "claude-sonnet-5"}, **kw)


# ---------- anthropic_input_tokens ----------

def test_anthropic_input_tokens_cong_ca_doc_va_ghi_cache():
    """Anthropic để token cache RA NGOÀI `input_tokens`. Không cộng lại thì `audit-log.tokens` bỏ sót gần hết
    system prompt (phần lặp nằm trong cache) và trần ngân sách của supervisor không bao giờ chạm."""
    assert anthropic_input_tokens(_Usage(input_tokens=10, cache_read_input_tokens=2,
                                         cache_creation_input_tokens=3)) == (15, 2, 3)
    assert anthropic_input_tokens(_Usage(input_tokens=10)) == (10, 0, 0)


# ---------- AnthropicClient ----------

def test_anthropic_timeout_di_xuong_sdk(monkeypatch):
    """Điểm nâng 1. Studio trước không đặt timeout: một request treo giữ luôn cả orchestrator — vòng lặp tuần
    tự, một tiến trình, không ai gỡ được ngoài Ctrl-C."""
    seen = _fake_sdk(monkeypatch, _Msg([_Block("text", text="{}")], _Usage()))
    AnthropicClient(_cfg())
    assert seen["timeout"] == 600.0
    AnthropicClient(_cfg(), timeout=12.0)
    assert seen["timeout"] == 12.0


def test_anthropic_tra_ve_du_token_cache_va_tool_call(monkeypatch):
    """Điểm nâng 3: `cache_write_tokens` phải ra tới `Completion`, không dừng ở helper."""
    usage = _Usage(input_tokens=10, output_tokens=7, cache_read_input_tokens=2, cache_creation_input_tokens=3)
    blocks = [_Block("text", text='{"a": 1}'), _Block("tool_use", id="t1", name="web", input={"q": "x"})]
    _fake_sdk(monkeypatch, _Msg(blocks, usage))
    c = AnthropicClient(_cfg())
    out = c.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong",
                     tools=[ToolSpec("web", "tìm", {"type": "object"})])
    assert (out.input_tokens, out.cached_input_tokens, out.cache_write_tokens) == (15, 2, 3)
    assert out.output_tokens == 7 and out.text == '{"a": 1}'
    assert out.tool_calls == [ToolCall(id="t1", name="web", args={"q": "x"})]


def test_anthropic_loi_mang_va_ma_tam_thoi_la_transient(monkeypatch):
    """Điểm nâng 2, cái đắt nhất. Studio ném `LLMError` cho MỌI lỗi, mà `LLMError` là lỗi NỘI DUNG —
    orchestrator dừng thay vì hoãn event cho nhịp sau, nên một nhịp mạng chập làm hỏng cả lượt."""
    _fake_sdk(monkeypatch, raise_error=_ConnErr("đứt cáp"))
    with pytest.raises(TransientError, match="lỗi mạng"):
        AnthropicClient(_cfg()).complete(system="s", user="u", schema={}, model_tier="strong")

    _fake_sdk(monkeypatch, raise_error=_StatusErr("quá tải", status_code=529))
    with pytest.raises(TransientError) as ei:
        AnthropicClient(_cfg()).complete(system="s", user="u", schema={}, model_tier="strong")
    assert ei.value.status == 529


def test_anthropic_ma_khong_tam_thoi_van_la_llm_error(monkeypatch):
    """Mặt kia của điểm nâng 2: 400 là lỗi của ta, retry vô ích. Nếu ca này biến mất thì `TransientError` nuốt
    luôn lỗi vĩnh viễn và orchestrator quay vòng mãi trong im lặng."""
    _fake_sdk(monkeypatch, raise_error=_StatusErr("schema sai", status_code=400))
    with pytest.raises(LLMError) as ei:
        AnthropicClient(_cfg()).complete(system="s", user="u", schema={}, model_tier="strong")
    assert not isinstance(ei.value, TransientError) and ei.value.status == 400


def test_anthropic_refusal_mang_theo_ly_do(monkeypatch):
    _fake_sdk(monkeypatch, _Msg([], _Usage(), stop_reason="refusal",
                                stop_details=types.SimpleNamespace(category="harmful")))
    with pytest.raises(Refused, match="harmful"):
        AnthropicClient(_cfg()).complete(system="s", user="u", schema={}, model_tier="strong")


def test_anthropic_ghep_tool_result_lien_tiep_vao_mot_luot_user():
    """`_messages`: nhiều `tool_result` của cùng một lượt phải gộp vào MỘT message user — API từ chối nếu tách."""
    msgs = AnthropicClient._messages([
        {"role": "user", "content": "hỏi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "n", "args": {}}]},
        {"role": "tool", "tool_call_id": "a", "content": "kq1"},
        {"role": "tool", "tool_call_id": "b", "content": "kq2"},
    ])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in msgs[-1]["content"]] == ["a", "b"]
    assert msgs[1]["content"][0]["type"] == "tool_use"   # assistant không có text → chỉ khối tool_use


# ---------- CodexClient ----------

def _codex(out, **kw):
    seen: list = []
    cfg = LLMConfig(provider="codex", models={"strong": "gpt-5.6", "standard": "gpt-5.6"}, **kw)
    return CodexClient(cfg, binary="codex", runner=lambda a, stdin: (seen.append((a, stdin)), out)[1]), seen


OK = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"answer": "ok"}'}}) + "\n" + \
     json.dumps({"type": "turn.completed",
                 "usage": {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 7,
                           "output_tokens": 9}})


def test_codex_prompt_di_qua_stdin_khong_qua_argv():
    """Điểm nâng 4. Prompt dài trên argv làm hệ điều hành thoát với `Argument list too long` — một thông điệp
    không nói gì về prompt, đúng khuôn 1 của TRAPS §1 (chế độ hỏng không tự khai báo)."""
    c, seen = _codex(OK)
    out = c.complete(system="SYS", user="USER", schema={"type": "object"}, model_tier="standard")
    args, stdin = seen[0]
    assert "SYS" in stdin and "USER" in stdin and "JSON Schema" in stdin
    assert not any("SYS" in a for a in args), "prompt không được nằm trong argv"
    assert args[1] == "exec" and "--json" in args and args[args.index("-s") + 1] == "read-only"
    assert (out.input_tokens, out.cached_input_tokens, out.cache_write_tokens, out.output_tokens) == (100, 40, 7, 9)


def test_codex_thuc_su_goi_check_argv_chu_khong_chi_co_ham_do():
    """Đo hai chiều lộ ra lỗ: bỏ hẳn `check_argv(args)` khỏi `CodexClient.complete` mà bộ test vẫn XANH, vì ca
    `test_check_argv_*` chỉ gọi hàm đó TRỰC TIẾP — nó chứng minh hàm đúng, không chứng minh ai gọi nó. Ca này đi
    qua đúng `complete()` với argv vượt trần (tên model khổng lồ) nên nối được hai đầu.

    Nhớ vì sao trần này tồn tại: vượt argv làm hệ điều hành thoát với `Argument list too long`, một thông điệp
    không nhắc gì tới prompt — đúng khuôn 1 của TRAPS §1."""
    c, seen = _codex(OK)
    c.cfg.models["strong"] = "m" * (ARGV_LIMIT + 10)
    with pytest.raises(LLMError, match="argv của CLI dài"):
        c.complete(system="s", user="u", schema={}, model_tier="strong")
    assert seen == [], "phải chặn TRƯỚC khi sinh tiến trình con, không phải sau"


def test_codex_loi_han_muc_la_transient_loi_dang_nhap_thi_khong():
    """Hết quota là chờ được; chưa đăng nhập thì chờ bao lâu cũng thế. Studio trước gộp cả hai vào `LLMError`."""
    c, _ = _codex(json.dumps({"type": "error", "message": "429 rate limited"}))
    with pytest.raises(TransientError, match="429"):
        c.complete(system="s", user="u", schema={}, model_tier="strong")

    c, _ = _codex(json.dumps({"type": "error", "message": "not logged in, run codex login"}))
    with pytest.raises(LLMError, match="chưa đăng nhập") as ei:
        c.complete(system="s", user="u", schema={}, model_tier="strong")
    assert not isinstance(ei.value, TransientError)


def test_codex_canh_bao_metadata_khong_phai_loi():
    c, _ = _codex("\n".join([json.dumps({"type": "error", "message": "Defaulting to fallback metadata"}),
                             json.dumps({"type": "item.completed",
                                         "item": {"type": "agent_message", "text": "{}"}})]))
    assert c.complete(system="s", user="u", schema={}, model_tier="strong").text == "{}"


def test_codex_dong_jsonl_hong_bi_bo_qua_khong_lam_sap_ca_luot():
    """`codex exec` in JSONL, và một dòng hỏng giữa chừng không được giết cả lượt: nó có thể chỉ là log lạ,
    trong khi `agent_message` đứng ngay sau. Hai nhánh bỏ qua: dòng không bắt đầu bằng `{`, và dòng bắt đầu
    bằng `{` nhưng không phải JSON hợp lệ."""
    c, _ = _codex("\n".join([
        "log thuong khong phai json",
        '{"type": "item.completed", "item": {"type": "agent_mess',      # bắt đầu bằng { nhưng vỡ
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"a": 1}'}}),
    ]))
    assert c.complete(system="s", user="u", schema={}, model_tier="strong").text == '{"a": 1}'


def test_codex_error_long_trong_item_completed_cung_la_loi():
    """Codex báo lỗi ở HAI chỗ: sự kiện `error` cấp trên, và `item.completed` mang `item.type == "error"`.
    Bỏ sót chỗ thứ hai là lỗi đi qua im lặng rồi lộ ra dưới dạng "không trả agent_message" — sai nguyên nhân."""
    c, _ = _codex(json.dumps({"type": "item.completed", "item": {"type": "error", "message": "sandbox tu choi"}}))
    with pytest.raises(LLMError, match="sandbox tu choi"):
        c.complete(system="s", user="u", schema={}, model_tier="strong")


def test_codex_khong_co_agent_message_va_loi_la():
    c, _ = _codex("khong phai json\n" + json.dumps({"type": "turn.completed", "usage": {}}))
    with pytest.raises(LLMError, match="không trả agent_message"):
        c.complete(system="s", user="u", schema={}, model_tier="strong")

    c, _ = _codex(json.dumps({"type": "turn.failed", "error": {"message": "loi la"}}))
    with pytest.raises(LLMError, match="loi la"):
        c.complete(system="s", user="u", schema={}, model_tier="strong")


def test_codex_tu_choi_tool_va_gop_nhieu_luot():
    c, seen = _codex(OK)
    with pytest.raises(LLMError, match="không hỗ trợ tool-use"):
        c.complete(system="s", user="u", schema={}, model_tier="strong",
                   tools=[ToolSpec("web", "d", {"type": "object"})])
    c.complete(system="s", user="u", schema={}, model_tier="strong",
               messages=[{"role": "user", "content": "hoi"}, {"role": "assistant", "content": "dap"}])
    assert "[user]" in seen[0][1] and "[assistant]" in seen[0][1]


def test_codex_config_dir_thanh_codex_home(tmp_path):
    c, _ = _codex(OK, config_dir=str(tmp_path / "acc2"))
    assert c.env["CODEX_HOME"].endswith("acc2")


def test_codex_subprocess_that_phan_loai_dung_ba_loai_hong(monkeypatch):
    """`_subprocess` là đường duy nhất chạm hệ điều hành. Timeout là `TransientError` (điểm nâng 2 áp cho codex);
    thiếu binary và thoát mã ≠ 0 là `LLMError` — chờ thêm không làm chúng đúng lên."""
    import subprocess
    c, _ = _codex(OK)

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(LLMError, match="không tìm thấy"):
        c._subprocess(["codex"], "p")

    def timeout(*a, **k): raise subprocess.TimeoutExpired(cmd="codex", timeout=1)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(TransientError, match="quá"):
        c._subprocess(["codex"], "p")

    class R: returncode, stdout, stderr = 2, "o" * 900, "e"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    with pytest.raises(LLMError, match="thoát mã 2"):
        c._subprocess(["codex"], "p")

    class Ok: returncode, stdout, stderr = 0, "day la stdout", ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Ok())
    assert c._subprocess(["codex"], "p") == "day la stdout"


# ---------- FakeClient ----------

def test_fake_client_ghi_ca_luot_gui_di_lan_doi_so_goc():
    """Hai thứ khác nhau khi có `messages`, và cả hai đều có người dùng: `user` là lượt thật sự gửi đi (thứ model
    đọc), `user_arg` là đối số caller truyền vào — và `evals.prompt_key(system, user)` băm ĐỐI SỐ, nên test nào
    đối chiếu khoá eval phải dùng `user_arg`. Bản cũ của mỗi công ty đều mất đúng thứ bên kia dùng."""
    f = FakeClient(responses=[{"ok": 1}])
    f.complete(system="s", user="GOC", schema={}, model_tier="strong",
               messages=[{"role": "user", "content": "GOC + phần tool"}])
    call = f.calls[0]
    assert call["user"] == "GOC + phần tool" and call["user_arg"] == "GOC"

    f2 = FakeClient(responses=[{"ok": 1}])
    f2.complete(system="s", user="GOC", schema={}, model_tier="strong")
    assert f2.calls[0]["user"] == f2.calls[0]["user_arg"] == "GOC"   # không có messages thì hai cái trùng


def test_fake_client_handler_tool_va_het_cau_tra_loi():
    f = FakeClient(handler=lambda s, u: {"tu": "handler"})
    assert json.loads(f.complete(system="s", user="u", schema={}, model_tier="light").text) == {"tu": "handler"}

    f = FakeClient(responses=[{"a": 1}],
                   tool_handler=lambda msgs, tools: [ToolCall("c", "web", {})] if len(msgs) == 1 else [])
    out = f.complete(system="s", user="u", schema={}, model_tier="strong",
                     tools=[ToolSpec("web", "d", {"type": "object"})])
    assert out.stop_reason == "tool_use" and out.tool_calls[0].name == "web" and out.text == ""
    out2 = f.complete(system="s", user="u", schema={}, model_tier="strong",
                      tools=[ToolSpec("web", "d", {"type": "object"})],
                      messages=[{"role": "user", "content": "u"}, {"role": "tool", "tool_call_id": "c", "content": "kq"}])
    assert json.loads(out2.text) == {"a": 1}

    with pytest.raises(LLMError, match="hết câu trả lời"):
        FakeClient().complete(system="s", user="u", schema={}, model_tier="strong")


# ---------- cli_env / check_argv ----------

def test_cli_env_bo_khoa_tru_tien_to_cli_can(monkeypatch):
    """Trước khi có hàm này, adapter truyền nguyên `os.environ` — khoá TTS/ảnh/YouTube của phòng ban đi thẳng vào
    tiến trình con, thứ không lượt gọi model nào cần tới."""
    monkeypatch.setenv("COMPANY_LLM_API_KEY", "x")
    monkeypatch.setenv("STUDIO_LLM_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "giu")
    monkeypatch.setenv("PATH_LIKE_NORMAL", "giu")
    env = cli_env(keep_prefixes=("OPENAI_",))
    assert "COMPANY_LLM_API_KEY" not in env and "STUDIO_LLM_API_KEY" not in env
    assert "ELEVENLABS_API_KEY" not in env
    assert env["OPENAI_API_KEY"] == "giu" and env["PATH_LIKE_NORMAL"] == "giu"


def test_check_argv_bao_ro_thay_vi_de_he_dieu_hanh_bao_kho_hieu():
    check_argv(["codex", "exec"])   # dưới trần: im lặng
    with pytest.raises(LLMError, match="argv của CLI dài"):
        check_argv(["codex", "x" * (ARGV_LIMIT + 1)])


# ---------- OpenAICompatClient (K3.3c3 bước 1) ----------
#
# Năm điểm studio đang thiếu, mỗi cái một ca. Điểm 1 là **bug thật của studio**, không chỉ là thiếu sót: bản cũ
# tắt `json_schema`/`prompt_cache_key` khi gặp BẤT KỲ 400 nào, kể cả 400 vì một lý do chẳng liên quan — và tắt
# im lặng, vì lượt sau vẫn "chạy được", chỉ là chạy ở chế độ kém hơn.

import urllib.error  # noqa: E402

from xagents_core.llm import OpenAICompatClient  # noqa: E402


class _Resp:
    def __init__(self, payload): self._b = json.dumps(payload).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._b


def _oa(**kw):
    return OpenAICompatClient(LLMConfig(provider="openai", base_url="http://x/v1", api_key="k",
                                        models={"strong": "m", "standard": "m"}, **kw))


def _ok_body(content='{"a": 1}', finish="stop", **extra):
    return {"model": "m", "choices": [{"finish_reason": finish, "message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}, **extra}


def test_openai_400_khong_lien_quan_KHONG_duoc_tat_json_schema(monkeypatch):
    """Điểm nâng 1 — bug thật của studio. Một 400 vì prompt quá dài không được quy cho `json_schema` rồi tắt
    structured output vĩnh viễn cho cả tiến trình. `_rejects` đòi thân lỗi NHẮC TỚI đúng tính năng đang dò."""
    c = _oa()
    def post(body):
        if body.get("response_format", {}).get("type") == "json_schema":
            raise LLMError("HTTP 400: prompt is too long for this model")
        return _ok_body()
    monkeypatch.setattr(c, "_post_cacheable", post)
    with pytest.raises(LLMError, match="too long"):
        c.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong")
    assert c._json_schema_ok is None, "400 vì lý do khác không được tắt json_schema"


def test_openai_400_dung_tinh_nang_thi_lui_ve_json_object(monkeypatch):
    """Mặt kia của điểm 1: 400 CÓ nhắc tính năng thì lùi thật, và lượt sau không thử lại nữa."""
    c = _oa()
    seen = []
    def post(body):
        seen.append(body)
        if body.get("response_format", {}).get("type") == "json_schema":
            raise LLMError("HTTP 400: response_format json_schema unsupported")
        return _ok_body()
    monkeypatch.setattr(c, "_post_cacheable", post)
    assert c.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong").text == '{"a": 1}'
    assert c._json_schema_ok is False and len(seen) == 2
    seen.clear()
    c.complete(system="s", user="u2", schema={"type": "object"}, model_tier="strong")
    assert len(seen) == 1 and seen[0].get("response_format", {}).get("type") != "json_schema"


def test_openai_prompt_cache_key_cung_theo_luat_do_rieng(monkeypatch):
    """`_post_cacheable` tách riêng khỏi dò `json_schema` để một 400 không bị quy sai cho tính năng kia."""
    c = _oa()
    seen = []
    def post(body):
        seen.append(dict(body))
        if "prompt_cache_key" in body:
            raise LLMError("HTTP 400: unknown parameter prompt_cache_key")
        return _ok_body()
    monkeypatch.setattr(c, "_post", post)
    c._post_cacheable({"a": 1, "prompt_cache_key": "ck"})
    assert c._cache_key_ok is False and "prompt_cache_key" not in seen[-1]

    c2 = _oa()
    monkeypatch.setattr(c2, "_post", lambda body: _ok_body())
    c2._post_cacheable({"a": 1, "prompt_cache_key": "ck"})
    assert c2._cache_key_ok is True

    c3 = _oa()
    def post_khac(body): raise LLMError("HTTP 400: prompt is too long")
    monkeypatch.setattr(c3, "_post", post_khac)
    with pytest.raises(LLMError, match="too long"):
        c3._post_cacheable({"a": 1, "prompt_cache_key": "ck"})
    assert c3._cache_key_ok is None, "400 vì lý do khác không được tắt prompt_cache_key"


def test_openai_ma_tam_thoi_va_loi_mang_la_transient(monkeypatch):
    """Điểm nâng 2. Kèm `TimeoutError` — thứ `URLError` KHÔNG phủ, nên bản chỉ bắt `URLError` để nó thoát ra
    ngoài dưới dạng một exception lạ không ai phân loại."""
    c = _oa()

    def http(code):
        def f(req, timeout=None):
            e = urllib.error.HTTPError("u", code, "bad", {}, None)
            e.read = lambda: b"chi tiet"       # type: ignore[method-assign]
            raise e
        return f

    monkeypatch.setattr(urllib.request, "urlopen", http(503))
    with pytest.raises(TransientError) as ei:
        c._post({"a": 1})
    assert ei.value.status == 503

    monkeypatch.setattr(urllib.request, "urlopen", http(400))
    with pytest.raises(LLMError) as ei2:
        c._post({"a": 1})
    assert not isinstance(ei2.value, TransientError) and ei2.value.status == 400

    def url_err(req, timeout=None): raise urllib.error.URLError("dut cap")
    monkeypatch.setattr(urllib.request, "urlopen", url_err)
    with pytest.raises(TransientError, match="lỗi mạng"):
        c._post({"a": 1})

    def to(req, timeout=None): raise TimeoutError("het gio")
    monkeypatch.setattr(urllib.request, "urlopen", to)
    with pytest.raises(TransientError, match="lỗi mạng"):
        c._post({"a": 1})

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp({"ok": True}))
    assert c._post({"a": 1}) == {"ok": True}


def test_openai_het_han_muc_dau_ra_noi_ro_thay_vi_de_runner_bao_sai(monkeypatch):
    """Điểm nâng 3, khuôn 1 của TRAPS §1. Trước đó lượt này lọt xuống dưới với `text` cụt rồi runner báo "đầu ra
    không phải JSON" — người đọc đi sửa prompt, trong khi việc cần làm là tăng `max_tokens`. Thông điệp phải nói
    được cả trường hợp model tiêu sạch hạn mức vào token SUY NGHĨ mà chưa trả lời câu nào."""
    c = _oa(max_tokens=100)
    monkeypatch.setattr(c, "_post_cacheable", lambda body: _ok_body(
        content="", finish="length",
        usage={"prompt_tokens": 5, "completion_tokens": 100,
               "completion_tokens_details": {"reasoning_tokens": 98}}))
    with pytest.raises(LLMError) as ei:
        c.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong")
    m = str(ei.value)
    assert "finish_reason=length" in m and "max_tokens=100" in m
    assert "98 token suy nghĩ" in m and "RỖNG" in m

    c2 = _oa(max_tokens=100)
    monkeypatch.setattr(c2, "_post_cacheable", lambda body: _ok_body(
        content='{"a": 1', finish="length", usage={"prompt_tokens": 5, "completion_tokens": 100}))
    with pytest.raises(LLMError, match="bị cắt giữa chừng"):
        c2.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong")


def test_openai_than_rong_voi_http_200_la_transient_co_ten(monkeypatch):
    """Điểm nâng 4 — khuôn 1 đúng nguyên văn: 200 nên cả hai bên tưởng bình thường. Nguyên nhân thật (đo
    2026-09-04): server trả JSON qua `tool_calls` thay vì `message.content`, nên content rỗng trong khi dữ liệu
    nằm nguyên ở `tool_calls[0].function.arguments`; vì là 200, `_json_schema_ok` vẫn True và MỌI lượt sau hỏng
    y hệt. Thông điệp phải chỉ thẳng cách kiểm."""
    c = _oa()
    monkeypatch.setattr(c, "_post_cacheable", lambda body: {
        "model": "m", "choices": [{"finish_reason": "stop",
                                   "message": {"content": "", "reasoning_content": "nghi mot chut"}}],
        "usage": {}})
    with pytest.raises(TransientError) as ei:
        c.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong")
    m = str(ei.value)
    assert "không trả về nội dung nào" in m and "ký tự suy nghĩ" in m and "json_object" in m


def test_openai_tool_call_thi_than_rong_la_hop_le(monkeypatch):
    """Ranh giới của điểm 4: content rỗng KÈM `tool_calls` là lượt gọi tool bình thường, không được báo lỗi.
    Bỏ vế `not calls` là mọi vòng tool của company chết ngay."""
    c = _oa()
    monkeypatch.setattr(c, "_post_cacheable", lambda body: {
        "model": "m", "choices": [{"finish_reason": "tool_calls", "message": {
            "content": "", "tool_calls": [{"id": "t1", "function": {"name": "web", "arguments": '{"q": "x"}'}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                  "prompt_tokens_details": {"cached_tokens": 4}}})
    out = c.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong",
                     tools=[ToolSpec("web", "d", {"type": "object"})])
    assert out.tool_calls == [ToolCall(id="t1", name="web", args={"q": "x"})]
    # Điểm nâng 5: `prompt_tokens` của OpenAI ĐÃ gồm phần cache (ngược với Anthropic) — `cached` chỉ để báo cáo.
    assert out.input_tokens == 10 and out.cached_input_tokens == 4


def test_openai_content_filter_va_tool_arguments_hong(monkeypatch):
    c = _oa()
    monkeypatch.setattr(c, "_post_cacheable", lambda body: _ok_body(finish="content_filter"))
    with pytest.raises(Refused, match="content_filter"):
        c.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong")

    c2 = _oa()
    monkeypatch.setattr(c2, "_post_cacheable", lambda body: {
        "model": "m", "choices": [{"finish_reason": "tool_calls", "message": {
            "content": "", "tool_calls": [{"function": {"name": "web", "arguments": "khong-phai-json"}}]}}],
        "usage": {}})
    out = c2.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong",
                      tools=[ToolSpec("web", "d", {"type": "object"})])
    # `arguments` hỏng không được nuốt: giữ nguyên văn dưới `_raw` để runner/audit còn thấy model đã định nói gì.
    assert out.tool_calls[0].args == {"_raw": "khong-phai-json"} and out.tool_calls[0].id == "call_0"


def test_openai_messages_dung_dinh_dang_openai_cho_ca_vong_tool(monkeypatch):
    """`_messages` là chỗ đổi định dạng trung lập → OpenAI, và nó phải đúng cho CẢ vòng tool: assistant có
    `tool_calls` (content rỗng thì phải là `None`, không phải `""` — vài server từ chối chuỗi rỗng), rồi
    `role: tool` mang `tool_call_id`. Sai ở đây thì mọi lượt sau lượt đầu của company hỏng."""
    out = OpenAICompatClient._messages("SYS", [
        {"role": "user", "content": "hoi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "web", "args": {"q": "x"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "ket qua"},
    ])
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[0]["content"] == "SYS"
    assert out[2]["content"] is None and json.loads(out[2]["tool_calls"][0]["function"]["arguments"]) == {"q": "x"}
    assert out[3]["tool_call_id"] == "t1" and out[3]["content"] == "ket qua"


def test_openai_gui_prompt_cache_key_va_tool_theo_dung_dinh_dang(monkeypatch):
    """`cache_key` chỉ đi kèm khi server chưa từ chối nó; `tools` đổi sang bọc `{"type": "function", ...}`."""
    c = _oa()
    seen = []
    monkeypatch.setattr(c, "_post_cacheable", lambda body: (seen.append(body), _ok_body())[1])
    c.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong", cache_key="agent-x",
               tools=[ToolSpec("web", "tìm", {"type": "object"})])
    assert seen[0]["prompt_cache_key"] == "agent-x"
    assert seen[0]["tools"][0] == {"type": "function",
                                  "function": {"name": "web", "description": "tìm", "parameters": {"type": "object"}}}
    c._cache_key_ok = False   # đã bị server từ chối một lần thì thôi gửi
    seen.clear()
    c.complete(system="s", user="u", schema={"type": "object"}, model_tier="strong", cache_key="agent-x")
    assert "prompt_cache_key" not in seen[0]


# ---------- ClaudeCodeClient: TRANSPORT dùng chung (K3.3c3 bước 2) ----------
#
# Core chỉ giữ transport; `complete()` ở lại mỗi công ty vì ba chiến lược tool khác nhau THẬT (xem ghi chú dài
# trong `llm.py`). Nên ca ở đây đo đúng transport: dựng tiến trình, đọc JSON, kế toán token, phân loại lỗi —
# và đo cả hai chiều của bản hợp nhất, vì lần này KHÔNG bên nào là gốc.

from xagents_core.llm import ClaudeCodeClient, cli_exit_error  # noqa: E402


class _CC(ClaudeCodeClient):
    """Lớp con tối thiểu: core cố ý không có `complete()` mặc định, nên test phải tự khai một cái."""
    def complete(self, **kw): raise NotImplementedError


def _cc(**kw):
    return _CC(LLMConfig(provider="claude-code", models={"strong": "claude-x", "standard": "claude-x"}, **kw),
               binary="claude")


OUT_OK = json.dumps({
    # `result` cố ý KHÁC `structured_output`: nếu hai cái giống nhau thì ca dưới xanh dù `_parse` đọc nhầm cái
    # nào — đo hai chiều đã lộ đúng lỗ đó (đột biến "bỏ ưu tiên structured_output" sống sót ở lần đo đầu).
    "subtype": "success", "result": '{"a": "BAN CHU CHUA QUA KIEM"}', "structured_output": {"a": 1},
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 2, "cache_creation_input_tokens": 3},
    "modelUsage": {"claude-x": {"outputTokens": 4}}})


def test_claude_parse_uu_tien_structured_output_va_cong_du_token_cache():
    """`--json-schema` → `structured_output` đã parse VÀ đã qua kiểm của CLI: ưu tiên nó, `result` chỉ là bản chữ.
    Token cache của Anthropic nằm NGOÀI `input_tokens` nên phải cộng vào, y như `anthropic_input_tokens`."""
    c = _cc()
    out = c._parse(OUT_OK, "claude-x", tool_mode="mcp")
    assert json.loads(out.text) == {"a": 1}, "phải lấy `structured_output` (đã qua kiểm của CLI), không phải `result`"
    assert out.input_tokens == 15 and out.cached_input_tokens == 2 and out.cache_write_tokens == 3
    assert out.tool_mode == "mcp" and out.output_tokens == 4


def test_claude_parse_is_error_nhac_han_muc_la_transient_con_lai_la_llm_error():
    """Hợp nhất: bản studio ném `LLMError` cho MỌI `is_error`, nên hết quota (chờ được) và lỗi cấu hình (chờ bao
    lâu cũng thế) đi chung một đường, orchestrator dừng ở cả hai."""
    c = _cc()
    with pytest.raises(TransientError, match="rate limit"):
        c._parse(json.dumps({"is_error": True, "result": "rate limit exceeded"}), "claude-x")
    with pytest.raises(LLMError) as ei:
        c._parse(json.dumps({"is_error": True, "result": "cau hinh sai"}), "claude-x")
    assert not isinstance(ei.value, TransientError)


def test_claude_parse_cac_the_hong_khac():
    c = _cc()
    # Có `{` nhưng vỡ giữa chừng → "không phải JSON". KHÔNG có `{` thì `data = {}` và rơi vào nhánh thiếu
    # `result` — hai thông điệp khác nhau cho hai thể hỏng khác nhau, đúng tinh thần khuôn 1 của TRAPS §1.
    with pytest.raises(LLMError, match="không phải JSON"):
        c._parse('log lang nhang {"result": ', "claude-x")
    with pytest.raises(LLMError, match="thiếu trường result"):
        c._parse("khong co dau ngoac nao", "claude-x")
    with pytest.raises(LLMError, match="thiếu trường result"):
        c._parse(json.dumps({"subtype": "success"}), "claude-x")
    with pytest.raises(Refused):
        c._parse(json.dumps({"result": "x", "stop_reason": "refusal"}), "claude-x")
    # Subtype phải đọc TRƯỚC `result` — các subtype này có thể không có `result`, và nếu đọc sau thì lượt ấy
    # báo "thiếu trường result", giấu mất nguyên nhân thật. Khẳng định LỜI GIẢI THÍCH, không chỉ tên subtype:
    # tên subtype có mặt trong cả hai thông điệp nên `match=sub` xanh ở cả hai chiều (đo hai chiều đã lộ).
    sub, giai_thich = next(iter(CLI_SUBTYPE_ERRORS.items()))
    with pytest.raises(LLMError) as ei:
        c._parse(json.dumps({"subtype": sub}), "claude-x")
    assert giai_thich in str(ei.value) and "thiếu trường result" not in str(ei.value)


def test_claude_subprocess_phan_loai_dung_bon_the_hong(monkeypatch):
    """Bốn nhánh, và hai trong số đó là hai NỬA của bản hợp nhất hai chiều:
    - timeout → `TransientError` (company nâng studio: studio ném `LLMError` nên orchestrator dừng thay vì hoãn)
    - `OSError` → `LLMError` có tên (studio nâng company: company KHÔNG bắt, nên nó thoát ra thô)."""
    import subprocess
    c = _cc()

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(LLMError, match="không tìm thấy"):
        c._subprocess(["claude"], "p")

    def to(*a, **k): raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
    monkeypatch.setattr(subprocess, "run", to)
    with pytest.raises(TransientError, match="quá"):
        c._subprocess(["claude"], "p")

    def oserr(*a, **k): raise OSError("argv qua dai")
    monkeypatch.setattr(subprocess, "run", oserr)
    with pytest.raises(LLMError, match="không chạy được") as ei:
        c._subprocess(["claude"], "p")
    assert not isinstance(ei.value, TransientError), "OSError là lỗi hẳn, chờ thêm không làm nó đúng lên"

    class Fail: returncode, stdout, stderr = 1, json.dumps({"result": "rate limit"}), ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Fail())
    with pytest.raises(TransientError, match="rate limit"):
        c._subprocess(["claude"], "p")   # thoát mã ≠ 0 đi qua `cli_exit_error`, không phải một LLMError chung

    class Ok: returncode, stdout, stderr = 0, "day la stdout", ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Ok())
    assert c._subprocess(["claude"], "p", cwd="/tmp") == "day la stdout"


def test_cli_exit_error_doc_JSON_thay_vi_soi_duoi_output():
    """Đo được 2026-09-05: đuôi JSON của CLI là telemetry (`"refused":{"depth_limit":0,...}`), nên soi 500 ký tự
    cuối tìm "limit" biến MỌI lần thoát mã 1 thành "hết quota" — routing cho backend nghỉ, tick sau thử lại, lặp
    20 phút mỗi 44s với một lỗi thật không ai đọc được, trong khi `claude -p` gọi tay chạy bình thường."""
    telemetry = json.dumps({"subtype": "error", "result": "schema field 'x' invalid",
                            "refused": {"depth_limit": 0, "concurrency_limit": 0}})
    e = cli_exit_error(1, telemetry, "")
    assert not isinstance(e, TransientError), "telemetry chứa chữ 'limit' không được biến lỗi schema thành hết quota"
    assert "invalid" in str(e)

    assert isinstance(cli_exit_error(1, json.dumps({"result": "rate limit"}), ""), TransientError)
    assert isinstance(cli_exit_error(1, json.dumps({"api_error_status": 529, "result": "x"}), ""), TransientError)
    # Không có JSON thì mới dùng stderr
    assert isinstance(cli_exit_error(1, "", "overloaded"), TransientError)
    assert not isinstance(cli_exit_error(1, "", "loi la"), TransientError)
    # JSON hỏng / không phải object → rơi về đường stderr
    assert isinstance(cli_exit_error(1, "{khong phai json", "rate limit"), TransientError)
    assert not isinstance(cli_exit_error(1, "[1, 2]", "loi la"), TransientError)


def test_claude_init_loc_env_va_config_dir_rieng(monkeypatch, tmp_path):
    """Nhiều tài khoản Claude trên một máy: mỗi backend một `CLAUDE_CONFIG_DIR`. Và env phải đã lọc — khoá của
    công ty không đi vào tiến trình con."""
    monkeypatch.setenv("COMPANY_LLM_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "giu")
    c = _cc(config_dir=str(tmp_path / "acc2"))
    assert c.env["CLAUDE_CONFIG_DIR"].endswith("acc2")
    assert "COMPANY_LLM_API_KEY" not in c.env and "ELEVENLABS_API_KEY" not in c.env
    assert c.env["ANTHROPIC_API_KEY"] == "giu"
    assert _cc().env.get("CLAUDE_CONFIG_DIR") is None


def test_claude_core_khong_co_complete_mac_dinh():
    """Cố ý: một `complete()` "không tool" mặc định sẽ im lặng nuốt mất chiến lược tool của bên nào quên ghi đè,
    mà im lặng đúng là thứ TRAPS.md §1 cấm. Lớp con phải tự khai."""
    assert "complete" not in vars(ClaudeCodeClient)


def test_openai_post_cacheable_body_khong_co_cache_key_thi_khong_bat_co(monkeypatch):
    """Gọi THÀNH CÔNG mà body không mang `prompt_cache_key` → không được bật `_cache_key_ok`.

    Cờ này nghĩa là "server ĐÃ nhận cache key", nên bật nó sau một lượt không hề gửi key là kết luận từ bằng
    chứng không tồn tại — và nó dán luôn cho backend, khiến lượt sau tin nhầm là cache đang chạy."""
    c = _oa()
    monkeypatch.setattr(c, "_post", lambda body: _ok_body())

    c._post_cacheable({"a": 1})             # không có prompt_cache_key

    assert c._cache_key_ok is None          # vẫn "chưa biết", không phải True


def test_openai_messages_assistant_khong_co_tool_calls():
    """Assistant chỉ trả văn bản (không gọi tool) — vẫn phải vào `out`, không mất message.

    Lượt cuối của mọi vòng tool đúng là hình dạng này: model thôi gọi tool và trả lời. Bỏ sót nó là mất chính
    câu trả lời cuối."""
    out = OpenAICompatClient._messages("hệ thống", [
        {"role": "assistant", "content": "xong rồi"},
        {"role": "user", "content": "cảm ơn"},
    ])

    assert out == [
        {"role": "system", "content": "hệ thống"},
        {"role": "assistant", "content": "xong rồi"},
        {"role": "user", "content": "cảm ơn"},
    ]
# ---------- p3.2b/c: breakpoint cache thứ hai + TTL dài ----------

def _breakpoints(obj):
    """Mọi `cache_control` trong một cây JSON, kèm đường đi — test khẳng định VỊ TRÍ, không chỉ số lượng."""
    out = []
    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "cache_control": out.append((path, v))
                else: walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node): walk(v, f"{path}[{i}]")
    walk(obj, "")
    return out


def _tools3():
    return [ToolSpec(n, f"tool {n}", {"type": "object"}) for n in ("read_file", "write_file", "run")]


def test_breakpoint_cache_o_dinh_nghia_tool_cuoi_va_system(monkeypatch):
    """Thứ tự dựng tiền tố của Anthropic là `tools` → `system` → `messages` (skill `claude-api`,
    shared/prompt-caching.md: "Render order is: tools -> system -> messages").

    Vì `spec.system_prompt(phase)` ĐỔI theo pha (ADR-0037) mà danh sách tool thì không, một breakpoint duy nhất
    trên system nghĩa là đổi pha làm trượt cache cả phần tool. Breakpoint thứ hai đặt trên ĐỊNH NGHĨA TOOL CUỐI
    — ranh giới ổn định đứng trước system — để phần tool vẫn hit khi system đổi.
    """
    seen = _fake_sdk(monkeypatch, _Msg([_Block("text", text="{}")], _Usage()))
    AnthropicClient(_cfg()).complete(system="s", user="u", schema={"type": "object"},
                                     model_tier="strong", tools=_tools3())
    kw = seen["kwargs"]
    assert kw["tools"][-1]["cache_control"] == {"type": "ephemeral"}, "breakpoint trên tool CUỐI"
    assert all("cache_control" not in t for t in kw["tools"][:-1]), "chỉ tool cuối, không phải mọi tool"
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}, "breakpoint system giữ nguyên"
    assert _breakpoints(kw["messages"]) == [], "không breakpoint nào trong phần xoay vòng của messages"
    assert len(_breakpoints(kw["tools"]) + _breakpoints(kw["system"])) == 2, "đúng 2 breakpoint, trần của Anthropic là 4"


def test_khong_co_tool_thi_van_dung_mot_breakpoint(monkeypatch):
    seen = _fake_sdk(monkeypatch, _Msg([_Block("text", text="{}")], _Usage()))
    AnthropicClient(_cfg()).complete(system="s", user="u", schema={"type": "object"}, model_tier="strong")
    assert "tools" not in seen["kwargs"] and len(_breakpoints(seen["kwargs"]["system"])) == 1


def test_ttl_mac_dinh_tat_body_y_het_hom_nay(monkeypatch):
    """CHIỀU NGƯỢC 3: không cấu hình `cache_ttl` → không khoá `ttl` nào trong body, và KHÔNG header
    `anthropic-beta` (`extra_headers`) — TTL 1 giờ hôm nay không cần beta header."""
    seen = _fake_sdk(monkeypatch, _Msg([_Block("text", text="{}")], _Usage()))
    AnthropicClient(_cfg()).complete(system="s", user="u", schema={"type": "object"},
                                     model_tier="strong", tools=_tools3())
    kw = seen["kwargs"]
    assert all("ttl" not in v for _p, v in _breakpoints(kw["tools"]) + _breakpoints(kw["system"]))
    assert "extra_headers" not in kw


def test_ttl_dai_bat_qua_cau_hinh(monkeypatch):
    seen = _fake_sdk(monkeypatch, _Msg([_Block("text", text="{}")], _Usage()))
    AnthropicClient(_cfg(cache_ttl="1h")).complete(system="s", user="u", schema={"type": "object"},
                                                   model_tier="strong", tools=_tools3())
    kw = seen["kwargs"]
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert kw["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "extra_headers" not in kw, "TTL 1h không cần anthropic-beta (shared/prompt-caching.md § API reference)"
