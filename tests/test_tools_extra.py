"""Nhánh còn thiếu của tools.py: _resolve() khi DNS không phân giải được, _blocked_host(), _PinnedHTTPSConnection.connect(),
_open_pinned() (HTTPHandler thật qua server loopback thật), default_fetcher khi lỗi mạng, ToolBox.call khi tool ném
TypeError/ValueError, WebTools.web_fetch khi content-type json nhưng body không phải JSON hợp lệ."""

from __future__ import annotations

import http.server
import socket
import threading

import pytest

from studio.tools import (
    ToolBox,
    ToolCall,
    ToolError,
    ToolSpec,
    WebTools,
    _blocked_host,
    _open_pinned,
    _PinnedHTTPSConnection,
    _resolve,
    default_fetcher,
)


def test_resolve_returns_empty_list_on_gaierror(monkeypatch):
    import socket as socket_mod

    def raise_gaierror(host, *a, **k):
        raise socket_mod.gaierror("khong phan giai duoc")

    monkeypatch.setattr("studio.tools.socket.getaddrinfo", raise_gaierror)
    assert _resolve("khong-ton-tai.invalid") == []


def test_blocked_host_true_when_unresolvable_or_private(monkeypatch):
    monkeypatch.setattr("studio.tools.socket.getaddrinfo", lambda host, *a, **k: (_ for _ in ()).throw(socket.gaierror()))
    assert _blocked_host("khong-ton-tai.invalid") is True

    def fake(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr("studio.tools.socket.getaddrinfo", fake)
    assert _blocked_host("private.example") is True


def test_pinned_https_connection_connect_wraps_socket_with_sni(monkeypatch):
    import ssl as ssl_mod

    dialed = []
    wrapped = {}

    class _FakeSock:
        pass

    def fake_create_connection(addr, timeout):
        dialed.append(addr)
        return _FakeSock()

    ctx = ssl_mod.create_default_context()
    orig_wrap_socket = ctx.wrap_socket

    def spy_wrap_socket(sock, server_hostname=None, **kw):
        wrapped["hostname"] = server_hostname
        wrapped["sock"] = sock
        return "wrapped-sock"

    monkeypatch.setattr(ctx, "wrap_socket", spy_wrap_socket)
    monkeypatch.setattr("studio.tools.socket.create_connection", fake_create_connection)
    c = _PinnedHTTPSConnection("a.example.org", 443, pinned_ip="93.184.216.34", context=ctx)
    c.connect()
    assert dialed == [("93.184.216.34", 443)]
    assert wrapped["hostname"] == "a.example.org"
    assert c.sock == "wrapped-sock"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"pinned ok")

    def log_message(self, *a):
        pass


def test_open_pinned_connects_to_pinned_ip_via_real_loopback_server():
    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        import urllib.request

        req = urllib.request.Request(f"http://a.example.org:{port}/", headers={"Host": f"a.example.org:{port}"})
        # kết nối thẳng tới 127.0.0.1 (IP đã "ghim" giả lập) dù URL mang hostname khác — xác nhận _open_pinned dùng đúng IP truyền vào
        with _open_pinned("127.0.0.1", req) as r:
            assert r.status == 200
            assert r.read() == b"pinned ok"
    finally:
        srv.shutdown()
        srv.server_close()


def test_default_fetcher_wraps_network_error_as_tool_error(monkeypatch):
    import urllib.error

    def fake(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("studio.tools.socket.getaddrinfo", fake)

    def raise_url_error(ip, req):
        raise urllib.error.URLError("mat mang")

    with pytest.raises(ToolError, match="không lấy được"):
        default_fetcher("https://a.example.org/x", raise_url_error)


def test_toolbox_call_wraps_typeerror_and_valueerror_from_tool_fn():
    def boom_type(a: str) -> str:
        raise TypeError("thieu tham so noi bo")

    def boom_value(a: str) -> str:
        raise ValueError("gia tri khong hop le")

    tb = ToolBox()
    spec = ToolSpec(name="boom_type", description="d", parameters={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    tb.add(spec, boom_type)
    tb.add(ToolSpec(name="boom_value", description="d", parameters=spec.parameters), boom_value)

    out1 = tb.call(ToolCall(id="1", name="boom_type", args={"a": "x"}))
    out2 = tb.call(ToolCall(id="2", name="boom_value", args={"a": "x"}))
    assert out1.startswith("lỗi tham số:") and out2.startswith("lỗi tham số:")
    assert tb.calls[0]["ok"] is False and tb.calls[1]["ok"] is False


def test_web_fetch_json_content_type_with_invalid_body_falls_back_to_raw_text(monkeypatch):
    def fake_dns(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("studio.tools.socket.getaddrinfo", fake_dns)

    def fetcher(url):
        return 200, "application/json", url, b"khong phai json hop le {"

    wt = WebTools(fetcher=fetcher)
    out = wt.web_fetch("https://a.example.org/api")
    assert "khong phai json hop le" in out
