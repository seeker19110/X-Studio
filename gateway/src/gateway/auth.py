"""Xác thực Google Antigravity OAuth và quản lý pool tài khoản.

Đảm nhiệm:
- Google OAuth 2.0 PKCE qua trình duyệt (`login_pkce`), nhiều tài khoản trong một file.
- Lưu token bền vững ở `<XAGENTS_HOME>/auth/antigravity_tokens.json`, tự làm mới khi hết hạn.
- Pool tài khoản có cooldown từng tài khoản: 401/402/403/429 → tạm loại khỏi vòng xoay,
  tôn trọng `Retry-After`; lỗi mạng khi refresh thì chỉ bỏ qua lượt này, KHÔNG cooldown.
- Tìm Google Cloud project id cho backend Code Assist.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class UpstreamError(RuntimeError):
    """Lỗi từ upstream (Google Code Assist) kèm mã HTTP thật, để server trả đúng status
    cho client (429 khi hết quota → client phía trên có thể fallback tiếp)."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


ENV_HOME = "XAGENTS_HOME"
ENV_CLIENT_ID = "GATEWAY_ANTIGRAVITY_CLIENT_ID"
ENV_CLIENT_SECRET = "GATEWAY_ANTIGRAVITY_CLIENT_SECRET"
ENV_PROJECT_ID = "GATEWAY_ANTIGRAVITY_PROJECT_ID"

# OAuth client công khai của Antigravity IDE (giống mọi client desktop của Google: không phải bí mật).
_PUBLIC_CLIENT_ID_PROJECT_NUM = "1071006060591"
_PUBLIC_CLIENT_ID_HASH = "tmhssin2h21lcre235vtolojh4g403ep"
_PUBLIC_CLIENT_SECRET_SUFFIX = "K58FWR486LdLJ1mLB8sXC4z6qDAf"
DEFAULT_CLIENT_ID = f"{_PUBLIC_CLIENT_ID_PROJECT_NUM}-{_PUBLIC_CLIENT_ID_HASH}.apps.googleusercontent.com"
DEFAULT_CLIENT_SECRET = f"GOCSPX-{_PUBLIC_CLIENT_SECRET_SUFFIX}"
DEFAULT_PROJECT_ID = "aicode-consumers"

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v1/userinfo"
LOAD_CODE_ASSIST_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"

OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile "
    "https://www.googleapis.com/auth/cclog "
    "https://www.googleapis.com/auth/experimentsandconfigs"
)

DEFAULT_REDIRECT_PORT = 51121
REDIRECT_HOST = "localhost"
CALLBACK_PATH = "/oauth-callback"
REFRESH_SKEW_SECONDS = 120

# Cooldown mặc định theo mã lỗi (giây). 401: token hỏng, chờ ngắn rồi refresh lại;
# 402/403/429: hết quota/bị chặn, chờ 1 giờ trừ khi upstream nói `Retry-After`.
COOLDOWN_DEFAULTS = {401: 300, 402: 3600, 403: 3600, 429: 3600}
COOLDOWN_FALLBACK = 60


def get_home_dir() -> Path:
    """Thư mục dữ liệu của gateway: `$XAGENTS_HOME`, mặc định `~/.x-agents`."""
    custom = os.getenv(ENV_HOME)
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".x-agents"


def get_gateway_dir() -> Path:
    d = get_home_dir() / "gateway"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_token_file() -> Path:
    return get_home_dir() / "auth" / "antigravity_tokens.json"


@dataclass
class AntigravityCredentials:
    """Một tài khoản Google trong pool."""

    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0
    email: str = ""
    project_id: str = ""
    managed_project_id: str = ""
    tier_id: str = ""
    source: str = "oauth_pkce"
    unavailable_until: float = 0.0
    last_failure_status: int = 0
    last_used_at: float = 0.0  # lần cuối được chọn làm ứng viên đầu; dùng để xoay vòng LRU

    @property
    def is_expired(self) -> bool:
        if not self.access_token:
            return True
        if self.expires_at <= 0:
            return False
        return (self.expires_at - time.time()) < REFRESH_SKEW_SECONDS

    @property
    def is_cooling_down(self) -> bool:
        return self.unavailable_until > time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "email": self.email,
            "project_id": self.project_id,
            "managed_project_id": self.managed_project_id,
            "tier_id": self.tier_id,
            "source": self.source,
            "unavailable_until": self.unavailable_until,
            "last_failure_status": self.last_failure_status,
            "last_used_at": self.last_used_at,
            "updated_at": time.time(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AntigravityCredentials:
        return cls(
            access_token=data.get("access_token") or "",
            refresh_token=data.get("refresh_token") or "",
            expires_at=float(data.get("expires_at") or 0.0),
            email=data.get("email") or "",
            project_id=data.get("project_id") or "",
            managed_project_id=data.get("managed_project_id") or "",
            tier_id=data.get("tier_id") or "",
            source=data.get("source") or "stored",
            unavailable_until=float(data.get("unavailable_until") or 0.0),
            last_failure_status=int(data.get("last_failure_status") or 0),
            last_used_at=float(data.get("last_used_at") or 0.0),
        )


class AntigravityAuthManager:
    """Đọc/ghi pool tài khoản, refresh token, chọn ứng viên theo thứ tự failover."""

    def __init__(self, auth_file: Path | None = None) -> None:
        self.auth_file = auth_file or default_token_file()
        self._lock = threading.RLock()

    @property
    def token_file(self) -> Path:
        return self.auth_file

    def get_client_id(self) -> str:
        return (os.getenv(ENV_CLIENT_ID) or "").strip() or DEFAULT_CLIENT_ID

    def get_client_secret(self) -> str:
        return (os.getenv(ENV_CLIENT_SECRET) or "").strip() or DEFAULT_CLIENT_SECRET

    # ---------- đọc / ghi ----------

    def _read_file(self) -> dict[str, Any]:
        if not self.auth_file.is_file():
            return {}
        try:
            with open(self.auth_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning("Không đọc được %s: %s", self.auth_file, e)
            return {}

    def load_all_stored_credentials(self) -> list[AntigravityCredentials]:
        """Toàn bộ tài khoản trong file (định dạng `accounts` nhiều tài khoản, hoặc một tài khoản phẳng)."""
        data = self._read_file()
        creds_list: list[AntigravityCredentials] = []
        seen: set[str] = set()
        accounts = data.get("accounts")
        if isinstance(accounts, dict):
            for email_key, acct in accounts.items():
                if isinstance(acct, dict) and acct.get("access_token"):
                    em = acct.get("email") or email_key
                    if em not in seen:
                        seen.add(em)
                        creds_list.append(AntigravityCredentials.from_dict(acct))
        if data.get("access_token"):
            em = data.get("email") or "primary"
            if em not in seen:
                seen.add(em)
                creds_list.append(AntigravityCredentials.from_dict(data))
        return creds_list

    def load_stored_credentials(self, email: str | None = None) -> AntigravityCredentials | None:
        all_creds = self.load_all_stored_credentials()
        if not all_creds:
            return None
        if email:
            for c in all_creds:
                if c.email.lower() == email.lower():
                    return c
        return all_creds[0]

    def _atomic_write(self, path: Path, data: dict[str, Any]) -> None:
        """Ghi JSON nguyên tử: tạo file tạm với mode 0600 ngay từ lúc mở (không có khoảng hở
        world-readable), thư mục cha 0700, rồi replace vào chỗ."""
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp_path = path.with_suffix(".tmp")
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            if os.name != "nt":
                os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp_path, path)
        except Exception:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            raise

    def save_credentials(self, creds: AntigravityCredentials) -> None:
        """Ghi nguyên tử, giữ nguyên các tài khoản khác. File chỉ chủ sở hữu đọc được."""
        with self._lock:
            existing = self._read_file()
            accounts = existing.get("accounts")
            if not isinstance(accounts, dict):
                accounts = {}
                if existing.get("access_token"):
                    accounts[existing.get("email") or "primary"] = existing
            accounts[creds.email or "primary"] = creds.to_dict()

            out = creds.to_dict()
            out["accounts"] = accounts
            try:
                self._atomic_write(self.auth_file, out)
            except Exception as e:
                logger.error("Không ghi được token file %s: %s", self.auth_file, e)

    def _update_account_fields(self, creds: AntigravityCredentials, **fields: Any) -> None:
        """Đọc lại file và chỉ sửa vài trường của một tài khoản, KHÔNG ghi đè cả object `creds`
        (có thể đã cũ: request khác vừa refresh token mới trong lúc request này đang chờ upstream)."""
        with self._lock:
            for k, v in fields.items():
                setattr(creds, k, v)
            existing = self._read_file()
            accounts = existing.get("accounts")
            key = creds.email or "primary"
            if not isinstance(accounts, dict) or not isinstance(accounts.get(key), dict):
                self.save_credentials(creds)
                return
            accounts[key].update(fields)
            out = dict(existing)
            out["accounts"] = accounts
            if (existing.get("email") or "primary") == key:
                out.update(fields)
            try:
                self._atomic_write(self.auth_file, out)
            except Exception as e:
                logger.error("Không ghi được token file %s: %s", self.auth_file, e)

    def remove_account(self, email: str) -> bool:
        with self._lock:
            data = self._read_file()
            accounts = data.get("accounts")
            if not isinstance(accounts, dict) or email not in accounts:
                return False
            del accounts[email]
            if not accounts:
                self.auth_file.unlink(missing_ok=True)
                return True
            first = next(iter(accounts.values()))
            out = dict(first)
            out["accounts"] = accounts
            self._atomic_write(self.auth_file, out)
            return True

    def clear_credentials(self) -> bool:
        with self._lock:
            if self.auth_file.exists():
                try:
                    self.auth_file.unlink()
                    return True
                except Exception as e:
                    logger.warning("Không xóa được %s: %s", self.auth_file, e)
            return False

    # ---------- pool / failover ----------

    def resolve_credential_candidates(self, bearer_token: str = "") -> list[AntigravityCredentials]:
        """Danh sách tài khoản dùng được theo thứ tự failover, đã refresh khi cần.

        - `bearer_token` (từ header Authorization của client) trùng access token hoặc email
          của một tài khoản thì tài khoản đó được ưu tiên lên đầu.
        - Tài khoản đang cooldown bị bỏ qua.
        - Refresh bị Google từ chối (HTTP 4xx) → cooldown 401. Lỗi mạng tạm thời → bỏ qua lượt này,
          KHÔNG cooldown (tránh một nhịp mạng chập chờn làm nguội cả pool).
        """
        with self._lock:
            all_creds = self.load_all_stored_credentials()
            if not all_creds:
                raise UpstreamError("Chưa có tài khoản Antigravity nào. Chạy `python -m gateway login`.", 503)

            def matches_bearer(c: AntigravityCredentials) -> bool:
                if not bearer_token:
                    return False
                return bool(
                    c.access_token == bearer_token
                    or (bearer_token.startswith("ya29.") and c.access_token.startswith(bearer_token[:20]))
                    or (c.email and c.email.lower() == bearer_token.lower())
                )

            # Bearer khớp lên đầu; còn lại xoay vòng LRU (tài khoản lâu chưa dùng nhất đi trước).
            all_creds.sort(key=lambda c: (not matches_bearer(c), c.last_used_at))
            now = time.time()
            candidates: list[AntigravityCredentials] = []
            for creds in all_creds:
                if creds.unavailable_until > now:
                    continue
                if creds.is_expired:
                    if not creds.refresh_token:
                        continue
                    try:
                        creds = self.refresh_access_token(creds)
                    except urllib.error.HTTPError as exc:
                        logger.warning(
                            "Google từ chối refresh token của %s (HTTP %s)", creds.email or "unknown", exc.code
                        )
                        self.mark_account_unavailable(creds, 401)
                        continue
                    except Exception as exc:
                        logger.warning(
                            "Lỗi tạm thời khi refresh %s (bỏ qua lượt này, không cooldown): %s",
                            creds.email or "unknown",
                            exc,
                        )
                        continue
                if not creds.project_id:
                    creds.project_id = self.resolve_project_id(creds)
                    self.save_credentials(creds)
                candidates.append(creds)

            if not candidates:
                earliest = min((c.unavailable_until for c in all_creds if c.unavailable_until > now), default=0.0)
                wait = max(0, int(earliest - now)) if earliest else 0
                suffix = f" Thử lại sau khoảng {wait}s." if wait else ""
                raise UpstreamError("Mọi tài khoản Antigravity đều đang cooldown hoặc hết hạn." + suffix, 429)
            # Đồng hồ hệ thống có thể thô hơn khoảng cách giữa hai lượt (Windows: ~15ms). Hai tài khoản
            # cùng mốc thì khóa sắp xếp LRU hòa nhau và thứ tự quay về thứ tự lưu file — hết xoay vòng.
            # Luôn đóng dấu lớn hơn hẳn mốc lớn nhất đang có để LRU đơn điệu bất kể độ phân giải đồng hồ.
            newest = max((c.last_used_at for c in all_creds), default=0.0)
            self._update_account_fields(candidates[0], last_used_at=max(now, newest + 1e-3))
            return candidates

    def mark_account_unavailable(
        self, creds: AntigravityCredentials, status_code: int, retry_after: str | None = None
    ) -> None:
        """Ghi cooldown cho một tài khoản sau lỗi auth/quota/server."""
        cooldown = COOLDOWN_DEFAULTS.get(status_code, COOLDOWN_FALLBACK)
        if retry_after:
            with contextlib.suppress(ValueError, TypeError):
                cooldown = max(1, int(float(retry_after)))
        # Chỉ cập nhật hai trường cooldown, không ghi đè token (có thể đã được request khác refresh).
        self._update_account_fields(
            creds, unavailable_until=time.time() + cooldown, last_failure_status=int(status_code)
        )
        logger.warning(
            "Tài khoản %s vào cooldown %ss sau HTTP %s", creds.email or "unknown", cooldown, status_code
        )

    def mark_account_healthy(self, creds: AntigravityCredentials) -> None:
        """Xóa cooldown thủ công (CLI `reset`)."""
        self._update_account_fields(creds, unavailable_until=0.0, last_failure_status=0)

    # ---------- Google ----------

    def refresh_access_token(self, creds: AntigravityCredentials) -> AntigravityCredentials:
        if not creds.refresh_token:
            raise RuntimeError("Không có refresh token để làm mới.")
        payload = urllib.parse.urlencode(
            {
                "client_id": self.get_client_id(),
                "client_secret": self.get_client_secret(),
                "refresh_token": creds.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            TOKEN_ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        new_token = raw.get("access_token")
        if not new_token:
            raise RuntimeError(f"Refresh token thất bại: {raw}")
        creds.access_token = new_token
        creds.expires_at = time.time() + float(raw.get("expires_in") or 3600.0)
        if raw.get("refresh_token"):
            creds.refresh_token = raw["refresh_token"]
        self.save_credentials(creds)
        logger.info("Đã làm mới access token cho %s", creds.email or "unknown")
        return creds

    def resolve_project_id(self, creds: AntigravityCredentials) -> str:
        env_proj = (os.getenv(ENV_PROJECT_ID) or "").strip()
        if env_proj:
            return env_proj
        if creds.project_id:
            return creds.project_id
        try:
            req = urllib.request.Request(
                LOAD_CODE_ASSIST_ENDPOINT,
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {creds.access_token}",
                    "User-Agent": "Antigravity/1.0.0 windows/amd64",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            pid = data.get("cloudaicompanionProject") or data.get("projectId") or data.get("project_id") or ""
            if pid:
                creds.project_id = pid
                creds.managed_project_id = pid
                creds.tier_id = data.get("tierId") or ""
                return pid
        except Exception as e:
            logger.debug("loadCodeAssist không trả project id, dùng mặc định: %s", e)
        return DEFAULT_PROJECT_ID

    def login_pkce(
        self,
        port: int = DEFAULT_REDIRECT_PORT,
        open_browser: bool = True,
        timeout_seconds: float = 300.0,
    ) -> AntigravityCredentials:
        """Google OAuth 2.0 Authorization Code + PKCE. Mỗi lần gọi thêm một tài khoản vào pool."""
        code_verifier = secrets.token_urlsafe(64)[:128]
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("utf-8")).digest()).decode("utf-8").rstrip("=")
        )
        state = secrets.token_hex(16)
        redirect_uri = f"http://{REDIRECT_HOST}:{port}{CALLBACK_PATH}"
        client_id = self.get_client_id()
        client_secret = self.get_client_secret()

        auth_url = f"{AUTH_ENDPOINT}?" + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": OAUTH_SCOPES,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": state,
                "access_type": "offline",
                "prompt": "consent",
            }
        )

        holder: dict[str, str | None] = {"code": None, "error": None}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                if qs.get("state", [None])[0] != state:
                    holder["error"] = "State không khớp trong OAuth callback"
                    self._html("<h3>Lỗi xác thực</h3><p>State không khớp.</p>", 400)
                    return
                code = qs.get("code", [None])[0]
                if code:
                    holder["code"] = code
                    self._html(
                        "<html><body style='font-family:sans-serif;text-align:center;padding-top:40px'>"
                        "<h2 style='color:#10b981'>Đăng nhập Antigravity thành công</h2>"
                        "<p>Bạn có thể đóng tab này.</p></body></html>",
                        200,
                    )
                else:
                    holder["error"] = qs.get("error", ["Lỗi không rõ"])[0]
                    self._html(f"<h3>Đăng nhập thất bại</h3><p>{holder['error']}</p>", 400)

            def _html(self, html: str, status: int):
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

            def log_message(self, format, *args):
                pass

        server = HTTPServer((REDIRECT_HOST, port), CallbackHandler)
        server.timeout = 1.0
        if open_browser:
            logger.info("Mở trình duyệt để đăng nhập: %s", auth_url)
            webbrowser.open(auth_url)
        else:
            print(f"\nMở URL này trong trình duyệt để đăng nhập Google Antigravity:\n{auth_url}\n")

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            server.handle_request()
            if holder["code"] or holder["error"]:
                break
        server.server_close()

        if holder["error"]:
            raise RuntimeError(f"OAuth lỗi: {holder['error']}")
        if not holder["code"]:
            raise TimeoutError("Hết thời gian chờ Google OAuth callback.")

        token_payload = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": holder["code"],
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            TOKEN_ENDPOINT,
            data=token_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            token_data = json.loads(resp.read().decode("utf-8"))

        access_token = token_data.get("access_token")
        email = ""
        try:
            user_req = urllib.request.Request(
                f"{USERINFO_ENDPOINT}?alt=json", headers={"Authorization": f"Bearer {access_token}"}
            )
            with urllib.request.urlopen(user_req, timeout=10.0) as user_resp:
                email = json.loads(user_resp.read().decode("utf-8")).get("email") or ""
        except Exception:
            pass

        creds = AntigravityCredentials(
            access_token=access_token,
            refresh_token=token_data.get("refresh_token") or "",
            expires_at=time.time() + float(token_data.get("expires_in") or 3600.0),
            email=email,
            source="oauth_pkce",
        )
        creds.project_id = self.resolve_project_id(creds)
        self.save_credentials(creds)
        logger.info("Đăng nhập Antigravity thành công: %s", email or "user")
        return creds


__all__ = [
    "DEFAULT_PROJECT_ID",
    "AntigravityAuthManager",
    "AntigravityCredentials",
    "UpstreamError",
    "get_gateway_dir",
    "get_home_dir",
]
