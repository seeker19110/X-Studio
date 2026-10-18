"""Adapter nền tảng phân phối (ADR-0008): upload, thumbnail, lên lịch, bình luận, trả lời, số liệu.

Chỉ CODE gọi lớp này (orchestrator sau gate `publish`/`replies`, CLI `studio.youtube sync-*`); không model nào chạm tới.
`Platform` là interface trung lập nền tảng; `FakePlatform` chạy offline (test/demo, mặc định); `YouTubePlatform` gọi
YouTube Data API v3 + YouTube Analytics API v2 bằng `urllib` thuần qua `fetcher` tiêm được — test không chạm mạng.
Mọi kết quả kèm `evidence` (endpoint, HTTP status, id trả về) để audit-log có bằng chứng; số liệu là số thật từ API,
thiếu quyền thì để 0 và nói rõ trong evidence.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import re
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from .events import MetadataPackage, PerformanceSnapshot, RetentionPoint
from .media import PLATFORM_THUMBNAIL_MAX_BYTES, MediaConfig, load_media_config
from .upload_sessions import UploadSessions

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
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024  # 32 × 256 KiB, đúng hạn mức chunk của giao thức YouTube
# reason trong error.errors[].reason của Google API cho biết 403 là hết quota hay thiếu quyền — xử lý khác nhau
QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"})

Fetcher = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]


def _multipart_related(meta: dict[str, Any], data: bytes, ctype: str) -> tuple[bytes, str]:
    """Thân multipart/related của Google upload API: phần JSON rồi phần nhị phân, cùng một boundary."""
    boundary = "studio-" + os.urandom(12).hex()
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + json.dumps(meta, ensure_ascii=False).encode("utf-8")
            + f"\r\n--{boundary}\r\nContent-Type: {ctype}\r\n\r\n".encode() + data
            + f"\r\n--{boundary}--\r\n".encode())
    return body, f"multipart/related; boundary={boundary}"


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
    has_image: bool = False  # YouTube 11/2026 `snippet.imageUrl`; chỉ giữ cờ, ảnh/URL không vào bus (ADR-0007)


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
    def upload_captions(self, platform_ref: str, path: Path, language: str = "vi", name: str = "") -> str: ...
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
        self.videos[ref] = {"title": metadata.title, "privacy": privacy, "publish_at": publish_at, "thumbnail": None, "path": str(path),
                            "synthetic_media": metadata.synthetic_media,
                            "paid_product_placement": metadata.paid_product_placement}
        return UploadResult(ref, f"https://fake.video/{ref}", "scheduled" if publish_at else privacy,
                            evidence=f"fake upload {ref} bytes={Path(path).stat().st_size} synthetic_media={metadata.synthetic_media} "
                                     f"paid_product_placement={metadata.paid_product_placement}")

    def set_thumbnail(self, platform_ref: str, path: Path) -> str:
        self._check("set_thumbnail", platform_ref=platform_ref, path=str(path))
        self.videos[platform_ref]["thumbnail"] = str(path); return f"fake thumbnail {platform_ref} ← {Path(path).name}"

    def upload_captions(self, platform_ref: str, path: Path, language: str = "vi", name: str = "") -> str:
        self._check("upload_captions", platform_ref=platform_ref, path=str(path), language=language)
        if not Path(path).exists(): raise PlatformError(f"không có file phụ đề {path}")
        self.videos[platform_ref]["captions"] = str(path)
        return f"fake captions {platform_ref} ← {Path(path).name} ({language})"

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
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(t.__dict__, ensure_ascii=False, indent=2))
            with contextlib.suppress(OSError):
                os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temp_path, self.path)
        finally:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)

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
                 sleep: Callable[[float], None] | None = None, sessions: UploadSessions | None = None):
        self.store, self.fetcher = store, fetcher or default_fetcher
        self.now = now or (lambda: datetime.now(UTC))
        self.sleep = sleep or time.sleep  # test tiêm hàm giả để không chờ thật
        self._tokens: Tokens | None = None
        self.sessions = sessions

    # --- auth + HTTP ---

    def _token(self, force_refresh: bool = False) -> str:
        t = self._tokens or self.store.load()
        if force_refresh or t.expired(self.now()) or not t.access_token:
            t = refresh_access_token(t, self.fetcher, self.now()); self.store.save(t)
        self._tokens = t; return t.access_token

    def _call(self, method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None,
              ok: tuple[int, ...] = (200, 201), *, attempts: int = MAX_ATTEMPTS) -> tuple[int, dict[str, str], bytes]:
        h = {"Authorization": f"Bearer {self._token()}", **(headers or {})}
        refreshed = False; err: PlatformError | None = None
        st, raw = 0, b""; rh: dict[str, str] = {}
        for attempt in range(attempts):
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
            if attempt + 1 < attempts:  # backoff mũ + jitter: 0.5s, 1s (+ ≤ 0.25s ngẫu nhiên)
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

    def _upload_chunks(self, video: BinaryIO, location: str, size: int, offset: int = 0,
                       progress: Callable[[int], None] | None = None,
                       before_chunk: Callable[[int], None] | None = None) -> tuple[int, dict[str, str], bytes]:
        """Gửi ≤ 8 MiB/lượt; sau lỗi vận chuyển hỏi offset thật, không đoán chunk đã được nhận hay chưa."""
        failures = 0
        while True:
            video.seek(offset)
            data = video.read(min(UPLOAD_CHUNK_BYTES, size - offset))
            if not data: raise PlatformError("upload: file đã thay đổi hoặc bị cắt ngắn trong khi gửi")
            end = offset + len(data)
            if before_chunk: before_chunk(end)
            last_error: PlatformError | None = None
            try:
                result = self._session_call("PUT", location, data,
                                    {"Content-Type": "video/*", "Content-Length": str(len(data)),
                                     "Content-Range": f"bytes {offset}-{end - 1}/{size}"},
                                    ok=(200, 201, 308), attempts=1)
            except PlatformError as error:
                if error.status is not None and error.status not in RETRY_STATUSES: raise
                last_error = error
                failures += 1
                self.sleep(BACKOFF_BASE_S * (2 ** (failures - 1)) + random.uniform(0, 0.25))
                result = self._session_call("PUT", location, b"", {"Content-Length": "0", "Content-Range": f"bytes */{size}"},
                                    ok=(200, 201, 308))
            status, headers, _raw = result
            if status != 308:
                if end < size: raise PlatformError("upload: máy chủ báo hoàn tất trước khi nhận đủ file", status=status)
                return result
            received = headers.get("range")
            match = re.fullmatch(r"bytes=0-([0-9]+)", received) if received else None
            if received and match is None: raise PlatformError("upload: Range không hợp lệ từ máy chủ", status=308)
            next_offset = int(match.group(1)) + 1 if match else 0
            if not offset <= next_offset <= end:
                raise PlatformError("upload: Range đi lùi hoặc vượt số byte đã gửi", status=308)
            if next_offset == size: raise PlatformError("upload: thiếu xác nhận hoàn tất sau byte cuối", status=308)
            if next_offset == offset:
                if last_error is None: failures += 1
                if failures >= MAX_ATTEMPTS:
                    if last_error is not None: raise last_error
                    raise PlatformError("upload: máy chủ không nhận thêm byte sau 3 lượt", status=308)
            else:
                failures = 0
            if retry_after := headers.get("retry-after"):
                with contextlib.suppress(ValueError):
                    self.sleep(max(0, float(retry_after)))
            offset = next_offset
            if progress: progress(offset)

    def upload_video(self, path: Path, metadata: MetadataPackage, privacy: str = "private", publish_at: str | None = None,
                     made_for_kids: bool = False, *, operation_id: str | None = None, authorization: str = '') -> UploadResult:
        path = Path(path)
        if not path.exists(): raise PlatformError(f"không có file {path}")
        # Ghim file trước khi POST mở phiên: thay tên file nguyên tử trong lúc chờ HTTP không được đổi nội dung gửi.
        with path.open("rb") as video:
            size = os.fstat(video.fileno()).st_size
            if not size: raise PlatformError("upload: file video rỗng")
            desc = metadata.description
            if metadata.chapters: desc += "\n\n" + "\n".join(f"{c.time} {c.label}" for c in metadata.chapters)
            snippet: dict[str, Any] = {"title": metadata.title[:100], "description": desc[:5000], "tags": metadata.tags[:50],
                                       "defaultLanguage": metadata.language, "defaultAudioLanguage": metadata.language}
            if metadata.category.isdigit(): snippet["categoryId"] = metadata.category
            status: dict[str, Any] = {"privacyStatus": "private" if publish_at else privacy, "selfDeclaredMadeForKids": made_for_kids}
            if publish_at: status["publishAt"] = publish_at
            # ADR-0018: chỉ gửi khi đã quyết (code/người); bỏ trống thì YouTube tự xét và gắn nhãn (5/2026).
            if metadata.synthetic_media is not None: status["containsSyntheticMedia"] = bool(metadata.synthetic_media)
            body: dict[str, Any] = {"snippet": snippet, "status": status}
            # Tài trợ trả tiền (7/2026) là part riêng; chỉ gửi khi kênh đã khai, bỏ trống = YouTube coi như không khai.
            if metadata.paid_product_placement is not None:
                body["paidProductPlacementDetails"] = {"hasPaidProductPlacement": bool(metadata.paid_product_placement)}
            if self.sessions is not None and operation_id is not None:
                return self._persistent_upload(video, size, snippet, status, operation_id, authorization, publish_at, body)
            st1, rh, _ = self._call("POST", f"{UPLOAD_URL}?uploadType=resumable&part={','.join(body)}",
                                    json.dumps(body).encode(),
                                    {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/*",
                                     "X-Upload-Content-Length": str(size)})
            loc = rh.get("location")
            if not loc: raise PlatformError(f"upload: không có Location sau bước 1 (HTTP {st1})", status=st1)
            st2, _, raw = self._upload_chunks(video, loc, size)
        v = json.loads(raw) if raw.strip() else {}
        vid = v.get("id")
        if not vid: raise PlatformError(f"upload: phản hồi không có id (HTTP {st2})", status=st2)
        ps = (v.get("status") or {}).get("privacyStatus", status["privacyStatus"])
        ev = json.dumps({"init": st1, "put": st2, "id": vid, "bytes": size, "privacy": ps, "publishAt": publish_at,
                         "uploadStatus": (v.get("status") or {}).get("uploadStatus")}, ensure_ascii=False)
        return UploadResult(vid, f"https://youtu.be/{vid}", "scheduled" if publish_at else ps, evidence=ev)

    def _persistent_upload(self, video: BinaryIO, size: int, snippet: dict, status: dict,
                           operation_id: str, authorization: str, publish_at: str | None, body: dict) -> UploadResult:
        sessions = self.sessions
        assert sessions is not None
        checksum = hashlib.sha256()
        for block in iter(lambda:video.read(1024*1024),b''): checksum.update(block)
        identity = {'sha256': checksum.hexdigest(), 'size':size,
                    'metadata':hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
                    'authorization':authorization}
        video.seek(0)
        saved = sessions.get(operation_id)
        if saved and (saved.get('identity') != identity or saved.get('state') == 'needs_reconcile'):
            raise PlatformError('Upload cần đối chiếu: file, metadata hoặc phiên duyệt đã thay đổi; không tự tạo upload mới')
        if saved and saved.get('result'): return UploadResult(**saved['result'])
        location = saved.get('location') if saved else None
        result = None; offset = 0
        if location:
            self._session_origin(location)
            try:
                result = self._session_call('PUT', location, b'', {'Content-Length':'0','Content-Range':f'bytes */{size}'}, ok=(200,201,308))
            except PlatformError as exc:
                if exc.status != 404: raise
                if saved and saved.get('final_attempted') is not False:
                    saved['state'] = 'needs_reconcile'; sessions.save(operation_id,saved)
                    raise PlatformError('Upload cần đối chiếu YouTube: đã thử gửi chunk cuối nhưng session không còn; không tự upload lại') from None
                location = None
            if result and result[0] == 308:
                received = result[1].get('range', '')
                match = re.fullmatch(r'bytes=0-([0-9]+)', received)
                if received and not match: raise PlatformError('upload: invalid persisted session Range')
                offset = int(match[1]) + 1 if match else 0
                if not 0 <= offset < size: raise PlatformError('upload: persisted session offset requires reconciliation')
                result = None
        if not location:
            _, headers, _ = self._call('POST', f'{UPLOAD_URL}?uploadType=resumable&part={",".join(body)}',
                         json.dumps(body).encode(),
                         {'Content-Type':'application/json; charset=UTF-8', 'X-Upload-Content-Type':'video/*',
                          'X-Upload-Content-Length':str(size)})
            location = headers.get('location')
            if not location: raise PlatformError('upload: missing session Location')
            self._session_origin(location)
        record = {'identity':identity, 'location':location, 'offset':offset, 'state':'uploading','final_attempted':False}
        sessions.save(operation_id, record)
        def before_chunk(end: int) -> None:
            if end == size:
                record['final_attempted'] = True
                sessions.save(operation_id,record)
        def progress(acknowledged: int) -> None:
            record.update(offset=acknowledged,final_attempted=False)
            sessions.save(operation_id, record)
        if result is None: result = self._upload_chunks(video, location, size, offset=offset, progress=progress,before_chunk=before_chunk)
        try: raw = json.loads(result[2])
        except ValueError as exc: raise PlatformError('upload: completion requires reconciliation, invalid response') from exc
        if not isinstance(raw,dict): raise PlatformError('upload: completion requires reconciliation, invalid response')
        vid = raw.get('id')
        if not vid: raise PlatformError('upload: completion requires reconciliation, missing video id')
        remote_status = raw.get('status',{})
        if not isinstance(remote_status,dict): raise PlatformError('upload: completion requires reconciliation, invalid status')
        privacy = remote_status.get('privacyStatus', status['privacyStatus'])
        completed = UploadResult(vid, f'https://youtu.be/{vid}', 'scheduled' if publish_at else privacy,
                                 evidence=json.dumps({'id':vid,'bytes':size,'privacy':privacy,'resumed':bool(saved)}))
        record.update(state='completed', result=completed.__dict__, offset=size)
        sessions.save(operation_id, record)
        return completed

    @staticmethod
    def _session_origin(location: str) -> None:
        try:
            parsed = urllib.parse.urlsplit(location)
            valid = (parsed.scheme == 'https' and parsed.hostname == 'www.googleapis.com' and parsed.port in {None,443}
                     and not parsed.username and not parsed.password and not parsed.fragment)
        except ValueError: valid = False
        if not valid: raise PlatformError('upload: invalid session origin')

    def _session_call(self, method: str, location: str, body: bytes, headers: dict[str,str],
                      ok: tuple[int,...] = (200,201), *, attempts: int = MAX_ATTEMPTS) -> tuple[int,dict[str,str],bytes]:
        try: return self._call(method,location,body,headers,ok,attempts=attempts)
        except PlatformError as exc:
            raise PlatformError(f'YouTube upload session HTTP {exc.status or "network"}; thử lại hoặc đối chiếu phiên upload',
                                status=exc.status,reason=exc.reason) from None

    def set_thumbnail(self, platform_ref: str, path: Path) -> str:
        path = Path(path); data = path.read_bytes()
        # Giới hạn nền tảng (50 MB từ 14/9/2026; trước đó 2 MB — nay 2 MB chỉ là mục tiêu của renderer, QC warn). Bắt tại
        # đây để evidence nói đúng nguyên nhân và người duyệt thấy ngay phải làm gì, thay vì đọc HTTP 400 của Google.
        if len(data) > PLATFORM_THUMBNAIL_MAX_BYTES:
            raise PlatformError(f"thumbnail {path.name} nặng {len(data) / 1_000_000:.1f} MB, YouTube chỉ nhận "
                                f"≤ {PLATFORM_THUMBNAIL_MAX_BYTES // 1_000_000} MB (renderer nén lại, hoặc dùng file nhẹ hơn)")
        ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        st, _, _ = self._call("POST", f"{UPLOAD_URL.replace('/videos', '/thumbnails/set')}?videoId={urllib.parse.quote(platform_ref)}&uploadType=media",
                                data, {"Content-Type": ctype, "Content-Length": str(len(data))})
        return json.dumps({"thumbnails.set": st, "bytes": len(data), "file": path.name})

    def upload_captions(self, platform_ref: str, path: Path, language: str = "vi", name: str = "") -> str:
        """captions.insert dạng multipart/related: phần JSON mô tả rãnh phụ đề, phần hai là nội dung SRT."""
        path = Path(path); data = path.read_bytes()
        meta = {"snippet": {"videoId": platform_ref, "language": language, "name": name or "", "isDraft": False}}
        body, ctype = _multipart_related(meta, data, "application/octet-stream")
        st, _, raw = self._call("POST", f"{UPLOAD_URL.replace('/videos', '/captions')}?part=snippet&uploadType=multipart",
                                body, {"Content-Type": ctype, "Content-Length": str(len(body))})
        cid = (json.loads(raw) if raw.strip() else {}).get("id")
        return json.dumps({"captions.insert": st, "id": cid, "bytes": len(data), "language": language, "file": path.name})

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
                            sn.get("authorDisplayName", ""), int(sn.get("likeCount", 0) or 0), sn.get("publishedAt", ""),
                            has_image=bool(sn.get("imageUrl")))
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

    def _duration(self, platform_ref: str, ev: dict[str, Any]) -> float:
        """Thời lượng video để đổi retention ra giây. `videos:batchGetStats` (6/2026) trước: 1 đơn vị ở rổ riêng, tới
        mili-giây; kèm số đếm công khai TRỌN ĐỜI → chỉ ghi evidence (khác nghĩa với số theo cửa sổ của Analytics).
        Lỗi hoặc không có video trong kết quả → `videos.list` như trước."""
        try:
            q = urllib.parse.urlencode({"part": "contentDetails,statistics", "id": platform_ref})
            _, d = self._json("GET", f"{API_URL}/videos:batchGetStats?{q}")
            item = next((i for i in d.get("items") or [] if i.get("id") == platform_ref), None)
            if item:
                cd, st = item.get("contentDetails") or {}, item.get("statistics") or {}
                ms = cd.get("durationMillis")
                dur = round(int(ms) / 1000, 3) if ms else parse_duration(cd.get("duration", ""))
                ev.update(duration_s=dur, duration_source="videos.batchGetStats",
                          public_lifetime={k: int(st.get(f, 0) or 0) for k, f in
                                           (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount"))})
                return dur
            ev["batch_stats"] = {"status": 200, "note": "video không có trong kết quả"}
        except PlatformError as x:
            ev["batch_stats"] = {"error": str(x)[:160], "status": x.status}
        try:
            _, d = self._json("GET", f"{API_URL}/videos?{urllib.parse.urlencode({'part': 'contentDetails', 'id': platform_ref})}")
            items = d.get("items") or []
            dur = parse_duration(((items[0].get("contentDetails") or {}).get("duration", "")) if items else "")
            ev.update(duration_s=dur, duration_source="videos.list")
            return dur
        except PlatformError as x:
            ev["duration"] = {"error": str(x)[:160], "status": x.status}
            return 0.0

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
        dur = self._duration(platform_ref, ev)
        try:
            cols, rows = self._report(platform_ref, "audienceWatchRatio", s, e, dimensions="elapsedVideoTimeRatio")
            ci, cw = cols.index("elapsedVideoTimeRatio"), cols.index("audienceWatchRatio")
            snap.retention_curve = [RetentionPoint(t=round(float(r[ci]) * (dur or 1.0), 2), pct=round(float(r[cw]) * 100, 2)) for r in rows]
            ev["retention"] = {"status": 200, "points": len(rows), "t_unit": "s" if dur else "ratio (không lấy được duration)"}
        except (PlatformError, ValueError) as x:
            ev["retention"] = {"error": str(x)[:160], "status": getattr(x, "status", None), "note": "retention_curve rỗng"}
        return SnapshotResult(snap, evidence=json.dumps(ev, ensure_ascii=False))
