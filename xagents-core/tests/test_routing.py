"""`xagents_core.routing` — xoay backend theo gói tài khoản (K3.3d; company ADR-0019, studio ADR-0006).

Ca ở ĐÂY vì mã ở đây. Trước K3.3d mỗi công ty có một bản `routing.py` và một bộ ca riêng, nên một đột biến trong
phân loại lỗi chỉ đỏ ở một suite — đọc ra "lỗi của company" trong khi nó là lỗi của cơ chế dùng chung (bài học đã
ghi ở nhật ký phiên 2026-09-08 khi `TransientError` lên core mà ca ở lại company).

Bốn nhóm ca dưới đây là bốn điểm studio ĐƯỢC NÂNG; mỗi ca đỏ nếu ai đó gỡ điểm nâng ấy:

1. `test_ma_http_thang_hon_regex` — phân loại theo `LLMError.status` trước, regex chỉ là đường lùi cho CLI/gateway.
2. `test_quota_patterns_co_ranh_gioi_tu` — "unlimited"/"billingham"/4290 KHÔNG phải hết quota (bug thật của studio).
3. `test_loi_xac_thuc_401_403_cho_backend_nghi_thay_vi_nem_cho_agent` — `is_auth_error`, studio trước không có.
4. `test_moi_backend_deu_nghi_nem_transient_error` — `TransientError` chứ không `LLMError` trần, để orchestrator
   phân biệt được "chưa gọi được" với "model trả lời sai".
"""
from __future__ import annotations

import pytest

from xagents_core.llm import Completion, LLMError, Refused, TransientError
from xagents_core.routing import (
    Backend,
    RoutingClient,
    is_auth_error,
    is_missing_error,
    is_quota_error,
    plain,
    retry_after_seconds,
)
from xagents_core.tools import ToolSpec


class _Client:
    """Backend giả: `fail` = ngoại lệ ném ở N lần đầu; sau đó trả lời."""

    def __init__(self, name: str, fail: list[BaseException] | None = None):
        self.name, self.fail, self.calls = name, list(fail or []), []

    def complete(self, *, system, user, schema, model_tier, cache_key=None, tools=None, messages=None, workdir=None):
        self.calls.append(model_tier)
        if self.fail: raise self.fail.pop(0)
        return Completion(text="{}", input_tokens=10, output_tokens=1, model=f"{self.name}-{model_tier}")


def _router(*backends, clock=None, **kw):
    t = {"now": 1000.0}
    r = RoutingClient(list(backends), clock=clock or (lambda: t["now"]), **kw)
    r._t = t  # type: ignore[attr-defined]
    return r


def _call(r, tier="standard", tools=None):
    return r.complete(system="s", user="u", schema={"type": "object"}, model_tier=tier, tools=tools)


# ---------- xoay backend ----------

def test_quota_error_rotates_to_next_backend_and_rests_first():
    a, b = _Client("a", [TransientError("HTTP 429: quota exceeded, thử lại sau 120s")]), _Client("b")
    r = _router(Backend("a", a), Backend("b", b))
    c = _call(r)
    assert c.model == "b-standard" and a.calls == ["standard"] and b.calls == ["standard"]
    st = {s["name"]: s for s in r.status()}
    assert not st["a"]["ready"] and st["a"]["cooldown_remaining"] == 120 and st["b"]["ready"]
    notes = r.drain_retries()
    assert any("hết quota" in n and "nghỉ 120s" in n for n in notes) and any("đi backend b" in n for n in notes)
    assert r.drain_retries() == []
    # còn nghỉ → không gọi a; hết nghỉ → a lại đứng đầu
    _call(r); assert a.calls == ["standard"]
    r._t["now"] += 121
    _call(r); assert a.calls == ["standard", "standard"]


def test_transient_rest_is_short_and_content_errors_are_not_routed():
    a, b = _Client("a", [TransientError("lỗi mạng: timeout")]), _Client("b")
    r = _router(Backend("a", a), Backend("b", b), transient_cooldown_s=30, cooldown_s=3600)
    assert _call(r).model == "b-standard"
    assert r.status()[0]["cooldown_remaining"] == 30
    bad = _Client("x", [LLMError("đầu ra không phải JSON")])
    r2 = _router(Backend("x", bad), Backend("y", _Client("y")))
    with pytest.raises(LLMError, match="JSON"): _call(r2)
    ref = _Client("x", [Refused("model từ chối")])
    r3 = _router(Backend("x", ref), Backend("y", _Client("y")))
    with pytest.raises(Refused): _call(r3)


def test_prefer_per_tier_and_tools_skip_backend_without_tool_support():
    sub, free = _Client("claude-sub"), _Client("antigravity")
    r = _router(Backend("claude-sub", sub, supports_tools=False), Backend("antigravity", free),
                prefer={"light": "antigravity"})
    assert _call(r, "strong").model == "claude-sub-strong"
    assert _call(r, "light").model == "antigravity-light"
    tool = [ToolSpec(name="read_file", description="", parameters={"type": "object"})]
    assert _call(r, "strong", tools=tool).model == "antigravity-strong"
    r_only = _router(Backend("claude-sub", sub, supports_tools=False))
    with pytest.raises(LLMError, match="tool"): _call(r_only, tools=tool)


def test_missing_binary_rests_backend_for_full_cooldown():
    a = _Client("a", [LLMError("không tìm thấy `claude` (cài Claude Code hoặc đổi provider)")]); b = _Client("b")
    r = _router(Backend("a", a), Backend("b", b), cooldown_s=900)
    assert _call(r).model == "b-standard" and r.status()[0]["cooldown_remaining"] == 900


def test_router_validation():
    with pytest.raises(LLMError, match="backend"): RoutingClient([])
    with pytest.raises(LLMError, match="trùng"): RoutingClient([Backend("a", _Client("a")), Backend("a", _Client("a"))])
    with pytest.raises(LLMError, match="prefer"): RoutingClient([Backend("a", _Client("a"))], prefer={"strong": "zzz"})


def test_bind_toolbox_chuyen_tiep_cho_backend_biet_bo_qua_backend_khong_biet():
    """ADR-0024: `bind_toolbox` phải gọi tới mọi backend có phương thức đó, và không sập với backend không có (vd. codex).

    Ở core vì `RoutingClient` là chỗ chuyển tiếp; cầu MCP thật thì vẫn ở lại company (K3.3c3 bước 2)."""
    class _WithBind(_Client):
        def __init__(self, name):
            super().__init__(name)
            self.bound = None

        def bind_toolbox(self, tb):
            self.bound = tb

    a, b = _WithBind("a"), _Client("b")   # b không có bind_toolbox
    r = _router(Backend("a", a), Backend("b", b))
    r.bind_toolbox("hop-tool-gia")
    assert a.bound == "hop-tool-gia"


def test_drain_retries_gom_ca_ghi_chu_cua_backend_con():
    """`drain_retries` của backend (RetryingClient) và ghi chú xoay của router phải cùng về một chỗ: runner chỉ
    hỏi router, không biết backend nào có lớp retry riêng."""
    class _WithDrain(_Client):
        def drain_retries(self):
            return ["thử lại lần 1"]

    r = _router(Backend("a", _WithDrain("a")), Backend("b", _Client("b")))
    _call(r)
    assert r.drain_retries() == ["[a] thử lại lần 1"]


# ---------- 1. mã HTTP thẳng hơn regex ----------

def test_ma_http_thang_hon_regex():
    """Có `status` thì HỎI nó, đừng đọc chuỗi. Chuỗi "401" trong thân lỗi không làm nó thành lỗi xác thực, và
    một lỗi mang status=429 vẫn là hết quota dù thông điệp không có chữ nào khớp regex."""
    assert is_quota_error(LLMError("chuyện gì đó rất lạ", status=429))
    assert not is_quota_error(LLMError("429 nằm trong thân văn bản", status=500))
    assert is_missing_error(LLMError("gì đó", status=404)) and not is_missing_error(LLMError("not found", status=500))
    assert is_auth_error(LLMError("x", status=401)) and is_auth_error(LLMError("x", status=403))
    assert not is_auth_error(LLMError("401"))   # không mã → regex, và regex không đoán lỗi xác thực


def test_quota_patterns_co_ranh_gioi_tu():
    """Bản studio trước K3.3d khớp `insufficient` và `429` trần: "unlimited", "billingham", mã 4290 đều đọc ra
    "hết quota" và cho một backend còn tốt đi nghỉ nguyên tiếng. Ranh giới từ là bản vá."""
    for lanh in ("gói unlimited của bạn còn hiệu lực", "liên hệ billingham@example.com", "mã lỗi nội bộ 4290",
                 "limited edition model"):
        assert not is_quota_error(LLMError(lanh)), lanh
    for that in ("HTTP 429: quá nhiều yêu cầu", "RESOURCE_EXHAUSTED", "You've hit your limit · resets 3pm",
                 "insufficient quota", "billing required"):
        assert is_quota_error(LLMError(that)), that


def test_loi_xac_thuc_401_403_cho_backend_nghi_thay_vi_nem_cho_agent():
    """Khoá sai là lỗi CẤU HÌNH của backend, không phải lỗi nội dung của model. Studio trước K3.3d ném thẳng cho
    agent: một backend khai sai khoá làm hỏng từng lượt thay vì tự nghỉ ra một bên."""
    a, b = _Client("a", [LLMError("HTTP 401: invalid api key", status=401)]), _Client("b")
    r = _router(Backend("a", a), Backend("b", b), cooldown_s=1800, transient_cooldown_s=60)
    assert _call(r).model == "b-standard"
    assert r.status()[0]["cooldown_remaining"] == 1800 and "xác thực" in r.status()[0]["reason"]


def test_moi_backend_deu_nghi_nem_transient_error():
    """`TransientError`, không phải `LLMError` trần: orchestrator phân biệt được "chưa gọi được" (hoãn, nhịp sau
    thử lại) với "model trả lời sai" (lỗi agent, tính retry). Nó vẫn là con của `LLMError` nên chỗ nào đang
    `except LLMError` không phải sửa."""
    a = _Client("a", [TransientError("lỗi mạng: connection reset")])
    b = _Client("b", [LLMError("HTTP 402: insufficient quota")])
    r = _router(Backend("a", a), Backend("b", b), cooldown_s=600, transient_cooldown_s=45)
    with pytest.raises(TransientError, match="thử lại sau 45s"): _call(r)   # a nghỉ 45s (mạng), b nghỉ 600s (quota)
    assert all(not s["ready"] for s in r.status()) and [s["failures"] for s in r.status()] == [1, 1]
    with pytest.raises(LLMError): _call(r)   # vẫn bắt được bằng lớp cha


# ---------- phân loại chuỗi (đường lùi cho CLI/gateway không mã) ----------

def test_retry_after_va_giai_ma_tieng_viet_bi_escape():
    assert retry_after_seconds("mọi tài khoản đều cooldown, thử lại sau 77s") == 77
    assert retry_after_seconds("Mọi tài khoản Antigravity đều đang cooldown hoặc hết hạn. Thử lại sau khoảng 77s.") == 77
    assert retry_after_seconds("Retry-After: 30") == 30 and retry_after_seconds("resets in 12s") == 12
    assert retry_after_seconds("no hint") is None
    # thân lỗi HTTP escape tiếng Việt: không giải mã thì mẫu tiếng Việt trượt hết
    assert plain(r'{"error": "Chưa có tài khoản"}') == '{"error": "Chưa có tài khoản"}'
    assert is_missing_error(LLMError(r'{"detail": "pool trống"}'))


def test_hen_gio_cua_provider_thang_cooldown_mac_dinh():
    """Provider nói "thử lại sau 120s" thì nghỉ 120s, không phải `cooldown_s` mặc định — hỏi lại sớm hơn thì phí,
    nghỉ lâu hơn thì mất một gói còn dùng được."""
    a = _Client("a", [LLMError("HTTP 429: thử lại sau 120s")])
    r = _router(Backend("a", a), Backend("b", _Client("b")), cooldown_s=3600)
    _call(r)
    assert r.status()[0]["cooldown_remaining"] == 120


def test_ghi_chu_xoay_backend_theo_thread():
    """`notes` là thread-local: với `--workers>1` audit `llm_retry` của agent nào phải là của agent đó, không phải
    ghi chú của lượt chạy song song bên cạnh."""
    import threading

    r = _router(Backend("a", _Client("a")))
    r.notes.append("của thread chính")
    thay: list[list[str]] = []
    t = threading.Thread(target=lambda: thay.append(list(r.notes)))
    t.start(); t.join()
    assert thay == [[]] and r.notes == ["của thread chính"]


def test_backend_ready_va_status_phan_anh_dong_ho_tiem_vao():
    b = Backend("a", _Client("a"))
    assert b.ready(1000.0)
    b.cooldown_until = 1100.0
    assert not b.ready(1000.0) and b.ready(1100.0)
