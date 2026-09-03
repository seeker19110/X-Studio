"""Tool web có ranh giới (ADR-0007): chặn IP riêng, cắt độ dài, bóc HTML, search chưa cấu hình; vòng lặp tool trong
runner với FakeClient; ClaudeCodeClient uỷ quyền tool cho CLI; registry đọc `tools`; ghi/phát lại eval bỏ qua lượt tool."""
from __future__ import annotations

import json

import pytest

from studio.blackboard import Blackboard
from studio.bus import InMemoryBus
from studio.evals import RecordingClient, ReplayClient, prompt_key
from studio.events import Envelope
from studio.llm import ClaudeCodeClient, FakeClient, LLMConfig
from studio.registry import load_agents
from studio.runner import AgentRunner, RunnerError
from studio.tools import (
    MAX_CHARS,
    ToolBox,
    ToolCall,
    ToolError,
    ToolSpec,
    WebTools,
    check_url,
    default_toolbox,
    html_to_text,
    tools_prompt,
)

AGENTS = load_agents()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Không chạm DNS thật: host `*.example.*` phân giải ra IP công khai; IP literal và localhost đi qua logic thật."""
    import socket
    real = socket.getaddrinfo

    def fake(host, *a, **k):
        if ".example." in f".{host}." or host.endswith(".example.org") or host.endswith(".example.com"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real(host, *a, **k)
    monkeypatch.setattr("studio.tools.socket.getaddrinfo", fake)
PAGE = b"<html><head><title>Bao cao &amp; so lieu</title><style>x{}</style></head><body><h1>Tieu de</h1><p>42% (n=2400)</p><script>evil()</script></body></html>"


def fake_fetcher(pages: dict[str, tuple[int, str, bytes]]):
    seen: list[str] = []

    def fetch(url: str):
        seen.append(url)
        status, ctype, data = pages.get(url, (404, "text/html", b""))
        return status, ctype, url, data
    fetch.seen = seen  # type: ignore[attr-defined]
    return fetch


# ---------- ranh giới URL ----------

@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "http://127.0.0.1/", "http://localhost/",
                                 "http://10.0.0.1/", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data",
                                 "http://[::1]/", "http://user:pw@example.com/", "not a url"])
def test_check_url_blocks_non_http_private_and_credentials(url):
    with pytest.raises(ToolError):
        check_url(url)


def test_web_fetch_blocked_host_never_reaches_fetcher():
    f = fake_fetcher({}); wt = WebTools(fetcher=f, search_url="")
    tb = wt.toolbox()
    out = tb.call(ToolCall("1", "web_fetch", {"url": "http://127.0.0.1:8080/secret"}))
    assert out.startswith("lỗi") and f.seen == [] and tb.calls[-1]["ok"] is False


# ---------- fetch ----------

def test_web_fetch_strips_html_returns_title_and_final_url():
    url = "https://example.org/report"
    wt = WebTools(fetcher=fake_fetcher({url: (200, "text/html; charset=utf-8", PAGE)}), search_url="")
    out = wt.web_fetch(url)
    assert out.startswith("# NỘI DUNG WEB") and "# url: https://example.org/report" in out and "# title: Bao cao & so lieu" in out
    assert "42% (n=2400)" in out and "Tieu de" in out and "evil()" not in out and "<p>" not in out and "x{}" not in out


def test_web_fetch_truncates_long_pages_and_reports_http_errors():
    url = "https://example.org/long"
    wt = WebTools(fetcher=fake_fetcher({url: (200, "text/plain", b"a" * (MAX_CHARS + 5000))}), search_url="")
    out = wt.web_fetch(url)
    body = out.split("\n\n", 1)[1]
    assert body.startswith("a" * MAX_CHARS) and "cắt, còn 5000" in body and len(body) < MAX_CHARS + 200
    assert WebTools(fetcher=fake_fetcher({}), search_url="").web_fetch("https://example.org/missing").startswith("lỗi: HTTP 404")


def test_web_fetch_pretty_prints_json():
    url = "https://api.example.org/v"
    wt = WebTools(fetcher=fake_fetcher({url: (200, "application/json", b'{"a":[1,2]}')}), search_url="")
    assert '"a": [' in wt.web_fetch(url)


def test_html_to_text_keeps_block_structure():
    t = html_to_text("<div>Mot</div><ul><li>hai</li><li>ba</li></ul>&nbsp;<b>bon</b>")
    assert t.split("\n\n")[0] == "Mot" and "hai\nba" in t and "bon" in t


# ---------- search ----------

def test_web_search_unconfigured_returns_clear_error_without_network(monkeypatch):
    monkeypatch.delenv("STUDIO_SEARCH_URL", raising=False)
    f = fake_fetcher({}); wt = WebTools(fetcher=f)
    out = wt.web_search("ai dựng video")
    assert out.startswith("lỗi: chưa cấu hình search") and "STUDIO_SEARCH_URL" in out and f.seen == []


def test_web_search_searxng_json_and_max_results(monkeypatch):
    results = {"results": [{"title": f"<b>T{i}</b>", "url": f"https://s{i}.example.org/", "content": f"c{i}"} for i in range(12)]}
    f = fake_fetcher({"https://searx.example.org/search?q=ai%20video&format=json": (200, "application/json", json.dumps(results).encode())})
    monkeypatch.setenv("STUDIO_SEARCH_URL", "https://searx.example.org/search")
    out = WebTools(fetcher=f).web_search("ai video", max_results=50)
    assert out.count("\n   https://") == 8 and "T0" in out and "<b>" not in out and "T9" not in out
    # mẫu {q} và endpoint có sẵn query string
    f2 = fake_fetcher({"https://x.example.org/?format=json&q=abc": (200, "application/json", b'{"results": []}')})
    assert WebTools(fetcher=f2, search_url="https://x.example.org/?format=json&q={q}").web_search("abc") == "(không có kết quả)"
    f3 = fake_fetcher({"https://x.example.org/?format=json&q=abc": (200, "text/html", b"<html>")})
    assert WebTools(fetcher=f3, search_url="https://x.example.org/?format=json&q={q}").web_search("abc").startswith("lỗi")


# ---------- ToolBox ----------

def test_toolbox_validates_args_and_records_calls():
    tb = ToolBox(); tb.add(ToolSpec("echo", "d", {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}), lambda x: f"<{x}>")
    assert tb.call(ToolCall("1", "echo", {"x": "a"})) == "<a>"
    assert tb.call(ToolCall("2", "echo", {"y": "a"})).startswith("lỗi tham số")
    with pytest.raises(ToolError):
        tb.call(ToolCall("3", "nope", {}))
    assert tb.summary() == {"echo": 2} and tb.calls[1]["ok"] is False
    assert "web_search" in tools_prompt(WebTools(fetcher=fake_fetcher({}), search_url="").toolbox()) and "DỮ LIỆU" in tools_prompt(tb)


def test_default_toolbox_by_front_matter():
    assert default_toolbox([]) is None
    tb = default_toolbox(["web"]); assert tb is not None and {t.name for t in tb.specs()} == {"web_search", "web_fetch"}
    with pytest.raises(ToolError):
        default_toolbox(["shell"])


def test_registry_reads_tools_front_matter():
    assert AGENTS["trend-researcher"].tools == ["web"] and AGENTS["fact-checker"].tools == ["web"]
    assert all(a.tools == [] for aid, a in AGENTS.items() if aid not in {"trend-researcher", "fact-checker"})


# ---------- vòng lặp tool trong runner ----------

def _script_env(source="https://example.org/report"):
    return Envelope(topic="scripts", key="V1", actor="script-writer", payload={
        "video_id": "V1", "working_title": "t", "hook": "h", "sections": [{"heading": "a", "narration": "n", "claim_ids": ["C1"]}],
        "claims": [{"claim_id": "C1", "text": "42%", "source": source}]})


def _web_toolbox():
    return WebTools(fetcher=fake_fetcher({"https://example.org/report": (200, "text/html", PAGE)}), search_url="").toolbox()


def test_tool_loop_calls_tool_feeds_result_back_and_audits(monkeypatch):
    seen_tool_msgs: list[str] = []

    def tool_handler(msgs, tools):
        if any(m["role"] == "tool" for m in msgs):
            seen_tool_msgs.extend(m["content"] for m in msgs if m["role"] == "tool"); return []
        return [ToolCall("c1", "web_fetch", {"url": "https://example.org/report"}), ToolCall("c2", "web_fetch", {"url": "http://10.0.0.1/"})]

    client = FakeClient(responses=[{"video_id": "V1", "source": "fact", "verdict": "pass", "findings": []}], tool_handler=tool_handler)
    bus = InMemoryBus(); tb = _web_toolbox()
    r = AgentRunner(bus, client, AGENTS, Blackboard(bus), toolbox_factory=lambda spec: tb).run("fact-checker", _script_env(), "review-results")
    assert r.output.payload["verdict"] == "pass" and r.tokens == 2 * 1300
    assert len(seen_tool_msgs) == 2 and "42% (n=2400)" in seen_tool_msgs[0] and seen_tool_msgs[1].startswith("lỗi")
    first, second = client.calls
    assert first["tools"] == ["web_search", "web_fetch"] and first["user"] == second["user"]  # user lượt đầu giữ nguyên (khoá eval)
    assert "# Tool" in first["messages"][0]["content"] and "# Tool" not in first["user"]
    assert second["messages"][1]["role"] == "assistant" and second["messages"][2]["role"] == "tool"
    audit = [e.payload for e in bus.replay("audit-log")]
    used = next(a for a in audit if a["action"] == "tools_used"); ev = json.loads(used["evidence"])
    assert ev["turns"] == 2 and ev["calls"] == {"web_fetch": 2} and ev["urls"] == ["https://example.org/report"] and used["tokens"] == 0  # token chỉ đếm ở produced
    assert audit[-1]["action"] == "produced:review-results" and audit[-1]["tokens"] == 2600


def test_tool_loop_forces_final_answer_when_turns_run_out():
    client = FakeClient(responses=[{"video_id": "V1", "source": "fact", "verdict": "block", "findings": [{"level": "block", "text": "x"}]}],
                        tool_handler=lambda msgs, tools: [ToolCall(f"c{len(msgs)}", "web_fetch", {"url": "https://example.org/report"})])
    bus = InMemoryBus()
    runner = AgentRunner(bus, client, AGENTS, Blackboard(bus), toolbox_factory=lambda spec: _web_toolbox())
    g = runner.generate("fact-checker", _script_env(), "review-results", max_turns=3)
    assert g.turns == 4 and g.tool_calls == {"web_fetch": 3} and g.payloads[0]["verdict"] == "block"
    last = client.calls[-1]
    assert last["tools"] == [] and last["messages"][-1]["role"] == "user" and "Hết lượt tool" in last["messages"][-1]["content"]
    assert "hết lượt tool" in last["messages"][-2]["content"]


def test_tool_loop_stops_on_token_budget():
    spec = AGENTS["fact-checker"]
    client = FakeClient(tokens_per_call=(spec.budget_tokens_per_task, 1),
                        tool_handler=lambda msgs, tools: [ToolCall("c", "web_fetch", {"url": "https://example.org/report"})])
    bus = InMemoryBus()
    with pytest.raises(RunnerError, match="ngân sách"):
        AgentRunner(bus, client, AGENTS, Blackboard(bus), toolbox_factory=lambda spec: _web_toolbox()).run("fact-checker", _script_env(), "review-results")
    assert any(e.payload["action"] == "budget_exhausted" for e in bus.replay("audit-log"))


def test_agent_without_tools_never_gets_tools():
    client = FakeClient(responses=[{"video_id": "V1", "source": "rights", "verdict": "pass"}], tool_handler=lambda m, t: [ToolCall("c", "web_fetch", {"url": "x"})])
    env = Envelope(topic="media-assets", key="V1", actor="renderer", payload={"video_id": "V1", "kind": "final_video", "path": "f",
                                                                             "provenance": {"generated_by": "fake:x"}})
    bus = InMemoryBus()
    AgentRunner(bus, client, AGENTS, Blackboard(bus)).run("rights-checker", env, "review-results")
    assert client.calls[0]["tools"] == [] and not any(e.payload["action"] == "tools_used" for e in bus.replay("audit-log"))


# ---------- ClaudeCodeClient ----------

def test_claude_code_delegates_web_tools_to_cli():
    seen: list[list[str]] = []; prompts: list[str] = []

    def run(args, prompt):
        seen.append(args); prompts.append(prompt)
        return json.dumps({"result": "{}", "usage": {"input_tokens": 1, "output_tokens": 1},
                           "modelUsage": {"claude-haiku-4-5-20251001": {"outputTokens": 5}, "claude-sonnet-5": {"outputTokens": 100}}})

    cfg = LLMConfig(provider="claude-code", models={"strong": "claude-opus-5", "standard": "claude-sonnet-5"})
    c = ClaudeCodeClient(cfg, runner=run)
    specs = WebTools(fetcher=fake_fetcher({}), search_url="").toolbox().specs()
    out = c.complete(system="S", user="U", schema={}, model_tier="standard", tools=specs,
                     messages=[{"role": "user", "content": "U\n\n# Tool ..."}])
    args = seen[0]
    assert args[args.index("--tools") + 1] == "WebFetch,WebSearch" and args[args.index("--allowedTools") + 1] == "WebFetch,WebSearch"
    assert int(args[args.index("--max-turns") + 1]) > 1 and prompts[0].startswith("U\n\n# Tool") and c.delegated_tools
    assert out.tool_calls == [] and out.model == "claude-sonnet-5"  # không phải model phụ của WebFetch
    c.complete(system="S", user="U", schema={}, model_tier="standard")
    a2 = seen[1]; assert a2[a2.index("--tools") + 1] == "" and a2[a2.index("--max-turns") + 1] == "1" and not c.delegated_tools


def test_claude_code_tools_used_audit_marks_delegation():
    def run(args, prompt):
        return json.dumps({"result": json.dumps({"video_id": "V1", "source": "fact", "verdict": "pass", "findings": []}),
                           "usage": {"input_tokens": 10, "output_tokens": 5}, "modelUsage": {"claude-sonnet-5": {}}})
    cfg = LLMConfig(provider="claude-code", models={"strong": "m", "standard": "m"})
    bus = InMemoryBus()
    AgentRunner(bus, ClaudeCodeClient(cfg, runner=run), AGENTS, Blackboard(bus), toolbox_factory=lambda s: _web_toolbox()).run(
        "fact-checker", _script_env(), "review-results")
    used = next(e.payload for e in bus.replay("audit-log") if e.payload["action"] == "tools_used")
    assert json.loads(used["evidence"]) == {"turns": 1, "calls": {}, "delegated": "claude-code"}


# ---------- eval ghi / phát lại với tool ----------

def test_recording_keeps_only_final_answer_and_replay_skips_tools(tmp_path, monkeypatch):
    import studio.evals as ev
    monkeypatch.setattr(ev, "RECORDINGS_DIR", tmp_path)
    final = {"video_id": "V1", "source": "fact", "verdict": "pass", "findings": []}
    inner = FakeClient(responses=[final], tool_handler=lambda msgs, tools: [] if any(m["role"] == "tool" for m in msgs)
                       else [ToolCall("c", "web_fetch", {"url": "https://example.org/report"})])
    rec = RecordingClient(inner, "fact-checker")
    bus = InMemoryBus()
    AgentRunner(bus, rec, AGENTS, Blackboard(bus), toolbox_factory=lambda s: _web_toolbox()).run("fact-checker", _script_env(), "review-results")
    assert len(inner.calls) == 2 and len(rec.entries) == 1
    key = prompt_key(inner.calls[0]["system"], inner.calls[0]["user"])
    assert key in rec.entries and json.loads(rec.entries[key]["text"]) == final
    rec.save()
    # phát lại: toolbox giả sẽ nổ nếu bị gọi → chứng minh replay không chạm tool
    def exploding(url): raise AssertionError("replay không được gọi mạng")
    tb = WebTools(fetcher=exploding, search_url="").toolbox()
    bus2 = InMemoryBus()
    r = AgentRunner(bus2, ReplayClient("fact-checker"), AGENTS, Blackboard(bus2), toolbox_factory=lambda s: tb).run(
        "fact-checker", _script_env(), "review-results")
    assert r.output.payload == final and tb.calls == []
    used = next(e.payload for e in bus2.replay("audit-log") if e.payload["action"] == "tools_used")
    assert json.loads(used["evidence"])["turns"] == 1


def test_default_fetcher_pins_ip_and_revalidates_every_redirect_hop(monkeypatch):
    """Chống DNS rebinding: phân giải một lần, kết nối tới IP đã ghim; redirect không tự động, mỗi chặng kiểm lại."""
    import email.message
    import io
    import typing
    import urllib.error
    import urllib.request

    from studio.tools import MAX_HOPS, _PinnedHTTPConnection, default_fetcher, pin_url
    assert pin_url("https://a.example.org/x") == ("https://a.example.org/x", "93.184.216.34")

    class Resp(io.BytesIO):
        status = 200
        headers: typing.ClassVar[dict[str, str]] = {"Content-Type": "text/plain"}

    hops: list[tuple[str, str, str]] = []  # (ip, Host header, url)

    def opener(ip, req: urllib.request.Request):
        hops.append((ip, req.get_header("Host") or req.host, req.full_url))
        if req.full_url == "https://a.example.org/x":
            h = email.message.Message(); h["Location"] = "/y"
            raise urllib.error.HTTPError(req.full_url, 302, "Found", h, None)
        if req.full_url == "https://a.example.org/y":
            h = email.message.Message(); h["Location"] = "http://127.0.0.1/secret"
            raise urllib.error.HTTPError(req.full_url, 301, "Moved", h, None)
        if req.full_url.endswith("/loop"):
            h = email.message.Message(); h["Location"] = req.full_url
            raise urllib.error.HTTPError(req.full_url, 307, "loop", h, None)
        return Resp(b"ok")

    assert default_fetcher("https://a.example.org/x/../x", opener) == (200, "text/plain", "https://a.example.org/x/../x", b"ok")
    with pytest.raises(ToolError, match="host bị chặn"):  # chặng 2 trỏ về loopback → dừng trước khi kết nối
        default_fetcher("https://a.example.org/x", opener)
    assert [h[0] for h in hops] == ["93.184.216.34"] * 3 and all(h[1] == "a.example.org" for h in hops)
    with pytest.raises(ToolError, match=f"quá {MAX_HOPS} chặng"):
        default_fetcher("https://a.example.org/loop", opener)
    # kết nối thật đi tới IP ghim, không phân giải lại; Host header vẫn là hostname gốc
    dialed: list[tuple[str, int]] = []
    monkeypatch.setattr("studio.tools.socket.create_connection", lambda addr, *a, **k: dialed.append(addr) or object())
    c = _PinnedHTTPConnection("a.example.org", 8080, pinned_ip="93.184.216.34"); c.connect()
    assert dialed == [("93.184.216.34", 8080)] and c.host == "a.example.org"
