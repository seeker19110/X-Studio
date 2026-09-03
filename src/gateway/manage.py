#!/usr/bin/env python3
"""CLI quản lý gateway.

  python -m gateway login [--no-browser]   thêm một tài khoản Google vào pool (chạy nhiều lần = nhiều tài khoản)
  python -m gateway start [--foreground]   chạy daemon (mặc định 127.0.0.1:8100)
  python -m gateway stop
  python -m gateway status                 trạng thái server + từng tài khoản trong pool
  python -m gateway reset [EMAIL]          xóa cooldown (một hoặc mọi tài khoản)
  python -m gateway logout EMAIL           gỡ tài khoản khỏi pool
  python -m gateway setup [--target ...]   ghi llm.yaml của software-company trỏ vào gateway
  python -m gateway models [--check ...]   model gateway hỗ trợ + đối chiếu llm.yaml của các công ty
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
from typing import Any

from gateway.auth import AntigravityAuthManager
from gateway.client import (
    FALLBACK_MODELS,
    MODEL_ALIAS_MAP,
    PROBE_MISSING,
    PROBE_OK,
    PROBE_QUOTA,
    PROBE_RETIRED,
    classify_probe,
    fetch_available_models,
    known_model_ids,
    map_model_name,
    probe_code_assist_model,
    set_discovered_models,
)
from gateway.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    get_log_file,
    get_pid_file,
    is_loopback_host,
    is_server_running,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SETUP_TARGET = REPO_ROOT / "software-company" / "llm.yaml"
DEFAULT_STRONG_MODEL = "claude-sonnet-4-6"
DEFAULT_STANDARD_MODEL = "gemini-3.6-flash-medium"
DEFAULT_CHECK_TARGETS = [
    REPO_ROOT / "software-company" / "llm.yaml",
    REPO_ROOT / "Studio-creators" / "llm.yaml",
]


def _pid_is_gateway(pid: int) -> bool:
    """Tránh SIGTERM nhầm tiến trình khác tái dùng PID: trên Linux kiểm tra /proc/<pid>/cmdline có "gateway".
    Hệ khác không có /proc → bỏ qua kiểm tra."""
    if not sys.platform.startswith("linux"):
        return True
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return b"gateway" in cmdline


def _run_daemon(host: str, port: int) -> None:
    """Điểm vào của tiến trình daemon: xoá PID file khi thoát (atexit) rồi chạy server."""
    import atexit

    from gateway.server import run_server

    pid_file = get_pid_file()

    def _cleanup() -> None:
        with contextlib.suppress(Exception):
            if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()

    atexit.register(_cleanup)
    run_server(host=host, port=port)


def cmd_start(args: argparse.Namespace) -> int:
    host, port = args.host, args.port
    if not is_loopback_host(host):
        print(f"[!] CẢNH BÁO: --host {host} không phải loopback; gateway không có xác thực client, "
              "mọi máy trong mạng đều dùng được pool tài khoản. Chỉ làm vậy sau firewall/reverse proxy.")
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
    cmd = [sys.executable, "-u", "-c", f"from gateway.manage import _run_daemon; _run_daemon(host='{host}', port={port})"]
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
            start_new_session=sys.platform != "win32",
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
    if pid > 0 and not _pid_is_gateway(pid):
        print(f"[*] PID {pid} không phải tiến trình gateway (đã thoát hoặc PID được tái dùng); chỉ xoá PID file.")
    elif pid > 0:
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
    models: dict[str, Any] = data["models"] if isinstance(data.get("models"), dict) else {}
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


def _gateway_backends(data: dict, base_url_marker: str) -> list[tuple[str, dict]]:
    """Các backend trong llm.yaml thực sự trỏ vào gateway này (provider openai + base_url khớp host:port).
    Backend claude-code/codex đi CLI riêng, tên model của chúng không phải việc của gateway."""
    found: list[tuple[str, dict]] = []
    backends = data.get("backends")
    if isinstance(backends, list):
        for b in backends:
            if not isinstance(b, dict):
                continue
            if str(b.get("provider") or "") == "openai" and base_url_marker in str(b.get("base_url") or ""):
                models = b.get("models")
                found.append((str(b.get("name") or "?"), models if isinstance(models, dict) else {}))
        return found
    # Dạng một provider duy nhất (bản `setup` ghi ra).
    if str(data.get("provider") or "") == "openai" and base_url_marker in str(data.get("base_url") or ""):
        models = data.get("models")
        found.append(("(provider đơn)", models if isinstance(models, dict) else {}))
    return found


CLI_PROVIDERS = {"claude-code": ["claude", "-p", "--model"], "codex": ["codex", "exec", "--model"]}


def _cli_backends(data: dict) -> list[tuple[str, str, dict]]:
    """(tên backend, provider, models) cho backend chạy CLI subscription — gateway không đứng giữa,
    nhưng `models --probe-cli` vẫn kiểm tra được bằng cách gọi thử CLI."""
    out: list[tuple[str, str, dict]] = []
    for b in data.get("backends") or []:
        if not isinstance(b, dict):
            continue
        provider = str(b.get("provider") or "")
        if provider in CLI_PROVIDERS:
            models = b.get("models")
            out.append((str(b.get("name") or "?"), provider, models if isinstance(models, dict) else {}))
    return out


def _probe_antigravity(ids: list[str]) -> int:
    """Gọi thử từng model lên Code Assist. Trả số model hỏng (nghỉ hưu / không tồn tại) trong danh sách."""
    mgr = AntigravityAuthManager()
    candidates = mgr.resolve_credential_candidates()
    if not candidates:
        print("  Pool trống hoặc mọi tài khoản đang cooldown — không probe được. `python -m gateway login`")
        return 1
    print(f"  Dò bằng {len(candidates)} tài khoản trong pool (mỗi model = 1 request thật, tốn quota)")
    broken = 0
    for model_id in ids:
        verdict, note, who = PROBE_QUOTA, "", ""
        for creds in candidates:
            # 429 không kết luận được model sống hay chết — sang tài khoản khác trước khi bỏ cuộc.
            try:
                status, body = probe_code_assist_model(model_id, creds.access_token, creds.project_id)
            except Exception as e:
                verdict, note, who = "LỖI MẠNG", str(e), creds.email
                continue
            verdict, note = classify_probe(status, body)
            who = creds.email or "primary"
            if verdict != PROBE_QUOTA:
                break
        if verdict in {PROBE_RETIRED, PROBE_MISSING}:
            broken += 1
        suffix = f"[{who}] {note}" if verdict == PROBE_QUOTA else note
        print(f"      {model_id:<28} {verdict:<14} {suffix}")
    return broken


def _probe_cli(provider: str, model: str, timeout: float = 120.0) -> tuple[str, str]:
    """Gọi thử CLI subscription một lượt cực ngắn. Không có CLI trên PATH → báo rõ, không coi là model sai."""
    import shutil
    import subprocess

    args = CLI_PROVIDERS[provider]
    exe = shutil.which(args[0])
    if not exe:
        return "THIẾU CLI", f"không thấy `{args[0]}` trên PATH"
    try:
        r = subprocess.run(
            [exe, *args[1:], model], input="hi", capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "LỖI", f"quá {timeout:.0f}s"
    if r.returncode == 0:
        return PROBE_OK, ""
    err = " ".join((r.stderr or r.stdout or "").split())[:140]
    return "LỖI", f"exit {r.returncode}: {err}"


def _discover_catalog() -> list[dict[str, str]]:
    """Hỏi upstream danh sách model (`:fetchAvailableModels`). Không có tài khoản / lỗi mạng → rỗng."""
    mgr = AntigravityAuthManager()
    try:
        candidates = mgr.resolve_credential_candidates()
    except Exception as e:
        print(f"  Không lấy được tài khoản để dò: {e}")
        return []
    for creds in candidates:
        try:
            models = fetch_available_models(creds.access_token, creds.project_id)
        except Exception as e:
            print(f"  Dò qua {creds.email or 'primary'} lỗi: {e}")
            continue
        if models:
            return models
    return []


def cmd_models(args: argparse.Namespace) -> int:
    """In model gateway hỗ trợ và đối chiếu với llm.yaml của các công ty. Exit 1 nếu có tên model
    mà gateway không biết (nếu để im, request sẽ bị từ chối 400 lúc chạy)."""
    live = [] if args.offline else _discover_catalog()
    if live:
        set_discovered_models(live)
    known = set(known_model_ids())
    print("=" * 72)
    header = "upstream tự khai" if live else "bảng tĩnh trong client.py (chưa dò được upstream)"
    print(f"  GATEWAY — model hỗ trợ · nguồn: {header}")
    print("=" * 72)
    for m in live or FALLBACK_MODELS:
        # Cột phải là model upstream THẬT sau alias map (vd. gemini-3.7-flash thật ra chạy gemini-3.7-flash-high).
        print(f"  {m['id']:<26} → {map_model_name(m['id']):<26} {m['name']}")
    if live:
        # Alias trỏ vào id upstream không còn khai = alias chết, request sẽ hỏng lúc chạy.
        live_ids = {m["id"] for m in live}
        dangling = sorted({f"{a} → {t}" for a, t in MODEL_ALIAS_MAP.items() if t not in live_ids})
        if dangling:
            print("-" * 72)
            print(f"  Alias trỏ vào model upstream không còn khai: {', '.join(dangling)}")
    extra = sorted(set(MODEL_ALIAS_MAP) - {m["id"] for m in (live or FALLBACK_MODELS)})
    if extra:
        print("-" * 72)
        print("  Alias cũng chấp nhận: " + ", ".join(extra))

    problems = 0   # tên model trong llm.yaml gateway không biết
    dead = 0       # model gateway khai là hợp lệ nhưng upstream đã bỏ
    if args.probe:
        print("=" * 72)
        print("  Dò upstream Code Assist (Google KHÔNG có endpoint liệt kê model — phải gọi thử)")
        print("-" * 72)
        probe_ids = args.probe_id or sorted({m["id"] for m in (live or FALLBACK_MODELS)})
        custom = bool(args.probe_id)   # id do người dùng đưa: chết chỉ nghĩa là "chưa có", không phải lỗi cấu hình
        broken = _probe_antigravity(probe_ids)
        dead = 0 if custom else broken
        if broken and custom:
            print(f"  {broken}/{len(probe_ids)} id dò không có trên kênh Antigravity của pool này.")
        elif broken:
            print(f"  {broken} model gateway đang coi là hợp lệ nhưng upstream đã bỏ/nghỉ hưu → sửa client.py.")

    base_url_marker = f"{args.host}:{args.port}"
    targets = [Path(t).expanduser() for t in (args.check or [str(p) for p in DEFAULT_CHECK_TARGETS])]
    print("=" * 72)
    print(f"  Đối chiếu llm.yaml (backend trỏ vào {base_url_marker})")
    print("-" * 72)
    for target in targets:
        if not target.exists():
            print(f"  {target}: không có file, bỏ qua")
            continue
        import yaml

        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"  {target}: đọc lỗi ({e})")
            problems += 1
            continue
        data = data if isinstance(data, dict) else {}
        backends = _gateway_backends(data, base_url_marker)
        cli_backends = _cli_backends(data)
        if not backends and not cli_backends:
            print(f"  {target}: không có backend nào để đối chiếu")
            continue
        print(f"  {target}")
        for name, models in backends:
            for tier in ("strong", "standard", "light"):
                model = str(models.get(tier) or "")
                if not model:
                    continue
                ok = model.lower().split("/")[-1] in known
                if not ok:
                    problems += 1
                print(f"      backend {name:<14} {tier:<9} {model:<24} {'OK' if ok else 'KHÔNG HỖ TRỢ'}")
        for name, provider, models in cli_backends:
            seen: set[str] = set()
            for tier in ("strong", "standard", "light"):
                model = str(models.get(tier) or "")
                if not model or model in seen:
                    continue
                seen.add(model)
                if args.probe_cli:
                    verdict, note = _probe_cli(provider, model)
                    if verdict == "LỖI":
                        problems += 1
                    print(f"      backend {name:<14} {tier:<9} {model:<24} {verdict}  {note}")
                else:
                    print(f"      backend {name:<14} {tier:<9} {model:<24} CLI {provider} — dùng --probe-cli để gọi thử")
    print("=" * 72)
    if problems:
        print(f"  {problems} tên model gateway không biết → request sẽ bị trả 400 lúc chạy.")
        print("  Sửa llm.yaml, hoặc thêm alias vào MODEL_ALIAS_MAP trong src/gateway/client.py.")
    elif not dead:
        print("  Mọi model trong llm.yaml đều được gateway hỗ trợ.")
    return 1 if (problems or dead) else 0


def main(argv: list[str] | None = None) -> int:
    # Console Windows mặc định cp1252 không in được tiếng Việt.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")
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
    p = sub.add_parser("models", help="model gateway hỗ trợ + đối chiếu llm.yaml của các công ty")
    add_hostport(p)
    p.add_argument("--check", action="append", help="đường dẫn llm.yaml cần đối chiếu (lặp lại được)")
    p.add_argument("--offline", action="store_true",
                   help="không hỏi upstream, chỉ in bảng tĩnh trong client.py")
    p.add_argument("--probe", action="store_true",
                   help="gọi thử upstream Code Assist để xem model còn sống không (tốn quota thật)")
    p.add_argument("--probe-id", action="append",
                   help="id ứng viên cần dò, lặp lại được (mặc định: các id gateway đang coi là hợp lệ)")
    p.add_argument("--probe-cli", action="store_true",
                   help="gọi thử CLI subscription (claude -p / codex) cho backend claude-code, codex")

    args = parser.parse_args(argv)
    handlers = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "login": cmd_login,
        "reset": cmd_reset,
        "logout": cmd_logout,
        "setup": cmd_setup,
        "models": cmd_models,
    }
    return handlers[args.action](args)


if __name__ == "__main__":
    sys.exit(main())
