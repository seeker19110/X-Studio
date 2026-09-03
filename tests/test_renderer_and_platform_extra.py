"""Nhánh còn thiếu của renderer.py (thiếu asset khi ghép, cut.order sắp lại thứ tự cảnh) và platform.py (nhánh lỗi
YouTube hiếm gặp)."""
from __future__ import annotations

import urllib.error
import urllib.parse

import pytest

from studio.bus import InMemoryBus
from studio.events import CutList, Repair, Scene, SceneManifest
from studio.media import MediaConfig, make_media
from studio.platform import ANALYTICS_URL, PlatformError, Tokens, TokenStore, default_fetcher
from studio.renderer import Renderer


def _manifest(vid="V1"):
    return SceneManifest(video_id=vid, scenes=[
        Scene(scene_id="S1", order=0, narration="Câu một.", visual_prompt="bàn làm việc"),
        Scene(scene_id="S2", order=1, narration="Câu hai.", visual_prompt="sơ đồ")])


def test_finalize_raises_when_scene_missing_assets(tmp_path):
    bus = InMemoryBus(); r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    m = _manifest()  # chưa render: các cảnh chưa có asset_refs
    with pytest.raises(ValueError, match="chưa có đủ asset"):
        r.finalize(m, order=["S1", "S2"])


def test_apply_cutlist_with_order_resorts_scenes(tmp_path):
    bus = InMemoryBus(); r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    m = _manifest(); r.render(m)
    cut = CutList(video_id="V1", manifest_version=1, decision="repair", repairs=[Repair(scene_id="S1", action="lock", reason="ok")],
                  order=["S2", "S1"])
    new = r.apply_cutlist(m, cut)
    assert [s.scene_id for s in new.scenes] == ["S2", "S1"]
    assert new.scenes[0].order == 0 and new.scenes[1].order == 1


# ---------- platform.py: nhánh lỗi/hiếm gặp ----------


def test_tokens_expired_true_on_unparseable_expiry():
    assert Tokens(access_token="a", refresh_token="r", client_id="c", client_secret="s", expiry="khong-phai-iso").expired()
    assert Tokens(access_token="a", refresh_token="r", client_id="c", client_secret="s", expiry="").expired()


def test_token_store_chmod_failure_is_swallowed(tmp_path, monkeypatch):
    import os

    st = TokenStore(tmp_path / "auth" / "tok.json")

    def raise_chmod(*a, **k): raise OSError("không đổi được quyền trên hệ thống này")
    monkeypatch.setattr(os, "chmod", raise_chmod)
    st.save(Tokens(access_token="a", refresh_token="r", client_id="c", client_secret="s", expiry="2099-01-01T00:00:00+00:00"))
    assert st.load().access_token == "a"  # lỗi chmod không chặn việc lưu token


class _FakeHTTPResp:
    def __init__(self, status, body: bytes):
        self.status = status; self._body = body; self.headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._body


def test_default_fetcher_success_http_error_and_network_error(monkeypatch):
    import json

    from studio import platform as plat
    monkeypatch.setattr(plat.urllib.request, "urlopen", lambda req, timeout=None: _FakeHTTPResp(200, b'{"ok": true}'))
    st, _headers, body = default_fetcher("GET", "https://x.test/a", {}, None)
    assert st == 200 and json.loads(body) == {"ok": True}

    def raise_http(req, timeout=None):
        import io
        fp = io.BytesIO(b'{"error": "bad"}')
        raise urllib.error.HTTPError("https://x.test/a", 404, "not found", {}, fp)
    monkeypatch.setattr(plat.urllib.request, "urlopen", raise_http)
    st2, _, body2 = default_fetcher("GET", "https://x.test/a", {}, None)
    assert st2 == 404 and b"bad" in body2

    def raise_url(req, timeout=None): raise urllib.error.URLError("mat mang")
    monkeypatch.setattr(plat.urllib.request, "urlopen", raise_url)
    with pytest.raises(PlatformError, match="lỗi mạng"):
        default_fetcher("GET", "https://x.test/a?x=1", {}, None)


def test_youtube_snapshot_impressions_success_branch(tmp_path):
    import json as json_mod

    from studio.platform import YouTubePlatform

    def _enc(d): return json_mod.dumps(d).encode()

    def reports(method, url, headers, body):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query); m = q["metrics"][0]
        if m.startswith("views"):
            return 200, {}, _enc({"columnHeaders": [{"name": n} for n in m.split(",")], "rows": [[100, 90.0, 5.0, 3, 1]]})
        if m.startswith("impressions"):
            return 200, {}, _enc({"columnHeaders": [{"name": "impressions"}, {"name": "impressionsClickThroughRate"}], "rows": [[500, 0.08]]})
        return 200, {}, _enc({"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}], "rows": []})

    class _HTTP:
        def __call__(self, method, url, headers, body):
            if url.startswith(ANALYTICS_URL): return reports(method, url, headers, body)
            return 200, {}, _enc({"items": [{"contentDetails": {"duration": "PT10S"}}]})

    st = TokenStore(tmp_path / "auth" / "tok.json")
    st.save(Tokens(access_token="a", refresh_token="r", client_id="c", client_secret="s", expiry="2099-01-01T00:00:00+00:00"))
    yt = YouTubePlatform(st, _HTTP(), now=lambda: __import__("datetime").datetime(2026, 9, 2, tzinfo=__import__("datetime").UTC))
    r = yt.snapshot("yt1", 7)
    assert r.snapshot.impressions == 500 and r.snapshot.ctr == 0.08
