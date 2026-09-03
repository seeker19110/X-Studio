"""CLI YouTube (ADR-0008): đăng nhập OAuth do NGƯỜI DÙNG tự chạy, xem tình trạng token, kéo bình luận và số liệu thật
lên bus. Code không bao giờ tạo credential; test không chạm mạng (`fetcher` tiêm được, hàm thuần cho URL/đổi mã).

  python -m studio.youtube login --client-secrets client_secret.json   # mở trình duyệt, loopback 127.0.0.1, lưu token 0600
  python -m studio.youtube status                                       # có token? hết hạn? scopes? (không in secret)
  python -m studio.youtube sync-comments CH1-V1 [--since ISO]           # commentThreads.list → audience-comments
  python -m studio.youtube sync-metrics CH1-V1 [--window 7]             # Analytics reports.query → performance-snapshots
`platform_ref` (id video trên YouTube) tra từ `publish-events` của video trong bus, hoặc truyền `--ref`.
"""
from __future__ import annotations

import argparse
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .events import AuditLog, Envelope
from .media import load_media_config
from .platform import (
    SCOPES,
    TOKEN_URL,
    Fetcher,
    Platform,
    PlatformError,
    Tokens,
    TokenStore,
    _token_path,
    default_fetcher,
    make_platform,
)

ACTOR = "adapter:youtube"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


# ---------- OAuth installed-app (hàm thuần, test được) ----------

def load_client_secrets(path: Path) -> tuple[str, str]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    sec = d.get("installed") or d.get("web") or d
    cid, csec = sec.get("client_id"), sec.get("client_secret")
    if not cid or not csec: raise PlatformError(f"{path}: không có client_id/client_secret (tải JSON OAuth 'Desktop app' từ Google Cloud Console)")
    return str(cid), str(csec)


def auth_url(client_id: str, redirect_uri: str, state: str, scopes: tuple[str, ...] = SCOPES) -> str:
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes),
         "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state}
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str, fetcher: Fetcher = default_fetcher,
                  now: datetime | None = None) -> Tokens:
    body = urllib.parse.urlencode({"code": code, "client_id": client_id, "client_secret": client_secret,
                                   "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    st, _, raw = fetcher("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    if st != 200: raise PlatformError(f"đổi mã thất bại HTTP {st}: {raw[:200].decode('utf-8', 'replace')}", status=st)
    d = json.loads(raw)
    if not d.get("refresh_token"): raise PlatformError("Google không trả refresh_token — thu hồi quyền ứng dụng ở myaccount.google.com/permissions rồi đăng nhập lại")
    exp = ((now or datetime.now(UTC)) + timedelta(seconds=int(d.get("expires_in", 3600)))).isoformat()
    return Tokens(access_token=d["access_token"], refresh_token=d["refresh_token"], client_id=client_id, client_secret=client_secret,
                  expiry=exp, scopes=(d.get("scope") or " ".join(SCOPES)).split())


def _wait_for_code(port: int, state: str, timeout_s: float = 300.0) -> str:
    """HTTP server loopback nhận ?code=&state= một lần. Chỉ chạy trong `login` (người dùng)."""
    got: dict[str, str] = {}; done = threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state and q.get("code")
            if ok: got["code"] = q["code"][0]
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write(("<h3>Đăng nhập YouTube xong, đóng tab này.</h3>" if ok else "<h3>Thiếu code/state sai.</h3>").encode())
            done.set()
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        if not done.wait(timeout_s) or "code" not in got: raise PlatformError("không nhận được mã OAuth (hết giờ hoặc state sai)")
        return got["code"]
    finally:
        srv.shutdown(); srv.server_close()


def login(client_secrets: Path, store: TokenStore, port: int = 8765, fetcher: Fetcher = default_fetcher, open_browser: bool = True) -> Tokens:
    cid, csec = load_client_secrets(client_secrets)
    redirect = f"http://127.0.0.1:{port}/"; state = secrets.token_urlsafe(16)
    url = auth_url(cid, redirect, state)
    print(f"Mở trình duyệt để cấp quyền ({', '.join(s.rsplit('/', 1)[-1] for s in SCOPES)}). Nếu không tự mở, dán URL:\n{url}")
    if open_browser: webbrowser.open(url)
    code = _wait_for_code(port, state)
    t = exchange_code(code, cid, csec, redirect, fetcher); store.save(t)
    print(f"Đã lưu token vào {store.path} (hết hạn access {t.expiry}; refresh_token có).")
    return t


# ---------- kéo dữ liệu thật lên bus ----------

def find_ref(bus: Any, video_id: str) -> str | None:
    for e in reversed(list(bus.replay("publish-events", video_id))):
        if e.payload.get("kind", "video") == "video" and e.payload.get("platform_ref") and e.payload.get("status") in {"scheduled", "published"}:
            return str(e.payload["platform_ref"])
    return None


def find_channel(bus: Any, video_id: str) -> str:
    for e in bus.replay("video-briefs", video_id):
        if e.payload.get("channel_id"): return str(e.payload["channel_id"])
    return ""


def _audit(bus: Any, action: str, video_id: str, data: dict[str, Any]) -> None:
    a = AuditLog(actor=ACTOR, action=action, video_id=video_id, evidence=json.dumps(data, ensure_ascii=False, default=str)[:4000])
    bus.publish(Envelope(topic="audit-log", key=ACTOR, actor=ACTOR, payload=a.model_dump()))


def seen_comment_ids(bus: Any, video_id: str) -> set[str]:
    """comment_id đã lên bus (`audience-comments`) hoặc đã được trả lời (`publish-events` kind=reply) — kéo lại không tạo lô mới."""
    seen: set[str] = set()
    for e in bus.replay("audience-comments", video_id):
        seen.update(str(c.get("comment_id")) for c in e.payload.get("comments", []) if c.get("comment_id"))
    for e in bus.replay("publish-events", video_id):
        if e.payload.get("kind") == "reply" and e.payload.get("comment_id"):
            seen.add(str(e.payload["comment_id"]))
    return seen


def sync_comments(bus: Any, platform: Platform, video_id: str, ref: str | None = None, since: str | None = None) -> Envelope | None:
    ref = ref or find_ref(bus, video_id)
    if not ref: raise PlatformError(f"{video_id}: chưa có platform_ref (chưa upload?) — truyền --ref")
    seen = seen_comment_ids(bus, video_id)
    got = platform.list_comments(ref, since=since)
    cs = [c for c in got if c.comment_id not in seen]
    _audit(bus, "platform.comments", video_id, {"platform": platform.name, "platform_ref": ref, "since": since,
                                                "count": len(cs), "skipped": len(got) - len(cs)})
    if not cs: return None
    payload = {"video_id": video_id, "platform_ref": ref, "comments": [
        {"comment_id": c.comment_id, "author": c.author, "text": c.text, "likes": c.likes, "published_at": c.published_at} for c in cs]}
    return bus.publish(Envelope(topic="audience-comments", key=video_id, actor=ACTOR, payload=payload))


def sync_metrics(bus: Any, platform: Platform, video_id: str, ref: str | None = None, window_days: int = 7,
                 channel_id: str | None = None, variant_id: str | None = None) -> Envelope:
    ref = ref or find_ref(bus, video_id)
    if not ref: raise PlatformError(f"{video_id}: chưa có platform_ref (chưa upload?) — truyền --ref")
    r = platform.snapshot(ref, window_days, channel_id=channel_id or find_channel(bus, video_id) or "-")
    snap = r.snapshot.model_copy(update={"video_id": video_id, "variant_id": variant_id})
    _audit(bus, "platform.snapshot", video_id, {"platform": platform.name, "platform_ref": ref, "window_days": window_days, "evidence": r.evidence,
                                              "views": snap.views, "impressions": snap.impressions, "retention_points": len(snap.retention_curve)})
    return bus.publish(Envelope(topic="performance-snapshots", key=video_id, actor=ACTOR, payload=snap.model_dump()))


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="YouTube: login | status | sync-comments | sync-metrics")
    ap.add_argument("--db", type=Path, default=Path("studio.sqlite"))
    ap.add_argument("--tokens", type=Path, default=None, help="file token (mặc định ~/.x-agents/auth/youtube_tokens.json hoặc STUDIO_YOUTUBE_TOKENS)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("login"); lg.add_argument("--client-secrets", type=Path, required=True); lg.add_argument("--port", type=int, default=8765)
    lg.add_argument("--no-browser", action="store_true")
    sub.add_parser("status")
    sc = sub.add_parser("sync-comments"); sc.add_argument("video_id"); sc.add_argument("--ref", default=None); sc.add_argument("--since", default=None)
    sm = sub.add_parser("sync-metrics"); sm.add_argument("video_id"); sm.add_argument("--ref", default=None); sm.add_argument("--window", type=int, default=7)
    sm.add_argument("--channel", default=None); sm.add_argument("--variant", default=None)
    ns = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_media_config(); store = TokenStore(ns.tokens or _token_path(cfg))
    try:
        if ns.cmd == "login":
            login(ns.client_secrets, store, port=ns.port, open_browser=not ns.no_browser); return 0
        if ns.cmd == "status":
            print(json.dumps({"provider": cfg.platform.get("provider", "fake"), **store.status()}, ensure_ascii=False, indent=2)); return 0
        from .sqlite_bus import SQLiteBus
        bus = SQLiteBus(ns.db); platform = make_platform(cfg)
        if ns.cmd == "sync-comments":
            env = sync_comments(bus, platform, ns.video_id, ref=ns.ref, since=ns.since)
            print(f"{platform.name}: {len(env.payload['comments']) if env else 0} bình luận" + (f" → audience-comments event={env.event_id}" if env else " (không có gì mới)"))
        elif ns.cmd == "sync-metrics":
            env = sync_metrics(bus, platform, ns.video_id, ref=ns.ref, window_days=ns.window, channel_id=ns.channel, variant_id=ns.variant)
            p = env.payload
            print(f"{platform.name}: views={p['views']} impressions={p['impressions']} ctr={p['ctr']} avd={p['avg_view_duration_s']}s "
                  f"retention={len(p['retention_curve'])} điểm → performance-snapshots event={env.event_id}")
        return 0
    except PlatformError as e:
        print(f"lỗi: {e}", file=sys.stderr); return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
