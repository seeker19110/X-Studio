"""Local-first production console. No Node build, database service or CDN required."""
from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .factory import Factory
from .media import MediaConfig, load_media_config

STATIC = Path(__file__).with_name("web")


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], factory: Factory, token: str):
        self.factory = factory
        self.token = token
        super().__init__(address, ConsoleHandler)


class ConsoleHandler(BaseHTTPRequestHandler):
    server: ConsoleServer

    def log_message(self, *_args) -> None:
        pass  # URLs/cookies/credentials never enter access logs

    def send(self, status: int, data: bytes, mime: str = "application/json", headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'")
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def json(self, status: int, data: dict, headers: dict | None = None) -> None:
        self.send(status, json.dumps(data, ensure_ascii=False).encode(), headers=headers)

    def authorized(self) -> bool:
        bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get("Cookie", ""))
        except Exception: return False
        session = cookie.get("studio_session")
        return hmac.compare_digest(bearer, self.server.token) or bool(session and hmac.compare_digest(session.value, self.server.token))

    def same_origin(self) -> bool:
        host = self.headers.get("Host", "")
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in expected: return False
        origin = self.headers.get("Origin")
        return not origin or origin == "http://" + host

    def do_GET(self) -> None:
        if not self.same_origin(): self.json(403, {"error": "Invalid origin"}); return
        path = urlsplit(self.path).path
        static = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        if path in static:
            file = STATIC / static[path]
            self.send(200, file.read_bytes(), mimetypes.guess_type(file)[0] or "text/plain"); return
        if not self.authorized(): self.json(401, {"error": "Nhập mã truy cập xưởng"}); return
        if path == "/api/state": self.json(200, self.server.factory.snapshot()); return
        if path.startswith("/media/"):
            media_file = self.server.factory.media_path(path.removeprefix("/media/"))
            if media_file is not None:
                self.media(media_file); return
        self.json(404, {"error": "Không tìm thấy"})

    def media(self, file: Path) -> None:
        length = file.stat().st_size
        start, end = 0, length - 1
        partial = self.headers.get("Range")
        if partial:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", partial)
            if not match: self.json(416, {"error": "Invalid range"}); return
            start = int(match[1]); end = min(int(match[2]) if match[2] else end, end)
            if start > end or start >= length: self.json(416, {"error": "Range outside file"}); return
        self.send_response(206 if partial else 200)
        safe_type = file.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".wav", ".mp3", ".srt"}
        self.send_header("Content-Type", (mimetypes.guess_type(file)[0] or "application/octet-stream") if safe_type else "application/octet-stream")
        if not safe_type: self.send_header("Content-Disposition", "attachment")
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("Content-Length", str(end-start+1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if partial: self.send_header("Content-Range", f"bytes {start}-{end}/{length}")
        self.end_headers()
        with file.open("rb") as stream:
            stream.seek(start); remaining = end-start+1
            while remaining:
                chunk = stream.read(min(65536, remaining))
                if not chunk: break
                self.wfile.write(chunk); remaining -= len(chunk)

    def do_POST(self) -> None:
        if not self.same_origin(): self.json(403, {"error": "Invalid origin"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536: raise ValueError("request size must be 1–65536 bytes")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict): raise ValueError("JSON object required")
            if self.path == "/api/login":
                if not hmac.compare_digest(str(body.get("token", "")), self.server.token):
                    self.json(401, {"error": "Mã truy cập không đúng"}); return
                self.json(200, {"ok": True}, {"Set-Cookie": f"studio_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/"}); return
            if not self.authorized(): self.json(401, {"error": "Authentication required"}); return
            if self.headers.get("X-Studio-Request") != "1": self.json(403, {"error": "Missing request header"}); return
            if self.path != "/api/commands": self.json(404, {"error": "Unknown endpoint"}); return
            id = self.server.factory.command(str(body["kind"]), body.get("data", {}))
            self.json(202, {"id": id})
        except (ValueError, KeyError, TypeError) as exc:
            self.json(400, {"error": str(exc)[:300]})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="X-Studio production console")
    parser.add_argument("--db", type=Path, default=Path("studio.sqlite"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--media", type=Path, help="media profile; default media.yaml")
    parser.add_argument("--demo", action="store_true", help="offline simulation, fake media and platform")
    args = parser.parse_args(argv)
    cfg = MediaConfig(output_dir=Path("output/demo"), production={"automation": "production"}) if args.demo else load_media_config(args.media)
    factory = Factory(args.db, cfg, demo=args.demo)
    token = os.environ.get("STUDIO_CONSOLE_TOKEN") or secrets.token_urlsafe(24)
    try: server = ConsoleServer(("127.0.0.1", args.port), factory, token)
    except OSError:
        factory.close(); raise
    worker = threading.Thread(target=factory.work, daemon=True); worker.start()
    print(f"X-Studio: http://127.0.0.1:{server.server_port}")
    if not os.environ.get("STUDIO_CONSOLE_TOKEN"): print(f"Access code: {token}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        factory.stop.set(); worker.join()  # finish current stage before releasing the single-writer lease
        server.server_close(); factory.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
