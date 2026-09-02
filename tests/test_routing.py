"""ADR-0006: điều phối nhiều gói tài khoản (RoutingClient), tier `light`."""
from pathlib import Path

import pytest

from studio.llm import TIERS, Completion, LLMConfig, LLMError, Refused, load_config, make_client
from studio.routing import Backend, RoutingClient, is_quota_error, is_transient_error, retry_after_seconds


class _Client:
    def __init__(self, name: str, fail: list[BaseException] | None = None):
        self.name, self.fail, self.calls = name, list(fail or []), []

    def complete(self, *, system, user, schema, model_tier, cache_key=None):
        self.calls.append(model_tier)
        if self.fail: raise self.fail.pop(0)
        return Completion(text="{}", input_tokens=10, output_tokens=1, model=f"{self.name}-{model_tier}")


def _router(*backends, **kw):
    t = {"now": 1000.0}
    r = RoutingClient(list(backends), clock=lambda: t["now"], **kw)
    r._t = t  # type: ignore[attr-defined]
    return r


def _call(r, tier="standard"):
    return r.complete(system="s", user="u", schema={"type": "object"}, model_tier=tier)


def test_quota_error_rotates_and_rests_backend_until_retry_after():
    a, b = _Client("a", [LLMError("HTTP 429: mọi tài khoản đều cooldown, thử lại sau 120s")]), _Client("b")
    r = _router(Backend("a", a), Backend("b", b))
    assert _call(r).model == "b-standard" and a.calls == ["standard"]
    st = {s["name"]: s for s in r.status()}
    assert not st["a"]["ready"] and st["a"]["cooldown_remaining"] == 120 and st["b"]["ready"]
    notes = r.drain_retries()
    assert any("hết quota" in n and "120s" in n for n in notes) and any("đi backend b" in n for n in notes)
    _call(r); assert a.calls == ["standard"]
    r._t["now"] += 121
    _call(r); assert a.calls == ["standard", "standard"]


def test_transient_vs_content_vs_refusal():
    a, b = _Client("a", [LLMError("lỗi mạng: timeout")]), _Client("b")
    r = _router(Backend("a", a), Backend("b", b), transient_cooldown_s=30, cooldown_s=3600)
    assert _call(r).model == "b-standard" and r.status()[0]["cooldown_remaining"] == 30
    with pytest.raises(LLMError, match="JSON"):
        _call(_router(Backend("x", _Client("x", [LLMError("đầu ra không phải JSON")])), Backend("y", _Client("y"))))
    with pytest.raises(Refused):
        _call(_router(Backend("x", _Client("x", [Refused("model từ chối")])), Backend("y", _Client("y"))))


def test_prefer_per_tier_and_all_resting():
    sub, free = _Client("claude-sub"), _Client("antigravity")
    r = _router(Backend("claude-sub", sub), Backend("antigravity", free), prefer={"light": "antigravity"})
    assert _call(r, "strong").model == "claude-sub-strong" and _call(r, "light").model == "antigravity-light"
    a = _Client("a", [LLMError("lỗi mạng: timeout")]); b = _Client("b", [LLMError("402 insufficient quota")])
    r2 = _router(Backend("a", a), Backend("b", b), cooldown_s=600, transient_cooldown_s=45)
    with pytest.raises(LLMError, match="thử lại sau 45s"): _call(r2)   # a nghỉ 45s (mạng), b nghỉ 600s (quota)
    assert [s["cooldown_remaining"] for s in r2.status()] == [45, 600]


def test_router_validation_and_classifiers():
    with pytest.raises(LLMError, match="backend"): RoutingClient([])
    with pytest.raises(LLMError, match="trùng"): RoutingClient([Backend("a", _Client("a")), Backend("a", _Client("a"))])
    with pytest.raises(LLMError, match="prefer"): RoutingClient([Backend("a", _Client("a"))], prefer={"strong": "zzz"})
    assert is_quota_error(LLMError("RESOURCE_EXHAUSTED")) and is_quota_error(LLMError("You've hit your limit"))
    assert is_transient_error(LLMError("claude -p quá 900s")) and is_transient_error(LLMError("HTTP 502: bad gateway"))
    assert not is_quota_error(LLMError("đầu ra không phải JSON")) and not is_transient_error(LLMError("đầu ra không phải JSON"))
    assert retry_after_seconds("Retry-After: 30") == 30 and retry_after_seconds("no hint") is None


def test_light_tier_fallback_and_backends_config(tmp_path: Path, monkeypatch):
    assert TIERS == ("strong", "standard", "light")
    assert LLMConfig(models={"strong": "big", "standard": "mid"}).model_for("light") == "mid"
    assert LLMConfig(models={"strong": "big"}).model_for("light") == "big"
    p = tmp_path / "llm.yaml"
    p.write_text("""
provider: fake
backends:
  - name: claude-sub
    provider: fake
    models: {strong: claude-opus-5, standard: claude-sonnet-5}
  - name: antigravity
    provider: fake
    base_url: http://127.0.0.1:8100/v1
    api_key: gateway-local
    models: {strong: claude-sonnet-4-6, standard: gemini-3.7-flash, light: gemini-3.7-flash-low}
routing: {cooldown_s: 1200, transient_cooldown_s: 15, prefer: {light: antigravity}}
""", encoding="utf-8")
    monkeypatch.delenv("STUDIO_LLM_BACKENDS", raising=False)
    cfg = load_config(p)
    free = cfg.backend_config(cfg.backends[1])
    assert free.name == "antigravity" and free.api_key == "gateway-local" and free.tiers_configured() == {"strong", "standard", "light"}
    assert cfg.backend_config(cfg.backends[0]).model_for("light") == "claude-sonnet-5"
    client = make_client(cfg)
    assert isinstance(client, RoutingClient) and client.cooldown_s == 1200 and client.prefer == {"light": "antigravity"}
    client.backends[1].client.responses.append({"ok": True})
    assert client.complete(system="s", user="u", schema={}, model_tier="light").model == "fake-light"
    assert client.backends[0].calls == 0 and client.backends[1].calls == 1
    monkeypatch.setenv("STUDIO_LLM_BACKENDS", "antigravity")
    assert [b["name"] for b in load_config(p).backends] == ["antigravity"]
    monkeypatch.setenv("STUDIO_LLM_BACKENDS", "nope")
    with pytest.raises(LLMError, match="nope"): load_config(p)
