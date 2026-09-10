"""Nền chung của lớp LLM (K3.3a).

Chỉ đo phần đã lên core ở bước a. `LLMConfig`/`load_config` (K3.3b) đo ở `test_llm_config.py`, `Completion` và
đường bóc JSON (K3.3c1) ở `test_llm_completion.py`. Bốn adapter (`AnthropicClient`, `OpenAICompatClient`,
`ClaudeCodeClient`, `CodexClient`) vẫn ở hai bên — chúng lệch nhất, và `test_pham_vi_bon_adapter_chua_len_core`
dưới đây là chốt phạm vi cho điều đó.

Ba nhóm ca đáng giữ:

1. `reported_model` — hàm HỢP NHẤT của K3.3a, chỗ duy nhất không bên nào là gốc. Mỗi nhánh của nó là một cách một
   trong hai công ty từng đọc sai tên model đã chạy.
2. `cli_effort_args` — bảng ĐÓNG. Giá trị lạ phải hỏng to; rơi về mặc định là bài học `none` của codex (cấu hình
   nói một đằng, CLI chạy một nẻo).
3. `system_prompt_args` — phải xoá file tạm KỂ CẢ khi thân `with` ném; file đó chứa system prompt của agent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xagents_core.llm import (
    ARGV_LIMIT,
    CLAUDE_EFFORT,
    CLI_SUBTYPE_ERRORS,
    CODEX_EFFORT,
    TIERS,
    TRANSIENT_HTTP,
    LLMError,
    Refused,
    TransientError,
    cli_effort_args,
    find_codex_binary,
    neutral_messages,
    reported_model,
    strict_schema,
    system_prompt_args,
)

# ---------- lỗi ----------

def test_llm_error_mang_ma_http_va_mac_dinh_none():
    """`status` mặc định `None` là điều kiện để studio nhận lớp này mà không sửa chỗ nào: mọi `LLMError("…")` cũ
    vẫn dựng được."""
    assert LLMError("hỏng").status is None
    assert LLMError("quota", 429).status == 429


def test_thu_bac_ngoai_le_cho_phep_bat_chung_mot_cho():
    """`except LLMError` phải bắt được cả hai lớp con — supervisor và orchestrator dựa vào điều đó."""
    assert issubclass(Refused, LLMError) and issubclass(TransientError, LLMError)
    assert not issubclass(Refused, TransientError), "từ chối KHÁC lỗi tạm: một cái escalate, một cái thử lại"


def test_transient_http_gom_dung_nhom_ma_thu_lai_duoc():
    assert {429, 500, 502, 503, 504} <= TRANSIENT_HTTP
    assert 401 not in TRANSIENT_HTTP and 404 not in TRANSIENT_HTTP, "lỗi xác thực/thiếu model thử lại là vô ích"


# ---------- helper trung lập ----------

def test_neutral_messages_giu_hoi_thoai_hoac_dung_user():
    assert neutral_messages("xin chào", None) == [{"role": "user", "content": "xin chào"}]
    hoi_thoai = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert neutral_messages("bỏ qua", hoi_thoai) is hoi_thoai
    assert neutral_messages("bỏ qua", []) == [], "hội thoại RỖNG khác với 'không có hội thoại'"


def test_strict_schema_dong_moi_object_va_khong_dung_ban_goc():
    goc = {"type": "object", "properties": {"a": {"type": "object", "properties": {}},
                                            "ds": {"type": "array", "items": {"type": "object", "properties": {}}}},
           "anyOf": [{"type": "object", "properties": {}}],   # nhánh LIST: `required`, `anyOf`, `enum` đều là mảng
           "$schema": "http://x", "format": "email"}
    ra = strict_schema(goc)
    assert ra["additionalProperties"] is False
    assert ra["properties"]["a"]["additionalProperties"] is False
    assert ra["properties"]["ds"]["items"]["additionalProperties"] is False
    assert ra["anyOf"][0]["additionalProperties"] is False, "object nằm trong mảng cũng phải bị đóng"
    assert "$schema" not in ra and "format" not in ra
    assert "$schema" in goc, "phải trả BẢN SAO — sửa schema gốc là sửa của người gọi"


def test_strict_schema_them_properties_rong_cho_object_thieu():
    assert strict_schema({"type": "object"})["properties"] == {}


def test_tiers_theo_thu_tu_dat_den_re():
    assert TIERS == ("strong", "standard", "light")


# ---------- reported_model: hàm hợp nhất ----------

def test_reported_model_khop_ten_hai_chieu_tien_to():
    assert reported_model({"claude-opus-5-20260101": {}}, "claude-opus-5") == "claude-opus-5-20260101"
    assert reported_model({"claude-opus-5": {}}, "claude-opus-5-20260101") == "claude-opus-5"


def test_reported_model_khop_qua_canonical_model():
    """Nhánh của STUDIO: CLI quy đổi alias sang model thật, khoá là alias nên so tiền tố không ra.

    Khoá thứ hai tiêu NHIỀU output token hơn là cố ý: không có nó thì fallback của company vô tình trả đúng cùng
    một khoá, và ca này xanh cả khi nhánh `canonicalModel` bị xoá — đo hai chiều bắt được đúng chỗ đó."""
    usage = {"alias-nhanh": {"canonicalModel": "claude-opus-5-20260101", "outputTokens": 5},
             "model-phu": {"outputTokens": 900}}
    assert reported_model(usage, "claude-opus-5") == "alias-nhanh"


def test_reported_model_khong_khop_thi_lay_khoa_ton_nhieu_output_nhat():
    """Nhánh của COMPANY: `claude -p` liệt kê cả model phụ CLI tự gọi (Haiku), thường đứng TRƯỚC model chính.
    Studio thiếu nhánh này nên trả chuỗi rỗng — rỗng đi thẳng vào audit như thể CLI không báo model nào."""
    usage = {"claude-haiku-4-5": {"outputTokens": 40}, "model-chinh": {"outputTokens": 900}}
    assert reported_model(usage, "gpt-5") == "model-chinh"


def test_reported_model_usage_rong_thi_tra_ten_da_yeu_cau():
    assert reported_model({}, "claude-opus-5") == "claude-opus-5"


def test_reported_model_gia_tri_khong_phai_dict_khong_lam_no():
    assert reported_model({"a": "không phải dict", "b": {"outputTokens": 3}}, "x") == "b"


# ---------- adapter CLI ----------

def test_argv_limit_du_cho_windows_va_posix():
    assert ARGV_LIMIT in (30_000, 120_000)


@pytest.mark.parametrize("muc", CLAUDE_EFFORT)
def test_cli_effort_args_nhan_moi_muc_trong_bang(muc):
    assert cli_effort_args({"strong": muc}, "strong") == ["--effort", muc]


def test_cli_effort_args_khong_khai_tier_thi_khong_them_co():
    assert cli_effort_args({"strong": "high"}, "light") == []


def test_cli_effort_args_muc_la_thi_hong_to_chu_khong_ve_mac_dinh():
    with pytest.raises(LLMError, match="không hợp lệ"):
        cli_effort_args({"strong": "none"}, "strong")


def test_codex_effort_quy_doi_max_ve_xhigh():
    assert CODEX_EFFORT["max"] == "xhigh" and CODEX_EFFORT["none"] == "none"


def test_cli_subtype_errors_noi_ro_phai_tang_gi():
    assert "hết lượt" in CLI_SUBTYPE_ERRORS["error_max_turns"]
    assert set(CLI_SUBTYPE_ERRORS) >= {"error_max_turns", "error_max_budget_usd", "error_during_execution"}


def test_system_prompt_args_ghi_file_tam_roi_xoa():
    with system_prompt_args("SYSTEM PROMPT dài") as args:
        assert args[0] == "--system-prompt-file"
        p = Path(args[1])
        assert p.read_text(encoding="utf-8") == "SYSTEM PROMPT dài"
    assert not p.exists()


def test_system_prompt_args_xoa_file_ke_ca_khi_than_with_nem():
    """File tạm chứa system prompt của agent; rò một file mỗi lần lỗi là rò cả prompt lẫn inode."""
    with pytest.raises(ValueError):
        with system_prompt_args("x") as args:
            p = Path(args[1])
            raise ValueError("lỗi giữa chừng")
    assert not p.exists()


def test_find_codex_binary_uu_tien_path(monkeypatch):
    monkeypatch.setattr("xagents_core.llm.shutil.which", lambda b: "/usr/bin/codex")
    assert find_codex_binary() == "/usr/bin/codex"


def test_find_codex_binary_khong_co_path_khong_co_localappdata_thi_tra_lai_ten(monkeypatch):
    monkeypatch.setattr("xagents_core.llm.shutil.which", lambda b: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert find_codex_binary("codex") == "codex"


def test_find_codex_binary_tim_ban_kem_app_tren_windows(monkeypatch, tmp_path):
    """Windows: app Codex cài binary vào %LOCALAPPDATA% chứ không lên PATH. Nhiều bản thì lấy bản MỚI NHẤT."""
    monkeypatch.setattr("xagents_core.llm.shutil.which", lambda b: None)
    for ver, mtime in (("0.1", 1_000_000), ("0.2", 2_000_000)):
        d = tmp_path / "OpenAI" / "Codex" / "bin" / ver
        d.mkdir(parents=True)
        exe = d / "codex.exe"
        exe.write_text("", encoding="utf-8")
        os_utime(exe, mtime)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert find_codex_binary("codex").endswith(str(Path("0.2") / "codex.exe"))


def os_utime(p: Path, t: int) -> None:
    import os
    os.utime(p, (t, t))


def test_pham_vi_bon_adapter_chua_len_core():
    """Chốt phạm vi, cập nhật theo từng bước của K3.3.

    K3.3a đặt ca này để canh `Completion.json()` chưa lên core; K3.3c1 đã mang nó lên **kèm bằng chứng eval
    replay hai công ty giống hệt bản trước**, nên nửa đó của ca đổi chiều. Nửa còn lại vẫn đứng: bốn adapter
    chưa lên, và chúng là phần lệch nhất (`ClaudeCodeClient` 0.32, `OpenAICompatClient` 0.28). Ai mang chúng
    sang thì phải đổi ca này, và đổi nó là lúc phải hỏi "eval replay đâu?".
    """
    import xagents_core.llm as m

    assert hasattr(m, "Completion"), "K3.3c1 đã mang Completion lên core"
    for ten in ("AnthropicClient", "CodexClient", "FakeClient"):
        assert hasattr(m, ten), f"K3.3c2 đã mang {ten} lên core (eval replay 78 ca giống hệt bản trước)"
    assert hasattr(m, "OpenAICompatClient"), "K3.3c3 bước 1 (difflib 0.73, company là tập cha)"
    # K3.3c3 bước 2: `ClaudeCodeClient` lên core dưới dạng LỚP CƠ SỞ chỉ có transport. `complete()` cố ý ở lại
    # mỗi công ty — ba chiến lược tool khác nhau thật (studio web / company cli / company mcp), gộp lại cần năm
    # móc để giấu một khác biệt có thật, đúng thứ `tools.py` đã từ chối làm cho `tools_prompt`.
    assert hasattr(m, "ClaudeCodeClient")
    assert "complete" not in vars(m.ClaudeCodeClient), (
        "core KHÔNG được có `complete()` mặc định: một bản 'không tool' sẽ im lặng nuốt mất chiến lược tool của "
        "bên nào quên ghi đè. Ai thêm nó vào thì phải trả lời: nó thay thế được cả ba chiến lược, hay chỉ đang "
        "giấu một trong ba?")
    _bo_qua = (
        "Cầu MCP (`_toolbox`, `bind_toolbox`, `_complete_mcp`, ADR-0024) ở LẠI company: studio không có khái "
        "niệm tương đương, đưa lên core là bắt một bên mang 255 dòng `mcp_bridge.py` nó không bao giờ gọi.")
    assert not hasattr(m, "_complete_mcp") and not hasattr(m.ClaudeCodeClient, "bind_toolbox"), _bo_qua


def test_find_codex_binary_localappdata_co_nhung_khong_co_ban_nao(monkeypatch, tmp_path):
    """%LOCALAPPDATA% tồn tại mà KHÔNG có `codex.exe` nào → trả lại tên, không nổ vì `cands[0]` trên list rỗng.

    Đây là trạng thái của gần như mọi máy Windows chưa cài app Codex — tức là nhánh THƯỜNG GẶP hơn nhánh
    tìm thấy, chứ không phải ca hiếm."""
    monkeypatch.setattr("xagents_core.llm.shutil.which", lambda b: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))     # thư mục có thật, nhưng rỗng

    assert find_codex_binary("codex") == "codex"
