import pytest

from studio.tools import ToolError, WebTools, default_fetcher


def test_cached_page_survives_new_toolbox_but_policy_still_applies(tmp_path, monkeypatch):
    monkeypatch.setattr("studio.tools._resolve", lambda h: ["93.184.216.34"])
    calls = []
    def fetch(url):
        calls.append(url); return 200, "text/plain", url, b"verified content"
    for _ in range(2):
        web = WebTools(fetcher=fetch, cache_dir=tmp_path, allowed_domains=["example.org"])
        assert "verified content" in web.web_fetch("https://example.org/page")
    assert len(calls) == 1
    with pytest.raises(ToolError):
        WebTools(fetcher=fetch, cache_dir=tmp_path, allowed_domains=["other.org"]).web_fetch("https://example.org/page")


def test_redirect_target_checked_before_network(monkeypatch):
    import urllib.error
    from email.message import Message
    monkeypatch.setattr("studio.tools._resolve", lambda h: ["93.184.216.34"])
    calls = []
    def open_(ip, req):
        calls.append(req.full_url)
        headers = Message(); headers["Location"] = "https://evil.org/page"
        raise urllib.error.HTTPError(req.full_url, 302, "redirect", headers, None)
    with pytest.raises(ToolError, match="domain"):
        default_fetcher("https://example.org", opener=open_, allowed_domains=["example.org"])
    assert calls == ["https://example.org"]


def test_search_filters_disallowed_sources_and_rejects_malformed_rows(monkeypatch):
    monkeypatch.setattr("studio.tools._resolve", lambda h: ["93.184.216.34"])
    body = b'{"results":[null,{"title":"bad","url":"https://example.org.evil.com"},{"title":"ok","url":"https://sub.example.org/a"}]}'
    web = WebTools(fetcher=lambda u: (200, "application/json", u, body), search_url="https://search.org",
                   allowed_domains=["example.org"])
    assert "ok" in web.web_search("query", 1) and "bad" not in web.web_search("query", 1)
