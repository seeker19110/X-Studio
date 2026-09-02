#!/usr/bin/env python3
"""CLI quản lý gateway.

  python -m gateway login [--no-browser]   thêm một tài khoản Google vào pool (chạy nhiều lần = nhiều tài khoản)
  python -m gateway start [--foreground]   chạy daemon (mặc định 127.0.0.1:8100)
  python -m gateway stop
  python -m gateway status                 trạng thái server + từng tài khoản trong pool
  python -m gateway reset [EMAIL]          xóa cooldown (một hoặc mọi tài khoản)
  python -m gateway logout EMAIL           gỡ tài khoản khỏi pool
  python -m gateway setup [--target ...]   ghi llm.yaml của software-company trỏ vào gateway
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from gateway.auth import AntigravityAuthManager
from gateway.server import DEFAULT_HOST, DEFAULT_PORT, get_log_file, get_pid_file, is_server_running

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SETUP_TARGET = REPO_ROOT / "software-company" / "llm.yaml"
DEFAULT_STRONG_MODEL = "claude-sonnet-4-6"
DEFAULT_STANDARD_MODEL = "gemini-3.7-flash"


def cmd_start(args: argparse.Namespace) -> int:
    host, port = args.host, args.port
    if is_server_running(host, port):
        print(f"[*] Gateway đã chạy sẵn tại http://{host}:{port}")
        return 0
    if args.foreground:
        from gateway.server import run_server

        print(f"[*] Chạy gateway foreground tại http://{host}:{port} ...")
        run_server(host=host, port=port)
        return 0

    log_file = get_log_file()
    print(f"[*] Khởi động gateway daemon tại http://{host}:{port} ...")
    src_dir = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = str(src_dir) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, "-u", "-c", f"from gateway.server import run_server; run_server(host='{host}', port={port})"]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    with open(log_file, "a", encoding="utf-8") as log_fd:
        proc = subprocess.Popen(
            cmd,
            cwd=str(src_dir),
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            env=env,
            **({} if sys.platform == "win32" else {"start_new_session": True}),
        )
    get_pid_file().write_text(str(proc.pid), encoding="utf-8")

    deadline = time.time() + 10.0
    while time.time() < deadline:
        if is_server_running(host, port):
            print(f"[+] Gateway đã chạy (PID {proc.pid})")
            print(f"    Endpoint: http://{host}:{port}/v1")
            print(f"    Log:      {log_file}")
            return 0
        time.sleep(0.3)
    print(f"[-] Tiến trình đã khởi động nhưng healthcheck quá hạn. Xem log: {log_file}")
    return 1


def cmd_stop(args: argparse.Namespace) -> int:
    pid_file = get_pid_file()
    if not pid_file.is_file():
        print("[*] Không có PID file; gateway chưa chạy dưới dạng daemon.")
        return 0
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        pid = 0
    if pid > 0:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
            else:
                os.kill(pid, signal.SIGTERM)
            print(f"[+] Đã dừng gateway (PID {pid})")
        except Exception as e:
            print(f"[*] Tiến trình {pid} không chạy hoặc không dừng được: {e}")
    pid_file.unlink(missing_ok=True)
    return 0


def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


def cmd_status(args: argparse.Namespace) -> int:
    host, port = args.host, args.port
    running = is_server_running(host, port)
    mgr = AntigravityAuthManager()
    accounts = mgr.load_all_stored_credentials()
    now = time.time()

    print("=" * 60)
    print("  GATEWAY — proxy xoay vòng tài khoản Antigravity")
    print("=" * 60)
    print(f"  Server:      {'ONLINE' if running else 'OFFLINE'}  http://{host}:{port}/v1")
    print(f"  PID file:    {get_pid_file()}")
    print(f"  Log:         {get_log_file()}")
    print(f"  Token file:  {mgr.token_file}")
    print("-" * 60)
    if not accounts:
        print("  Pool trống. Chạy: python -m gateway login")
    for c in accounts:
        cooldown = max(0, int(c.unavailable_until - now))
        state = f"COOLDOWN {cooldown}s (HTTP {c.last_failure_status})" if cooldown else "SẴN SÀNG"
        token_state = "hết hạn, sẽ tự refresh" if c.is_expired else "còn hạn"
        print(f"  • {c.email or 'primary':40} {state}")
        print(f"      project={c.project_id or 'auto'}  token {token_state} tới {_fmt_time(c.expires_at)}"
              f"  refresh={'có' if c.refresh_token else 'KHÔNG'}")
    available = sum(1 for c in accounts if c.unavailable_until <= now)
    print("-" * 60)
    print(f"  {available}/{len(accounts)} tài khoản sẵn sàng xoay vòng")
    print("=" * 60)
    return 0 if running and available else 1


def cmd_login(args: argparse.Namespace) -> int:
    print("[*] Bắt đầu Google OAuth PKCE cho Antigravity ...")
    mgr = AntigravityAuthManager()
    try:
        creds = mgr.login_pkce(open_browser=not args.no_browser)
    except Exception as e:
        print(f"[-] Đăng nhập thất bại: {e}")
        return 1
    print("[+] Đăng nhập thành công")
    print(f"    Tài khoản: {creds.email}")
    print(f"    Project:   {creds.project_id}")
    print(f"    Pool:      {len(mgr.load_all_stored_credentials())} tài khoản tại {mgr.token_file}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    mgr = AntigravityAuthManager()
    targets = [c for c in mgr.load_all_stored_credentials() if not args.email or c.email == args.email]
    if not targets:
        print("[-] Không tìm thấy tài khoản phù hợp.")
        return 1
    for c in targets:
        mgr.mark_account_healthy(c)
        print(f"[+] Đã xóa cooldown: {c.email or 'primary'}")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    mgr = AntigravityAuthManager()
    if mgr.remove_account(args.email):
        print(f"[+] Đã gỡ {args.email} khỏi pool.")
        return 0
    print(f"[-] Không có tài khoản {args.email} trong pool.")
    return 1


def cmd_setup(args: argparse.Namespace) -> int:
    """Ghi `llm.yaml` cho software-company (provider openai, base_url trỏ vào gateway)."""
    import yaml

    target = Path(args.target).expanduser()
    data: dict = {}
    if target.exists():
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    data["provider"] = "openai"
    data["base_url"] = f"http://{args.host}:{args.port}/v1"
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    models["strong"] = args.strong
    models["standard"] = args.standard
    data["models"] = models
    data.setdefault("max_tokens", 16000)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"[+] Đã ghi {target}")
    print(f"    provider=openai  base_url={data['base_url']}")
    print(f"    strong={args.strong}  standard={args.standard}")
    print("    Đặt COMPANY_LLM_API_KEY=gateway-local (chuỗi bất kỳ; gateway tự xác thực Google).")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Console Windows mặc định cp1252 không in được tiếng Việt.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="gateway", description="Proxy xoay vòng tài khoản Antigravity")
    sub = parser.add_subparsers(dest="action", required=True)

    def add_hostport(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", default=DEFAULT_HOST)
        p.add_argument("--port", type=int, default=DEFAULT_PORT)

    p = sub.add_parser("start", help="chạy daemon")
    add_hostport(p)
    p.add_argument("--foreground", "-f", action="store_true")
    sub.add_parser("stop", help="dừng daemon")
    p = sub.add_parser("status", help="trạng thái server và pool")
    add_hostport(p)
    p = sub.add_parser("login", help="thêm tài khoản Google")
    p.add_argument("--no-browser", action="store_true")
    p = sub.add_parser("reset", help="xóa cooldown")
    p.add_argument("email", nargs="?", default="")
    p = sub.add_parser("logout", help="gỡ tài khoản khỏi pool")
    p.add_argument("email")
    p = sub.add_parser("setup", help="ghi llm.yaml của software-company trỏ vào gateway")
    add_hostport(p)
    p.add_argument("--target", default=str(DEFAULT_SETUP_TARGET))
    p.add_argument("--strong", default=DEFAULT_STRONG_MODEL)
    p.add_argument("--standard", default=DEFAULT_STANDARD_MODEL)

    args = parser.parse_args(argv)
    handlers = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "login": cmd_login,
        "reset": cmd_reset,
        "logout": cmd_logout,
        "setup": cmd_setup,
    }
    return handlers[args.action](args)


if __name__ == "__main__":
    sys.exit(main())
