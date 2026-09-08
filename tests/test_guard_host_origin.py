"""Hàng rào `Host` + `Origin` của gateway (chống DNS rebinding và CSRF).

Vì sao cần, đo được trong audit 2026-09-08: gateway KHÔNG có xác thực client và giữ pool tài khoản Google, trong
khi `console/server.py::_guard` đã phòng thủ đúng hai header này từ đầu. Trên loopback, một trang web bất kỳ
người dùng đang mở vẫn `fetch()` được sang `127.0.0.1:1123`: CORS chặn trang đó ĐỌC phản hồi, nhưng không chặn
tác dụng phụ — `POST /v1/chat/completions` đốt quota thật, `POST /auth/login` mở luồng thêm tài khoản.
"""
from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway import auth as gw_auth
from gateway.server import GatewayServer, host_header_is_loopback, origin_is_local
from test_server import StubClient


@pytest.fixture
def manager(tmp_path):
    return gw_auth.AntigravityAuthManager(auth_file=tmp_path / "tokens.json")


async def _client(auth, host="127.0.0.1") -> TestClient:
    server = GatewayServer(host=host, auth_manager=auth, client=StubClient())
    tc = TestClient(TestServer(server.app))
    await tc.start_server()
    return tc


# ---------- hàm thuần: bảng biên ----------

@pytest.mark.parametrize("header,ok", [
    (None, True),                    # HTTP/1.0 không có Host
    ("", True),
    ("127.0.0.1:1123", True),
    ("127.0.0.1", True),
    ("127.9.9.9:80", True),          # cả dải 127/8
    ("localhost:1123", True),
    ("LocalHost:1123", True),        # so sánh không phân biệt hoa thường
    ("[::1]:1123", True),
    ("[::1]", True),
    ("evil.example:1123", False),    # DNS rebinding: A record trỏ 127.0.0.1 nhưng Host là tên kẻ tấn công
    ("192.168.1.5:1123", False),
    ("1270.0.0.1", False),           # tiền tố "127." không được nới thành "1270."
])
def test_host_header_is_loopback(header, ok):
    assert host_header_is_loopback(header) is ok


@pytest.mark.parametrize("origin,ok", [
    (None, True),                        # curl / SDK OpenAI: không phải trình duyệt
    ("", True),
    ("http://127.0.0.1:3000", True),     # trang dev cục bộ ở CỔNG KHÁC vẫn hợp lệ
    ("http://localhost:1123", True),
    ("https://[::1]:8080", True),
    ("https://evil.example", False),
    ("null", False),                     # sandbox iframe / file://
    ("http://", False),
    ("ftp://127.0.0.1", False),
    ("127.0.0.1:1123", False),           # thiếu scheme
])
def test_origin_is_local(origin, ok):
    assert origin_is_local(origin) is ok


# ---------- qua HTTP thật ----------

@pytest.mark.asyncio
async def test_host_la_ten_mien_la_bi_tu_choi_404(manager):
    """404 chứ không 403: không xác nhận cho kẻ tấn công rằng có server ở đây."""
    tc = await _client(manager)
    try:
        r = await tc.get("/health", headers={"Host": "evil.example"})
        assert r.status == 404
        assert (await r.json())["error"]["type"] == "invalid_request_error"
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_origin_la_bi_tu_choi_truoc_khi_cham_pool(manager):
    """Chặn phải xảy ra ở middleware, TRƯỚC handler: nếu chỉ dựa vào CORS thì request đã đốt quota xong rồi
    trình duyệt mới giấu phản hồi đi."""
    tc = await _client(manager)
    try:
        for path, kw in (("/v1/chat/completions", {"json": {"model": "m", "messages": []}}), ("/auth/login", {})):
            r = await tc.post(path, headers={"Origin": "https://evil.example"}, **kw)
            assert r.status == 403, path
            assert (await r.json())["error"]["type"] == "permission_error"
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_client_dong_lenh_va_trang_cuc_bo_van_qua(manager):
    tc = await _client(manager)
    try:
        assert (await tc.get("/health")).status == 200                                     # không Origin
        assert (await tc.get("/health", headers={"Origin": "http://localhost:3000"})).status == 200
    finally:
        await tc.close()


@pytest.mark.asyncio
async def test_bind_ra_ngoai_loopback_thi_khong_ep_hai_luat(manager):
    """Người vận hành cố ý mở ra ngoài (đã bị `warn_if_public_host` cảnh báo, và chế độ đó vốn đòi
    firewall/reverse proxy lo xác thực): khi ấy `Host` hợp lệ LÀ tên miền thật, ép luật loopback là chặn nhầm."""
    tc = await _client(manager, host="0.0.0.0")
    try:
        r = await tc.get("/health", headers={"Host": "gw.example", "Origin": "https://gw.example"})
        assert r.status == 200
    finally:
        await tc.close()
