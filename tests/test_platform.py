"""Adapter nền tảng (ADR-0008): FakePlatform; YouTubePlatform với HTTP giả (resumable 2 bước, refresh khi 401, quota 403,
analytics thiếu quyền → 0 + evidence); CLI youtube (URL OAuth, đổi mã, sync-comments/sync-metrics lên bus). Không chạm mạng."""
from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import pytest

from studio.bus import InMemoryBus
from studio.events import Chapter, Envelope, MetadataPackage
from studio.media import MediaConfig, load_media_config
from studio.platform import (
    ANALYTICS_URL,
    API_URL,
    TOKEN_URL,
    UPLOAD_URL,
    FakePlatform,
    PlatformError,
    Tokens,
    TokenStore,
    YouTubePlatform,
    make_platform,
    parse_duration,
)
from studio.youtube import auth_url, exchange_code, find_ref, main, sync_comments, sync_metrics

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
META = MetadataPackage(video_id="CH1-V1", title="AI dựng video", description="mô tả", tags=["ai"], chapters=[Chapter(time="00:00", label="Mở đầu")],
                       language="vi", category="27")


def _store(tmp_path, expiry="2099-01-01T00:00:00+00:00", access="tok-1"):
    st = TokenStore(tmp_path / "auth" / "youtube_tokens.json")
    st.save(Tokens(access_token=access, refresh_token="rt", client_id="cid", client_secret="cs", expiry=expiry, scopes=["a"]))
    return st


class FakeHTTP:
    """HTTP giả: ghi mọi lời gọi; handler theo (method, url-prefix) → (status, headers, body)."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.routes: list[tuple[str, str, object]] = []

    def on(self, method, prefix, resp):
        self.routes.append((method, prefix, resp)); return self

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        for m, pre, resp in self.routes:
            if m == method and url.startswith(pre):
                r = resp(method, url, headers, body) if callable(resp) else resp
                st, h, b = r
                return st, h, (json.dumps(b).encode() if isinstance(b, dict) else b)
        raise AssertionError(f"không có route giả cho {method} {url}")

    def urls(self, method=None):
        return [u for m, u, _, _ in self.calls if method is None or m == method]


# ---------- fake ----------

def test_fake_platform_records_calls_and_fails_on_demand(tmp_path):
    f = tmp_path / "final.mp4"; f.write_bytes(b"x" * 10); t = tmp_path / "A.png"; t.write_bytes(b"png")
    p = FakePlatform()
    up = p.upload_video(f, META); assert up.platform_ref == "fake-0001" and up.url.endswith("fake-0001") and "bytes=10" in up.evidence
    assert "thumbnail" in p.set_thumbnail(up.platform_ref, t) and "@ 2026" in p.schedule(up.platform_ref, "2026-09-05T12:00:00Z")
    assert p.videos["fake-0001"]["publish_at"] == "2026-09-05T12:00:00Z" and p.videos["fake-0001"]["thumbnail"] == str(t)
    r = p.reply("c1", "cảm ơn"); assert r.platform_ref == "reply:fake-reply-c1" and r.comment_id == "c1"
    assert [c[0] for c in p.calls] == ["upload_video", "set_thumbnail", "schedule", "reply"]
    p.fail.add("upload_video")
    with pytest.raises(PlatformError) as ei: p.upload_video(f, META)
    assert ei.value.status == 403
    with pytest.raises(PlatformError): FakePlatform().upload_video(tmp_path / "missing.mp4", META)


def test_make_platform_defaults_to_fake_and_reads_env(tmp_path, monkeypatch):
    assert make_platform(MediaConfig()).name == "fake"
    monkeypatch.setenv("STUDIO_PLATFORM", "youtube"); monkeypatch.setenv("STUDIO_YOUTUBE_TOKENS", str(tmp_path / "t.json"))
    cfg = load_media_config(tmp_path / "none.yaml"); assert cfg.platform["provider"] == "youtube"
    yt = make_platform(cfg); assert yt.name == "youtube" and yt.store.path == tmp_path / "t.json"
    with pytest.raises(PlatformError): yt.reply("c", "x")  # chưa đăng nhập → lỗi rõ, không chạm mạng
    with pytest.raises(PlatformError): make_platform(MediaConfig(platform={"provider": "tiktok"}))


# ---------- YouTube: upload / thumbnail / lịch ----------

def test_youtube_resumable_upload_two_steps_then_thumbnail_and_schedule(tmp_path):
    f = tmp_path / "final.mp4"; f.write_bytes(b"v" * 100); t = tmp_path / "A.png"; t.write_bytes(b"\x89PNG")
    http = FakeHTTP()
    http.on("POST", UPLOAD_URL, (200, {"location": "https://upload.example/session/1"}, b""))
    http.on("PUT", "https://upload.example/session/1", (200, {}, {"id": "yt123", "status": {"privacyStatus": "private", "uploadStatus": "uploaded"}}))
    http.on("POST", "https://www.googleapis.com/upload/youtube/v3/thumbnails/set", (200, {}, {"items": []}))
    http.on("PUT", f"{API_URL}/videos", (200, {}, {"id": "yt123", "status": {"privacyStatus": "private", "publishAt": "2026-09-05T12:00:00Z"}}))
    yt = YouTubePlatform(_store(tmp_path), fetcher=http, now=lambda: NOW)
    up = yt.upload_video(f, META)
    assert (up.platform_ref, up.url, up.status) == ("yt123", "https://youtu.be/yt123", "private")
    init = http.calls[0]; body = json.loads(init[3])
    assert "uploadType=resumable" in init[1] and init[2]["X-Upload-Content-Length"] == "100" and init[2]["Authorization"] == "Bearer tok-1"
    assert body["snippet"]["title"] == "AI dựng video" and "00:00 Mở đầu" in body["snippet"]["description"] and body["snippet"]["categoryId"] == "27"
    assert body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    put = http.calls[1]; assert put[0] == "PUT" and put[3] == b"v" * 100 and put[2]["Content-Length"] == "100"
    ev = json.loads(up.evidence); assert ev["init"] == 200 and ev["put"] == 200 and ev["id"] == "yt123" and ev["bytes"] == 100
    th = yt.set_thumbnail("yt123", t); assert json.loads(th)["thumbnails.set"] == 200
    assert "videoId=yt123" in http.calls[2][1] and http.calls[2][2]["Content-Type"] == "image/png"
    sc = yt.schedule("yt123", "2026-09-05T12:00:00Z"); assert json.loads(sc)["publishAt"] == "2026-09-05T12:00:00Z"
    assert json.loads(http.calls[3][3]) == {"id": "yt123", "status": {"privacyStatus": "private", "publishAt": "2026-09-05T12:00:00Z"}}
    assert not any(TOKEN_URL in u for u in http.urls())  # token còn hạn → không refresh


def test_youtube_upload_missing_location_or_id_is_clear_error(tmp_path):
    f = tmp_path / "f.mp4"; f.write_bytes(b"v")
    http = FakeHTTP().on("POST", UPLOAD_URL, (200, {}, b""))
    with pytest.raises(PlatformError, match="Location"): YouTubePlatform(_store(tmp_path), http, lambda: NOW).upload_video(f, META)
    http = FakeHTTP().on("POST", UPLOAD_URL, (200, {"location": "https://u/1"}, b"")).on("PUT", "https://u/1", (200, {}, {"kind": "x"}))
    with pytest.raises(PlatformError, match="không có id"): YouTubePlatform(_store(tmp_path), http, lambda: NOW).upload_video(f, META)


# ---------- YouTube: token ----------

def test_youtube_refreshes_expired_token_before_call_and_saves(tmp_path):
    st = _store(tmp_path, expiry="2020-01-01T00:00:00+00:00", access="old")
    http = FakeHTTP().on("POST", TOKEN_URL, (200, {}, {"access_token": "new", "expires_in": 3600, "scope": "s1 s2"}))
    http.on("POST", f"{API_URL}/comments", (200, {}, {"id": "r1"}))
    yt = YouTubePlatform(st, http, lambda: NOW)
    r = yt.reply("c1", "hi")
    assert r.reply_id == "r1" and r.platform_ref == "reply:r1"
    assert http.calls[0][1] == TOKEN_URL and "grant_type=refresh_token" in http.calls[0][3].decode() and "cs" in urllib.parse.parse_qs(http.calls[0][3].decode())["client_secret"]
    assert http.calls[1][2]["Authorization"] == "Bearer new" and json.loads(http.calls[1][3]) == {"snippet": {"parentId": "c1", "textOriginal": "hi"}}
    saved = st.load(); assert saved.access_token == "new" and saved.scopes == ["s1", "s2"] and saved.expiry.startswith("2026-09-02T13:00")
    assert st.status()["logged_in"] and st.status()["expiry"] == saved.expiry and "cs" not in json.dumps(st.status())


def test_youtube_refreshes_once_on_401_then_retries(tmp_path):
    seen = {"n": 0}
    def comments(method, url, headers, body):
        seen["n"] += 1
        return (401, {}, {"error": {"message": "Invalid Credentials"}}) if headers["Authorization"] == "Bearer tok-1" else (200, {}, {"id": "r9"})
    http = FakeHTTP().on("POST", f"{API_URL}/comments", comments).on("POST", TOKEN_URL, (200, {}, {"access_token": "fresh", "expires_in": 100}))
    yt = YouTubePlatform(_store(tmp_path), http, lambda: NOW)
    assert yt.reply("c", "x").reply_id == "r9" and seen["n"] == 2
    assert [c[0] + " " + c[1].split("?")[0] for c in http.calls] == [f"POST {API_URL}/comments", f"POST {TOKEN_URL}", f"POST {API_URL}/comments"]


def test_youtube_refresh_failure_and_quota_403_are_platform_errors(tmp_path):
    http = FakeHTTP().on("POST", TOKEN_URL, (400, {}, {"error": "invalid_grant"}))
    with pytest.raises(PlatformError, match="đăng nhập lại") as ei:
        YouTubePlatform(_store(tmp_path, expiry="2020-01-01T00:00:00+00:00"), http, lambda: NOW).reply("c", "x")
    assert ei.value.status == 400
    http = FakeHTTP().on("POST", f"{API_URL}/comments", (403, {}, {"error": {"message": "The request cannot be completed because you have exceeded your quota."}}))
    with pytest.raises(PlatformError, match=r"quota \(quotaExceeded\) HTTP 403 POST comments: The request cannot") as ei:
        YouTubePlatform(_store(tmp_path), http, lambda: NOW).reply("c", "x")
    assert ei.value.status == 403 and ei.value.reason == "quotaExceeded" and len(http.calls) == 1  # không tự retry khi quota


# ---------- YouTube: bình luận + số liệu ----------

def _thread(cid, text, at, author="an", likes=2):
    return {"id": cid, "snippet": {"topLevelComment": {"id": cid, "snippet": {"textOriginal": text, "authorDisplayName": author, "likeCount": likes, "publishedAt": at}}}}


def test_youtube_list_comments_paginates_and_filters_since(tmp_path):
    def threads(method, url, headers, body):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["videoId"] == ["yt1"] and q["part"] == ["snippet"]
        if "pageToken" not in q:
            return 200, {}, {"items": [_thread("c3", "mới nhất", "2026-09-02T10:00:00Z"), _thread("c2", "hôm qua", "2026-09-01T10:00:00Z")], "nextPageToken": "p2"}
        return 200, {}, {"items": [_thread("c1", "cũ", "2026-08-20T10:00:00Z")]}
    http = FakeHTTP().on("GET", f"{API_URL}/commentThreads", threads)
    yt = YouTubePlatform(_store(tmp_path), http, lambda: NOW)
    allc = yt.list_comments("yt1"); assert [c.comment_id for c in allc] == ["c3", "c2", "c1"] and allc[0].likes == 2 and allc[0].author == "an"
    recent = yt.list_comments("yt1", since="2026-09-01T00:00:00Z"); assert [c.comment_id for c in recent] == ["c3", "c2"]


def test_youtube_snapshot_uses_real_numbers_and_marks_missing_permissions(tmp_path):
    def reports(method, url, headers, body):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query); m = q["metrics"][0]
        assert q["filters"] == ["video==yt1"] and q["ids"] == ["channel==MINE"] and q["startDate"] == ["2026-08-27"] and q["endDate"] == ["2026-09-02"]
        if m.startswith("views"):
            return 200, {}, {"columnHeaders": [{"name": n} for n in m.split(",")], "rows": [[1200, 95.5, 7.3, 40, 6]]}
        if m.startswith("impressions"):
            return 403, {}, {"error": {"message": "Insufficient permission / unknown metric"}}
        assert q["dimensions"] == ["elapsedVideoTimeRatio"]
        return 200, {}, {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}], "rows": [[0.0, 1.0], [0.5, 0.8], [1.0, 0.6]]}
    http = FakeHTTP().on("GET", ANALYTICS_URL, reports)
    http.on("GET", f"{API_URL}/videos", (200, {}, {"items": [{"contentDetails": {"duration": "PT1M40S"}}]}))
    r = YouTubePlatform(_store(tmp_path), http, lambda: NOW).snapshot("yt1", 7, channel_id="CH1")
    s = r.snapshot
    assert (s.views, s.avg_view_duration_s, s.likes, s.comments, s.window_days, s.channel_id) == (1200, 7.3, 40, 6, 7, "CH1")
    assert (s.impressions, s.ctr) == (0, 0.0)
    assert [(p.t, p.pct) for p in s.retention_curve] == [(0.0, 100.0), (50.0, 80.0), (100.0, 60.0)]
    ev = json.loads(r.evidence)
    assert ev["core"]["status"] == 200 and ev["impressions"]["status"] == 403 and "không bịa" in ev["impressions"]["note"] and ev["duration_s"] == 100.0
    assert ev["retention"]["points"] == 3 and ev["window"] == ["2026-08-27", "2026-09-02"]


def test_youtube_snapshot_all_failures_keeps_zero_with_evidence(tmp_path):
    http = FakeHTTP().on("GET", ANALYTICS_URL, (403, {}, {"error": {"message": "no analytics scope"}})).on("GET", f"{API_URL}/videos", (404, {}, {}))
    r = YouTubePlatform(_store(tmp_path), http, lambda: NOW).snapshot("yt1", 28)
    assert r.snapshot.views == 0 and r.snapshot.retention_curve == [] and r.snapshot.window_days == 28
    ev = json.loads(r.evidence); assert ev["core"]["status"] == 403 and ev["retention"]["status"] == 403 and ev["duration"]["status"] == 404
    assert parse_duration("PT2H3M4S") == 7384.0 and parse_duration("") == 0.0


# ---------- CLI youtube: OAuth thuần + sync lên bus ----------

def test_auth_url_and_exchange_code_offline(tmp_path):
    u = auth_url("cid", "http://127.0.0.1:8765/", "st1")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and "youtube.upload" in q["scope"][0] and q["state"] == ["st1"]
    http = FakeHTTP().on("POST", TOKEN_URL, (200, {}, {"access_token": "a", "refresh_token": "r", "expires_in": 60, "scope": "x y"}))
    t = exchange_code("code1", "cid", "cs", "http://127.0.0.1:8765/", http, now=NOW)
    body = urllib.parse.parse_qs(http.calls[0][3].decode())
    assert body["grant_type"] == ["authorization_code"] and body["code"] == ["code1"] and (t.access_token, t.refresh_token, t.scopes) == ("a", "r", ["x", "y"])
    http = FakeHTTP().on("POST", TOKEN_URL, (200, {}, {"access_token": "a", "expires_in": 60}))
    with pytest.raises(PlatformError, match="refresh_token"): exchange_code("c", "cid", "cs", "http://127.0.0.1:1/", http)


def test_sync_comments_and_metrics_publish_real_data_to_bus():
    bus = InMemoryBus(); p = FakePlatform()
    bus.publish(Envelope(topic="video-briefs", key="V1", actor="human", payload={"video_id": "V1", "channel_id": "CH9", "working_title": "t", "pillar": "p",
                                                                                 "angle": "a", "audience": "x", "estimate_tokens": 10}))
    with pytest.raises(PlatformError, match="platform_ref"): sync_comments(bus, p, "V1")
    bus.publish(Envelope(topic="publish-events", key="V1", actor="publisher", payload={"video_id": "V1", "status": "scheduled", "platform_ref": "fake-0001"}))
    assert find_ref(bus, "V1") == "fake-0001"
    assert sync_comments(bus, p, "V1") is None  # không có bình luận → không phát event rỗng
    from studio.platform import Comment
    p.comments["fake-0001"] = [Comment("c1", "hay quá", "an", 3, "2026-09-01T00:00:00Z"), Comment("c0", "cũ", "b", 0, "2026-08-01T00:00:00Z")]
    env = sync_comments(bus, p, "V1", since="2026-08-15T00:00:00Z")
    assert env.topic == "audience-comments" and env.actor == "adapter:youtube" and [c["comment_id"] for c in env.payload["comments"]] == ["c1"]
    assert env.payload["comments"][0] == {"comment_id": "c1", "author": "an", "text": "hay quá", "likes": 3, "published_at": "2026-09-01T00:00:00Z"}
    env2 = sync_metrics(bus, p, "V1", window_days=14, variant_id="A")
    assert env2.topic == "performance-snapshots" and env2.payload["video_id"] == "V1" and env2.payload["channel_id"] == "CH9"
    assert env2.payload["window_days"] == 14 and env2.payload["variant_id"] == "A" and env2.payload["views"] == 0
    acts = [(e.payload["action"], json.loads(e.payload["evidence"])) for e in bus.replay("audit-log") if e.actor == "adapter:youtube"]
    assert [a for a, _ in acts] == ["platform.comments", "platform.comments", "platform.snapshot"] and acts[-1][1]["evidence"].startswith("fake snapshot")
    assert [c[0] for c in p.calls] == ["list_comments", "list_comments", "snapshot"]


def test_cli_status_and_sync_with_fake_platform(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STUDIO_YOUTUBE_TOKENS", str(tmp_path / "none.json")); monkeypatch.delenv("STUDIO_PLATFORM", raising=False)
    assert main(["status"]) == 0
    out = json.loads(capsys.readouterr().out); assert out["provider"] == "fake" and out["logged_in"] is False
    db = tmp_path / "s.sqlite"
    from studio.sqlite_bus import SQLiteBus
    b = SQLiteBus(db); b.publish(Envelope(topic="publish-events", key="V1", actor="publisher", payload={"video_id": "V1", "status": "scheduled", "platform_ref": "fake-1"})); b.close()
    assert main(["--db", str(db), "sync-metrics", "V1", "--window", "3"]) == 0 and "views=0" in capsys.readouterr().out
    assert main(["--db", str(db), "sync-comments", "V2"]) == 1 and "platform_ref" in capsys.readouterr().err
    b = SQLiteBus(db); assert [e.payload["window_days"] for e in b.replay("performance-snapshots", "V1")] == [3]; b.close()


def test_token_file_is_private_and_missing_client_secret_is_clear(tmp_path):
    st = _store(tmp_path); assert st.path.exists() and st.load().client_secret == "cs"
    from studio.youtube import load_client_secrets
    (tmp_path / "cs.json").write_text(json.dumps({"installed": {"client_id": "i", "client_secret": "s"}}), encoding="utf-8")
    assert load_client_secrets(tmp_path / "cs.json") == ("i", "s")
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PlatformError, match="client_id"): load_client_secrets(tmp_path / "bad.json")
    assert Path(st.path).read_text(encoding="utf-8").count("cs") >= 1


def test_token_file_created_with_0600_from_the_start(tmp_path, monkeypatch):
    import os
    import stat as st_
    seen = []
    real_open = os.open
    def spy(path, flags, mode=0o777, *a, **k):
        seen.append((str(path), mode)); return real_open(path, flags, mode, *a, **k)
    monkeypatch.setattr(os, "open", spy)
    st = _store(tmp_path)
    assert any(p == str(st.path) and m == 0o600 for p, m in seen)  # mode 0600 ngay lúc tạo, không có khoảnh khắc 0644
    assert st.load().access_token == "tok-1"
    if os.name == "posix":  # Windows không có bit quyền POSIX, stat() luôn báo 0666 dù os.open đã nhận 0600
        assert st_.S_IMODE(st.path.stat().st_mode) == 0o600


def test_youtube_retries_5xx_429_and_network_with_backoff_but_not_403(tmp_path):
    slept = []; n = {"k": 0}
    def flaky(method, url, headers, body):
        n["k"] += 1
        if n["k"] == 1: return 503, {}, {"error": {"message": "backend"}}
        if n["k"] == 2: raise PlatformError("lỗi mạng POST comments")
        if n["k"] == 3: return 429, {}, {"error": {"message": "slow down"}}
        return 200, {}, {"id": "r1"}
    http = FakeHTTP().on("POST", f"{API_URL}/comments", flaky)
    yt = YouTubePlatform(_store(tmp_path), http, lambda: NOW, sleep=slept.append)
    with pytest.raises(PlatformError) as ei: yt.reply("c", "x")  # 3 lần đều lỗi tạm thời → ném lỗi cuối
    assert ei.value.status == 429 and len(slept) == 2 and 0.5 <= slept[0] < 0.75 and 1.0 <= slept[1] < 1.25
    assert yt.reply("c", "x").reply_id == "r1" and n["k"] == 4 and len(slept) == 2  # lần sau thành công ngay, không chờ
    # 403 thiếu quyền: không retry, reason=forbidden, khác với quotaExceeded
    http = FakeHTTP().on("POST", f"{API_URL}/comments", (403, {}, {"error": {"message": "Insufficient Permission",
                                                                            "errors": [{"reason": "insufficientPermissions"}]}}))
    with pytest.raises(PlatformError, match=r"quyền \(insufficientPermissions\)") as ei:
        YouTubePlatform(_store(tmp_path), http, lambda: NOW, sleep=slept.append).reply("c", "x")
    assert ei.value.reason == "insufficientPermissions" and len(http.calls) == 1 and len(slept) == 2
    http = FakeHTTP().on("POST", f"{API_URL}/comments", (403, {}, {"error": {"message": "Quota", "errors": [{"reason": "quotaExceeded"}]}}))
    with pytest.raises(PlatformError, match=r"quota \(quotaExceeded\)"): YouTubePlatform(_store(tmp_path), http, lambda: NOW).reply("c", "x")


def test_sync_comments_skips_seen_and_replied_and_since_compares_datetimes():
    from studio.platform import Comment, parse_ts
    from studio.youtube import seen_comment_ids
    assert parse_ts("2026-09-01T00:00:00Z") == datetime(2026, 9, 1, tzinfo=UTC) and parse_ts("2026-09-01T00:00:00") == datetime(2026, 9, 1, tzinfo=UTC)
    assert parse_ts("rác") is None and parse_ts(None) is None
    bus = InMemoryBus(); p = FakePlatform()
    p.comments["fake-0001"] = [Comment("c1", "a", "x", 0, "2026-09-01T00:00:00Z"), Comment("c2", "b", "y", 0, "2026-09-02T00:00:00.000Z"),
                               Comment("c3", "c", "z", 0, "2026-08-01T00:00:00+00:00")]
    # `since` với múi giờ khác / độ chính xác khác: so bằng datetime chứ không so chuỗi
    assert [c.comment_id for c in p.list_comments("fake-0001", since="2026-09-01T02:00:00+02:00")] == ["c1", "c2"]
    bus.publish(Envelope(topic="audience-comments", key="V1", actor="human", payload={"video_id": "V1", "comments": [{"comment_id": "c1", "text": "a"}]}))
    bus.publish(Envelope(topic="publish-events", key="V1", actor="publisher", payload={"video_id": "V1", "kind": "reply", "status": "published",
                                                                                      "platform_ref": "reply:r3", "comment_id": "c3"}))
    assert seen_comment_ids(bus, "V1") == {"c1", "c3"}
    env = sync_comments(bus, p, "V1", ref="fake-0001")
    assert [c["comment_id"] for c in env.payload["comments"]] == ["c2"]
    ev = json.loads(next(e.payload["evidence"] for e in bus.replay("audit-log") if e.payload["action"] == "platform.comments"))
    assert ev["count"] == 1 and ev["skipped"] == 2
    assert sync_comments(bus, p, "V1", ref="fake-0001") is None  # kéo lại: tất cả đã thấy
