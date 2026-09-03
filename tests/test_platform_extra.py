"""Vài nhánh còn thiếu của platform.py: Tokens.expired() với chuỗi hỏng, default_fetcher (HTTP/URL error thật,
không qua FakeHTTP của test_platform.py), snapshot khi impressions trả về thành công."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from datetime import UTC, datetime

from studio.platform import ANALYTICS_URL, API_URL, Tokens, YouTubePlatform, default_fetcher

from test_platform import NOW, FakeHTTP, _store


def test_tokens_expired_with_unparseable_expiry_string_is_expired():
    assert Tokens(expiry="khong-phai-iso").expired() is True


def test_token_store_save_ignores_chmod_failure(tmp_path, monkeypatch):
    import os

    from studio.platform import TokenStore

    store = TokenStore(tmp_path / "tok.json")

    def raise_chmod(path, mode):
        raise OSError("chmod không được hỗ trợ ở đây")

    monkeypatch.setattr(os, "chmod", raise_chmod)
    store.save(Tokens(access_token="a"))  # không raise dù chmod lỗi (best-effort, đã tạo file với mode đúng từ os.open)
    assert store.path.exists()


class _FakeHTTPResponse:
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"ok": true}'


def test_default_fetcher_success(monkeypatch):
    import studio.platform as plat

    monkeypatch.setattr(plat.urllib.request, "urlopen", lambda req, timeout=None: _FakeHTTPResponse())
    status, headers, body = default_fetcher("GET", "https://x.test/a", {}, None)
    assert status == 200 and json.loads(body) == {"ok": True}


def test_default_fetcher_http_error_returns_status_and_body(monkeypatch):
    import studio.platform as plat

    class _FP:
        def read(self):
            return b'{"error": "bad"}'

    def raise_http(req, timeout=None):
        raise urllib.error.HTTPError("u", 403, "forbidden", {}, _FP())

    monkeypatch.setattr(plat.urllib.request, "urlopen", raise_http)
    status, headers, body = default_fetcher("GET", "https://x.test/a", {}, None)
    assert status == 403 and json.loads(body) == {"error": "bad"}


def test_default_fetcher_network_error_raises_platform_error(monkeypatch):
    import studio.platform as plat
    from studio.platform import PlatformError

    def raise_url(req, timeout=None):
        raise urllib.error.URLError("mat mang")

    monkeypatch.setattr(plat.urllib.request, "urlopen", raise_url)
    try:
        default_fetcher("GET", "https://x.test/a?k=v", {}, None)
        assert False, "phải raise"
    except PlatformError as e:
        assert "lỗi mạng" in str(e)


def test_youtube_snapshot_impressions_success_path(tmp_path):
    def reports(method, url, headers, body):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query); m = q["metrics"][0]
        if m.startswith("views"):
            return 200, {}, {"columnHeaders": [{"name": n} for n in m.split(",")], "rows": [[1200, 95.5, 7.3, 40, 6]]}
        if m.startswith("impressions"):
            return 200, {}, {"columnHeaders": [{"name": n} for n in m.split(",")], "rows": [[50000, 0.045]]}
        return 200, {}, {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}], "rows": [[0.0, 1.0]]}

    http = FakeHTTP().on("GET", ANALYTICS_URL, reports)
    http.on("GET", f"{API_URL}/videos", (200, {}, {"items": [{"contentDetails": {"duration": "PT1M40S"}}]}))
    r = YouTubePlatform(_store(tmp_path), http, lambda: NOW).snapshot("yt1", 7, channel_id="CH1")
    assert r.snapshot.impressions == 50000 and r.snapshot.ctr == 0.045
    ev = json.loads(r.evidence)
    assert ev["impressions"]["status"] == 200
