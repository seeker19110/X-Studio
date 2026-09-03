"""Adapter nền tảng phân phối (ADR-0008): upload, thumbnail, lên lịch, bình luận, trả lời, số liệu.

Chỉ CODE gọi lớp này (orchestrator sau gate `publish`/`replies`, CLI `studio.youtube sync-*`); không model nào chạm tới.
`Platform` là interface trung lập nền tảng; `FakePlatform` chạy offline (test/demo, mặc định); `YouTubePlatform` gọi
YouTube Data API v3 + YouTube Analytics API v2 bằng `urllib` thuần qua `fetcher` tiêm được — test không chạm mạng.
Mọi kết quả kèm `evidence` (endpoint, HTTP status, id trả về) để audit-log có bằng chứng; số liệu là số thật từ API,
thiếu quyền thì để 0 và nói rõ trong evidence.
"""
from __future__ import annotations

import json
import os
import random
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .events import MetadataPackage, PerformanceSnapshot, RetentionPoint
from .media import MediaConfig, load_media_config

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API_URL = "https://www.googleapis.com/youtube/v3"
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/yt-analytics.readonly")
DEFAULT_TOKEN_FILE = Path.home() / ".x-agents" / "auth" / "youtube_tokens.json"
TIMEOUT = 120.0
MAX_ATTEMPTS = 3  # 5xx / 429 / lỗi mạng: thử lại với backoff mũ + jitter; 4xx khác (403 quota, 400...) không thử lại
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFF_BASE_S = 0.5
# reason trong error.errors[].reason của Google API cho biết 403 là hết quota hay thiếu quyền — xử lý khác nhau
QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"})

Fetcher = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]


class PlatformError(Exception):
    def __init__(self, msg: str, status: int | None = None, reason: str | None = None):
        super().__init__(msg); self.status = status
        self.reason = reason  # 403: quotaExceeded | forbidden ...; None với lỗi khác


@dataclass
class UploadResult:
    platform_ref: str
    url: str
    status: str  # private | scheduled | public
    evidence: str = ""


@dataclass
class ReplyResult:
    reply_id: str
    comment_id: str
    platform_ref: str
    evidence: str = ""


@dataclass
class Comment:
    comment_id: str
    text: str
    author: str = ""
    likes: int = 0
    published_at: str = ""


def parse_ts(s: str | None) -> datetime | None:
    """ISO 8601 (chấp nhận `Z`) → datetime có múi giờ; thiếu múi giờ coi là UTC; hỏng → None."""
    if not s: return None
    try: d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError: return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _before(published_at: str, since: str | None) -> bool:
    """published_at < since, so sánh bằng datetime (không so chuỗi: `Z` vs `+00:00`, độ chính xác khác nhau)."""
    a, b = parse_ts(published_at), parse_ts(since)
    return a is not None and b is not None and a < b


@dataclass
class SnapshotResult:
    snapshot: PerformanceSnapshot
    evidence: str = ""


class Platform(Protocol):
    name: str

    def upload_video(self, path: Path, metadata: MetadataPackage, privacy: str = "private", publish_at: str | None = None,
                     made_for_kids: bool = False) -> UploadResult: ...
    def set_thumbnail(self, platform_ref: str, path: Path) -> str: ...
    def schedule(self, platform_ref: str, publish_at: str) -> str: ...
    def list_comments(self, platform_ref: str, since: str | None = None) -> list[Comment]: ...
    def reply(self, comment_id: str, text: str) -> ReplyResult: ...
    def snapshot(self, platform_ref: str, window_days: int = 7, channel_id: str = "") -> SnapshotResult: ...


# ---------- cấu hình ----------

def make_platform(cfg: MediaConfig | None = None, fetcher: Fetcher | None = None) -> Platform:
    """`platform.provider` trong media.yaml hoặc STUDIO_PLATFORM: fake (mặc định) | youtube."""
    cfg = cfg or load_media_config()
    prov = str(cfg.platform.get("provider") or "fake")
    if prov == "fake": return FakePlatform()
    if prov == "youtube": return YouTubePlatform(TokenStore(_token_path(cfg)), fetcher=fetcher)
    raise PlatformError(f"platform: provider lạ `{prov}` (fake | youtube)")


def _token_path(cfg: MediaConfig) -> Path:
    return Path(os.environ.get("STUDIO_YOUTUBE_TOKENS") or cfg.platform.get("tokens") or DEFAULT_TOKEN_FILE).expanduser()


# ---------- fake (offline) ----------

class FakePlatform:
    name = "fake"

    def __init__(self):
        self.videos: dict[str, dict[str, Any]] = {}
        self.comments: dict[str, list[Comment]] = {}
        self.replies: list[ReplyResult] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: set[str] = set()  # tên thao tác muốn cho lỗi (test)

    def _check(self, op: str, **kw: Any) -> None:
        self.calls.append((op, kw))
        if op in self.fail: raise PlatformError(f"fake: {op} lỗi giả lập (quota)", status=403)

    def upload_video(self, path: Path, metadata: MetadataPackage, privacy: str = "private", publish_at: str | None = None,
                     made_for_kids: bool = False) -> UploadResult:
        self._check("upload_video", path=str(path), video_id=metadata.video_id, title=metadata.title, privacy=privacy, publish_at=publish_at)
        if not Path(path).exists(): raise PlatformError(f"không có file {path}")
        ref = f"fake-{len(self.videos) + 1:04d}"
        self.videos[ref] = {"title": metadata.title, "privacy": privacy, "publish_at": publish_at, "thumbnail": None, "path": str(path)}
        return UploadResult(ref, f"https://fake.video/{ref}", "scheduled" if publish_at else privacy, evidence=f"fake upload {ref} bytes={Path(path).stat().st_size}")

    def set_thumbnail(self, platform_ref: str, path: Path) -> str:
        self._check("set_thumbnail", platform_ref=platform_ref, path=str(path))
        self.videos[platform_ref]["thumbnail"] = str(path); return f"fake thumbnail {platform_ref} ← {Path(path).name}"

    def schedule(self, platform_ref: str, publish_at: str) -> str:
        self._check("schedule", platform_ref=platform_ref, publish_at=publish_at)
        self.videos[platform_ref].update(privacy="private", publish_at=publish_at); return f"fake schedule {platform_ref} @ {publish_at}"

    def list_comments(self, platform_ref: str, since: str | None = None) -> list[Comment]:
        self._check("list_comments", platform_ref=platform_ref, since=since)
        return [c for c in self.comments.get(platform_ref, []) if not _before(c.published_at, since)]

    def reply(self, comment_id: str, text: str) -> ReplyResult:
        self._check("reply", comment_id=comment_id, text=text)
        r = ReplyResult(f"fake-reply-{comment_id}", comment_id, f"reply:fake-reply-{comment_id}", evidence=f"fake reply to {comment_id}: {text[:60]}")
        self.replies.append(r); return r

    def snapshot(self, platform_ref: str, window_days: int = 7, channel_id: str = "") -> SnapshotResult:
        self._check("snapshot", platform_ref=platform_ref, window_days=window_days)
        v = self.videos.get(platform_ref, {})
        snap = PerformanceSnapshot(video_id=str(v.get("video_id") or platform_ref), channel_id=channel_id or "fake", window_days=window_days)
        return SnapshotResult(snap, evidence="fake snapshot: mọi số liệu 0 (không có API)")


# ---------- token OAuth ----------

@dataclass
class Tokens:
    access_token: str = ""
    refresh_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    expiry: str = ""  # ISO 8601 UTC
    scopes: list[str] = field(default_factory=list)

    def expired(self, now: datetime | None = None, skew_s: int = 60) -> bool:
        if not self.expiry: return True
        try: exp = datetime.fromisoformat(self.expiry.replace("Z", "+00:00"))
        except ValueError: return True
        return (now or datetime.now(UTC)) + timedelta(seconds=skew_s) >= exp


class TokenStore:
    """Đọc/ghi `youtube_tokens.json` (access_token, refresh_token, client_id, client_secret, expiry). Không log secret."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or DEFAULT_TOKEN_FILE)

    def load(self) -> Tokens:
        if not self.path.exists():
            raise PlatformError(f"chưa đăng nhập YouTube: không có {self.path} — chạy `python -m studio.youtube login --client-secrets <file>`")
        d = json.loads(self.path.read_text(encoding="utf-8"))
        return Tokens(**{k: d.get(k, "" if k != "scopes" else []) for k in Tokens.__dataclass_fields__})

    def save(self, t: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Tạo file với mode 0600 ngay từ đầu (không có khoảnh khắc 0644 rồi mới chmod); file cũ thì chmod lại cho chắc.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(t.__dict__, ensure_ascii=False, indent=2))
        try: os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError: pass

    def status(self) -> dict[str, Any]:
        if not self.path.exists(): return {"path": str(self.path), "logged_in": False}
        t = self.load()
        return {"path": str(self.path), "logged_in": bool(t.refresh_token), "expiry": t.expiry, "expired": t.expired(),
                "scopes": t.scopes, "client_id": (t.client_id[:12] + "…") if t.client_id else ""}


def default_fetcher(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PlatformError(f"lỗi mạng {method} {url.split('?')[0]}: {getattr(e, 'reason', e)}") from e


def refresh_access_token(t: Tokens, fetcher: Fetcher = default_fetcher, now: datetime | None = None) -> Tokens:
    body = urllib.parse.urlencode({"client_id": t.client_id, "client_secret": t.client_secret, "refresh_token": t.refresh_token,
                                   "grant_type": "refresh_token"}).encode()
    st, _, raw = fetcher("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    if st != 200:
        raise PlatformError(f"refresh token thất bại HTTP {st}: {raw[:200].decode('utf-8', 'replace')} — đăng nhập lại", status=st)
    d = json.loads(raw)
    t.access_token = d["access_token"]
    t.expiry = ((now or datetime.now(UTC)) + timedelta(seconds=int(d.get("expires_in", 3600)))).isoformat()
    if d.get("scope"): t.scopes = d["scope"].split()
    return t


# ---------- YouTube ----------

_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(s: str) -> float:
    m = _DUR.fullmatch(s or "")
    if not m: return 0.0
    h, mi, se = (int(x or 0) for x in m.groups()); return float(h * 3600 + mi * 60 + se)


class YouTubePlatform:
    name = "youtube"

    def __init__(self, store: TokenStore, fetcher: Fetcher | None = None, now: Callable[[], datetime] | None = None,
                 sleep: Callable[[float], None] | None = None):
        self.store, self.fetcher = store, fetcher or default_fetcher
        self.now = now or (lambda: datetime.now(UTC))
        self.sleep = sleep or time.sleep  # test tiêm hàm giả để không chờ thật
        self._tokens: Tokens | None = None

    # --- auth + HTTP ---

    def _token(self, force_refresh: bool = False) -> str:
        t = self._tokens or self.store.load()
        if force_refresh or t.expired(self.now()) or not t.access_token:
            t = refresh_access_token(t, self.fetcher, self.now()); self.store.save(t)
        self._tokens = t; return t.access_token

    def _call(self, method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None,
              ok: tuple[int, ...] = (200, 201)) -> tuple[int, dict[str, str], bytes]:
        h = {"Authorization": f"Bearer {self._token()}", **(headers or {})}
        refreshed = False; err: PlatformError | None = None
        st, raw = 0, b""; rh: dict[str, str] = {}
        for attempt in range(MAX_ATTEMPTS):
            try:
                st, rh, raw = self.fetcher(method, url, h, body)
            except PlatformError as e:  # lỗi mạng/timeout từ fetcher → thử lại
                err = e
                st = 0
            else:
                if st == 401 and not refreshed:  # token vừa bị thu hồi/hết hạn sớm → refresh đúng một lần rồi thử lại
                    refreshed = True
                    h["Authorization"] = f"Bearer {self._token(force_refresh=True)}"
                    st, rh, raw = self.fetcher(method, url, h, body)
                if st not in RETRY_STATUSES:
                    break
                err = PlatformError(f"YouTube HTTP {st} tạm thời {method} {url.split('?')[0].rsplit('/', 1)[-1]}", status=st)
            if attempt + 1 < MAX_ATTEMPTS:  # backoff mũ + jitter: 0.5s, 1s (+ ≤ 0.25s ngẫu nhiên)
                self.sleep(BACKOFF_BASE_S * (2**attempt) + random.uniform(0, 0.25))
        else:
            assert err is not None
            raise err
        if st not in ok:
            msg = raw[:400].decode("utf-8", "replace")
            reason = None
            try:
                body_err = json.loads(raw)["error"]
                msg = body_err["message"]; reason = ((body_err.get("errors") or [{}])[0]).get("reason")
            except Exception: pass
            if st == 403:  # phân biệt hết quota (chờ reset/xin thêm) với thiếu quyền (đăng nhập lại đúng scope)
                reason = reason or ("quotaExceeded" if "quota" in msg.lower() else "forbidden")
                kind = f"quota ({reason})" if reason in QUOTA_REASONS else f"quyền ({reason})"
            else:
                kind = "lỗi"
            raise PlatformError(f"YouTube {kind} HTTP {st} {method} {url.split('?')[0].rsplit('/', 1)[-1]}: {msg}",
                                status=st, reason=reason)
        return st, rh, raw

    def _json(self, method: str, url: str, body: dict[str, Any] | None = None, ok: tuple[int, ...] = (200, 201)) -> tuple[int, dict[str, Any]]:
        st, _, raw = self._call(method, url, json.dumps(body).encode() if body is not None else None,
                                {"Content-Type": "application/json"} if body is not None else None, ok)
        return st, (json.loads(raw) if raw.strip() else {})

    # --- upload / thumbnail / lịch ---

    def upload_video(self, path: Path, metadata: MetadataPackage, privacy: str = "private", publish_at: str | None = None,
                     made_for_kids: bool = False) -> UploadResult:
        path = Path(path)
        if not path.exists(): raise PlatformError(f"không có file {path}")
        size = path.stat().st_size
        desc = metadata.description
        if metadata.chapters: desc += "\n\n" + "\n".join(f"{c.time} {c.label}" for c in metadata.chapters)
        snippet: dict[str, Any] = {"title": metadata.title[:100], "description": desc[:5000], "tags": metadata.tags[:50],
                                   "defaultLanguage": metadata.language, "defaultAudioLanguage": metadata.language}
        if metadata.category.isdigit(): snippet["categoryId"] = metadata.category
        status: dict[str, Any] = {"privacyStatus": "private" if publish_at else privacy, "selfDeclaredMadeForKids": made_for_kids}
        if publish_at: status["publishAt"] = publish_at
        st1, rh, _ = self._call("POST", f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
                                json.dumps({"snippet": snippet, "status": status}).encode(),
                                {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/*",
                                 "X-Upload-Content-Length": str(size)})
        loc = rh.get("location")
        if not loc: raise PlatformError(f"upload: không có Location sau bước 1 (HTTP {st1})", status=st1)
        data = path.read_bytes()
        st2, _, raw = self._call("PUT", loc, data, {"Content-Type": "video/*", "Content-Length": str(size)})
        v = json.loads(raw) if raw.strip() else {}
        vid = v.get("id")
        if not vid: raise PlatformError(f"upload: phản hồi không có id (HTTP {st2})", status=st2)
        ps = (v.get("status") or {}).get("privacyStatus", status["privacyStatus"])
        ev = json.dumps({"init": st1, "put": st2, "id": vid, "bytes": size, "privacy": ps, "publishAt": publish_at,
                         "uploadStatus": (v.get("status") or {}).get("uploadStatus")}, ensure_ascii=False)
        return UploadResult(vid, f"https://youtu.be/{vid}", "scheduled" if publish_at else ps, evidence=ev)

    def set_thumbnail(self, platform_ref: str, path: Path) -> str:
        path = Path(path); data = path.read_bytes()
        ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        st, _, _ = self._call("POST", f"{UPLOAD_URL.replace('/videos', '/thumbnails/set')}?videoId={urllib.parse.quote(platform_ref)}&uploadType=media",
                                data, {"Content-Type": ctype, "Content-Length": str(len(data))})
        return json.dumps({"thumbnails.set": st, "bytes": len(data), "file": path.name})

    def schedule(self, platform_ref: str, publish_at: str) -> str:
        st, d = self._json("PUT", f"{API_URL}/videos?part=status",
                           {"id": platform_ref, "status": {"privacyStatus": "private", "publishAt": publish_at}})
        return json.dumps({"videos.update": st, "privacy": (d.get("status") or {}).get("privacyStatus"),
                           "publishAt": (d.get("status") or {}).get("publishAt", publish_at)})

    # --- bình luận ---

    def list_comments(self, platform_ref: str, since: str | None = None) -> list[Comment]:
        out: list[Comment] = []; token: str | None = None
        for _ in range(20):  # ≤ 2000 bình luận một lần kéo
            q = {"part": "snippet", "videoId": platform_ref, "maxResults": "100", "order": "time", "textFormat": "plainText"}
            if token: q["pageToken"] = token
            _, d = self._json("GET", f"{API_URL}/commentThreads?{urllib.parse.urlencode(q)}")
            stop = False
            for it in d.get("items", []):
                top = (it.get("snippet") or {}).get("topLevelComment") or {}; sn = top.get("snippet") or {}
                c = Comment(top.get("id") or it.get("id", ""), sn.get("textOriginal") or sn.get("textDisplay", ""),
                            sn.get("authorDisplayName", ""), int(sn.get("likeCount", 0) or 0), sn.get("publishedAt", ""))
                if _before(c.published_at, since): stop = True; continue
                out.append(c)
            token = d.get("nextPageToken")
            if stop or not token: break
        return out

    def reply(self, comment_id: str, text: str) -> ReplyResult:
        st, d = self._json("POST", f"{API_URL}/comments?part=snippet", {"snippet": {"parentId": comment_id, "textOriginal": text}})
        rid = d.get("id", "")
        if not rid: raise PlatformError(f"reply: phản hồi không có id (HTTP {st})", status=st)
        return ReplyResult(rid, comment_id, f"reply:{rid}", evidence=json.dumps({"comments.insert": st, "id": rid, "parent": comment_id}))

    # --- số liệu ---

    def _report(self, video: str, metrics: str, start: str, end: str, dimensions: str | None = None) -> tuple[list[str], list[list[Any]]]:
        q = {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics, "filters": f"video=={video}"}
        if dimensions: q["dimensions"] = dimensions
        _, d = self._json("GET", f"{ANALYTICS_URL}?{urllib.parse.urlencode(q)}")
        return [h.get("name", "") for h in d.get("columnHeaders", [])], d.get("rows") or []

    def snapshot(self, platform_ref: str, window_days: int = 7, channel_id: str = "") -> SnapshotResult:
        end = self.now().date(); start = end - timedelta(days=max(1, window_days) - 1)
        s, e = start.isoformat(), end.isoformat()
        ev: dict[str, Any] = {"video": platform_ref, "window": [s, e], "source": "youtubeanalytics.reports.query"}
        snap = PerformanceSnapshot(video_id=platform_ref, channel_id=channel_id, window_days=window_days)
        # 1) tổng: views, phút xem, thời lượng xem TB, like, comment
        try:
            cols, rows = self._report(platform_ref, "views,estimatedMinutesWatched,averageViewDuration,likes,comments", s, e)
            row = dict(zip(cols, rows[0], strict=False)) if rows else {}
            snap.views = int(row.get("views", 0) or 0); snap.avg_view_duration_s = float(row.get("averageViewDuration", 0) or 0)
            snap.likes = int(row.get("likes", 0) or 0); snap.comments = int(row.get("comments", 0) or 0)
            ev["core"] = {"status": 200, "rows": len(rows), "estimatedMinutesWatched": row.get("estimatedMinutesWatched", 0)}
        except PlatformError as x:
            ev["core"] = {"error": str(x)[:200], "status": x.status}; ev["note"] = "core metrics lỗi → 0"
        # 2) impressions/CTR: API v2 không công bố metric này; thử, thất bại thì để 0 và nói rõ
        try:
            cols, rows = self._report(platform_ref, "impressions,impressionsClickThroughRate", s, e)
            row = dict(zip(cols, rows[0], strict=False)) if rows else {}
            snap.impressions = int(row.get("impressions", 0) or 0); snap.ctr = float(row.get("impressionsClickThroughRate", 0) or 0)
            ev["impressions"] = {"status": 200, "rows": len(rows)}
        except PlatformError as x:
            ev["impressions"] = {"error": str(x)[:160], "status": x.status, "note": "không có quyền/metric → impressions=0, ctr=0 (không bịa)"}
        # 3) retention: audienceWatchRatio theo elapsedVideoTimeRatio, đổi ra giây theo duration
        dur = 0.0
        try:
            _, d = self._json("GET", f"{API_URL}/videos?{urllib.parse.urlencode({'part': 'contentDetails', 'id': platform_ref})}")
            items = d.get("items") or []
            dur = parse_duration(((items[0].get("contentDetails") or {}).get("duration", "")) if items else "")
            ev["duration_s"] = dur
        except PlatformError as x:
            ev["duration"] = {"error": str(x)[:160], "status": x.status}
        try:
            cols, rows = self._report(platform_ref, "audienceWatchRatio", s, e, dimensions="elapsedVideoTimeRatio")
            ci, cw = cols.index("elapsedVideoTimeRatio"), cols.index("audienceWatchRatio")
            snap.retention_curve = [RetentionPoint(t=round(float(r[ci]) * (dur or 1.0), 2), pct=round(float(r[cw]) * 100, 2)) for r in rows]
            ev["retention"] = {"status": 200, "points": len(rows), "t_unit": "s" if dur else "ratio (không lấy được duration)"}
        except (PlatformError, ValueError) as x:
            ev["retention"] = {"error": str(x)[:160], "status": getattr(x, "status", None), "note": "retention_curve rỗng"}
        return SnapshotResult(snap, evidence=json.dumps(ev, ensure_ascii=False))
