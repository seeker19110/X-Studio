"""Phủ nốt các nhánh CLI chưa test: _pid_is_gateway non-linux, cmd_start (foreground/daemon/spawn),
cmd_stop (mọi nhánh), cmd_status, cmd_login, _discover_catalog lỗi mạng, main() dispatch.

Tên file cố tình đặt để sort SAU test_server*.py theo alphabet (pytest chạy theo thứ tự thu thập
mặc định = alphabet). Một vài test ở đây monkeypatch `sys.platform` (để ép nhánh POSIX-only của
_pid_is_gateway chạy trên Windows CI) — nếu module này chạy TRƯỚC các test aiohttp/async của
test_server.py, coverage.py trong môi trường này undercount toàn bộ nhánh streaming của
handle_chat_completions dù test đó vẫn PASS bình thường (đã xác minh qua log assertion). Không rõ
cơ chế chính xác (nghi là coverage.py + asyncio event loop trên Windows), tái lập ổn định qua nhiều
lần chạy. Đổi tên file là cách né an toàn nhất; đừng đổi lại tên bắt đầu bằng "test_manage" mà
không kiểm tra lại `python -m pytest --cov=gateway --cov-report=term-missing -q` sau đó."""

from __future__ import annotations

import subprocess
import sys
import types

import pytest

from gateway import auth as gw_auth
from gateway import manage

# ---------- _pid_is_gateway ----------


def test_pid_is_gateway_short_circuits_on_non_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert manage._pid_is_gateway(123) is True


def test_pid_is_gateway_linux_oserror_treated_as_gateway(monkeypatch):
    """/proc tồn tại nhưng đọc lỗi (permission...) -> không chắc, coi là gateway để không SIGTERM nhầm."""
    monkeypatch.setattr(sys, "platform", "linux")

    def raise_oserror(self):
        raise PermissionError("nope")

    monkeypatch.setattr(manage.Path, "read_bytes", raise_oserror)
    assert manage._pid_is_gateway(1) is True


# ---------- cmd_start ----------


def test_cmd_start_foreground_runs_server_directly(monkeypatch, capsys):
    monkeypatch.setattr(manage, "is_server_running", lambda host, port: False)
    called = {}
    monkeypatch.setattr("gateway.server.run_server", lambda host, port: called.update(host=host, port=port))
    rc = manage.main(["start", "--host", "127.0.0.1", "--port", "1", "--foreground"])
    assert rc == 0
    assert called == {"host": "127.0.0.1", "port": 1}
    assert "foreground" in capsys.readouterr().out


def test_cmd_start_already_running_short_circuits(monkeypatch, capsys):
    monkeypatch.setattr(manage, "is_server_running", lambda host, port: True)
    assert manage.main(["start", "--host", "127.0.0.1", "--port", "1"]) == 0
    assert "đã chạy sẵn" in capsys.readouterr().out


def test_cmd_start_background_spawn_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    running_calls = {"n": 0}

    def fake_running(host, port):
        running_calls["n"] += 1
        return running_calls["n"] > 1  # false lần đầu (trước spawn), true sau khi "khởi động"

    monkeypatch.setattr(manage, "is_server_running", fake_running)

    class FakeProc:
        pid = 4242

    monkeypatch.setattr(manage.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(manage.time, "sleep", lambda s: None)
    rc = manage.main(["start", "--host", "127.0.0.1", "--port", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PID 4242" in out
    assert manage.get_pid_file().read_text(encoding="utf-8") == "4242"


def test_cmd_start_background_spawn_sleeps_between_healthcheck_polls(tmp_path, monkeypatch, capsys):
    """Healthcheck đầu tiên thất bại (server chưa kịp bind) -> phải sleep rồi thử lại, không chỉ chờ 1 lần."""
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    running_calls = {"n": 0}

    def fake_running(host, port):
        running_calls["n"] += 1
        # lần 1: kiểm tra "đã chạy sẵn chưa" (trước spawn) -> False
        # lần 2: lần dò đầu trong vòng lặp -> False (bắt sleep chạy)
        # lần 3 trở đi: True
        return running_calls["n"] > 2

    monkeypatch.setattr(manage, "is_server_running", fake_running)

    class FakeProc:
        pid = 4244

    monkeypatch.setattr(manage.subprocess, "Popen", lambda *a, **k: FakeProc())
    sleeps = []
    monkeypatch.setattr(manage.time, "sleep", lambda s: sleeps.append(s))
    rc = manage.main(["start", "--host", "127.0.0.1", "--port", "5"])
    assert rc == 0
    assert sleeps == [0.3]
    assert "PID 4244" in capsys.readouterr().out


def test_cmd_start_background_spawn_healthcheck_timeout(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    monkeypatch.setattr(manage, "is_server_running", lambda host, port: False)

    class FakeProc:
        pid = 4243

    monkeypatch.setattr(manage.subprocess, "Popen", lambda *a, **k: FakeProc())

    # Đẩy deadline vượt ngay lập tức: time.time() trả về số tăng dần lớn để while time.time() < deadline false ngay.
    times = iter([1000.0, 100000.0, 100000.0])
    monkeypatch.setattr(manage.time, "time", lambda: next(times, 100000.0))
    monkeypatch.setattr(manage.time, "sleep", lambda s: None)
    rc = manage.main(["start", "--host", "127.0.0.1", "--port", "3"])
    assert rc == 1
    assert "healthcheck quá hạn" in capsys.readouterr().out


# ---------- cmd_stop ----------


def test_cmd_stop_no_pid_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    assert manage.main(["stop"]) == 0
    assert "Không có PID file" in capsys.readouterr().out


def test_cmd_stop_bad_pid_content_still_removes_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    pid_file = manage.get_pid_file()
    pid_file.write_text("not-a-number", encoding="utf-8")
    monkeypatch.setattr(manage, "_pid_is_gateway", lambda pid: True)
    assert manage.main(["stop"]) == 0
    assert not pid_file.exists()


def test_cmd_stop_windows_taskkill_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(manage, "_pid_is_gateway", lambda pid: True)
    calls = []
    monkeypatch.setattr(
        manage.subprocess, "run",
        lambda *a, **k: calls.append(a) or types.SimpleNamespace(returncode=0),
    )
    pid_file = manage.get_pid_file()
    pid_file.write_text("555", encoding="utf-8")
    assert manage.main(["stop"]) == 0
    assert calls and calls[0][0][0] == "taskkill"
    assert "Đã dừng gateway" in capsys.readouterr().out
    assert not pid_file.exists()


def test_cmd_stop_kill_raises_is_caught(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(manage, "_pid_is_gateway", lambda pid: True)

    def boom(*a, **k):
        raise OSError("no such process")

    monkeypatch.setattr(manage.subprocess, "run", boom)
    pid_file = manage.get_pid_file()
    pid_file.write_text("556", encoding="utf-8")
    assert manage.main(["stop"]) == 0
    assert "không chạy hoặc không dừng được" in capsys.readouterr().out
    assert not pid_file.exists()


def test_cmd_stop_pid_not_gateway_only_removes_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    monkeypatch.setattr(manage, "_pid_is_gateway", lambda pid: False)
    killed = []
    monkeypatch.setattr(manage.os, "kill", lambda pid, sig: killed.append(pid))
    pid_file = manage.get_pid_file()
    pid_file.write_text("558", encoding="utf-8")
    assert manage.main(["stop"]) == 0
    assert killed == []
    assert "không phải tiến trình gateway" in capsys.readouterr().out
    assert not pid_file.exists()


def test_cmd_stop_posix_uses_os_kill(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(manage, "_pid_is_gateway", lambda pid: True)
    killed = []
    monkeypatch.setattr(manage.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    pid_file = manage.get_pid_file()
    pid_file.write_text("557", encoding="utf-8")
    assert manage.main(["stop"]) == 0
    assert killed == [(557, manage.signal.SIGTERM)]


# ---------- cmd_status ----------


def test_cmd_status_prints_pool_and_reflects_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    monkeypatch.setattr(manage, "is_server_running", lambda host, port: True)
    mgr = gw_auth.AntigravityAuthManager()
    c1 = gw_auth.AntigravityCredentials(access_token="t", email="a@example.com", project_id="p", refresh_token="r")
    c2 = gw_auth.AntigravityCredentials(access_token="t2", email="b@example.com", project_id="p2")
    mgr.save_credentials(c1)
    mgr.save_credentials(c2)
    mgr.mark_account_unavailable(c1, 429, retry_after="120")
    rc = manage.main(["status", "--host", "127.0.0.1", "--port", "1123"])
    out = capsys.readouterr().out
    assert "ONLINE" in out
    assert "a@example.com" in out and "b@example.com" in out
    assert "COOLDOWN" in out
    assert "SẴN SÀNG" in out
    assert "1/2 tài khoản sẵn sàng" in out
    assert rc == 0


def test_cmd_status_offline_or_no_accounts_available_is_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    monkeypatch.setattr(manage, "is_server_running", lambda host, port: False)
    rc = manage.main(["status", "--host", "127.0.0.1", "--port", "1123"])
    assert rc == 1
    assert "Pool trống" in capsys.readouterr().out


# ---------- cmd_login ----------


def test_cmd_login_success(monkeypatch, capsys):
    creds = gw_auth.AntigravityCredentials(access_token="t", email="me@example.com", project_id="proj")

    class FakeMgr:
        token_file = "token-file-path"

        def login_pkce(self, open_browser):
            return creds

        def load_all_stored_credentials(self):
            return [creds]

    monkeypatch.setattr(manage, "AntigravityAuthManager", lambda: FakeMgr())
    assert manage.main(["login"]) == 0
    out = capsys.readouterr().out
    assert "Đăng nhập thành công" in out and "me@example.com" in out


def test_cmd_login_failure(monkeypatch, capsys):
    class FakeMgr:
        def login_pkce(self, open_browser):
            raise RuntimeError("state mismatch")

    monkeypatch.setattr(manage, "AntigravityAuthManager", lambda: FakeMgr())
    assert manage.main(["login", "--no-browser"]) == 1
    assert "thất bại" in capsys.readouterr().out


def test_pid_is_gateway_linux_filenotfound_returns_false(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def raise_fnf(self):
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(manage.Path, "read_bytes", raise_fnf)
    assert manage._pid_is_gateway(99999) is False


def test_pid_is_gateway_linux_cmdline_without_gateway_marker(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(manage.Path, "read_bytes", lambda self: b"python3\0-m\0something_else\0")
    assert manage._pid_is_gateway(1) is False


# ---------- _gateway_backends / _cli_backends: nhánh bỏ qua entry hỏng ----------


def test_gateway_backends_skips_non_dict_entries():
    data = {"backends": ["not-a-dict", {"provider": "openai", "base_url": "http://127.0.0.1:1123/v1", "name": "gw"}]}
    found = manage._gateway_backends(data, "127.0.0.1:1123")
    assert found == [("gw", {})]


def test_gateway_backends_single_provider_form_matches():
    data = {"provider": "openai", "base_url": "http://127.0.0.1:1123/v1", "models": {"strong": "x"}}
    found = manage._gateway_backends(data, "127.0.0.1:1123")
    assert found == [("(provider đơn)", {"strong": "x"})]


def test_cli_backends_skips_non_dict_entries():
    data = {"backends": ["oops", {"provider": "claude-code", "name": "c", "models": {"strong": "x"}}]}
    out = manage._cli_backends(data)
    assert out == [("c", "claude-code", {"strong": "x"})]


def test_probe_cli_timeout_expired(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=120.0)

    monkeypatch.setattr("subprocess.run", raise_timeout)
    verdict, note = manage._probe_cli("claude-code", "claude-opus-5")
    assert verdict == "LỖI" and "120s" in note


def test_discover_catalog_returns_empty_when_no_models_ever_found(monkeypatch):
    class FakeMgr:
        def resolve_credential_candidates(self):
            return [types.SimpleNamespace(access_token="t", project_id="p", email="a@x")]

    monkeypatch.setattr(manage, "AntigravityAuthManager", lambda: FakeMgr())
    monkeypatch.setattr(manage, "fetch_available_models", lambda token, project, **k: [])
    assert manage._discover_catalog() == []


# ---------- cmd_models: nhánh alias chết + đọc yaml lỗi ----------


def test_models_reports_dangling_alias_when_live_catalog_misses_alias_target(tmp_path, monkeypatch, capsys):
    target = tmp_path / "llm.yaml"
    target.write_text("backends: []\n", encoding="utf-8")
    # Catalog "sống" không chứa bất kỳ đích alias nào -> mọi alias đều dangling.
    monkeypatch.setattr(manage, "_discover_catalog", lambda: [{"id": "brand-new-model", "name": "N", "code_assist_model": "brand-new-model"}])
    try:
        assert manage.main(["models", "--check", str(target)]) == 0
    finally:
        # cmd_models gọi set_discovered_models(live) thật (không mock) -> ô nhiễm state toàn cục
        # của gateway.client, ảnh hưởng các test khác (vd. test_server.py::test_health_and_models).
        from gateway.client import set_discovered_models

        set_discovered_models([])
    out = capsys.readouterr().out
    assert "Alias trỏ vào model upstream không còn khai" in out


def test_models_target_missing_file_is_skipped(tmp_path, capsys):
    missing = tmp_path / "no-such-llm.yaml"
    assert manage.main(["models", "--check", str(missing), "--offline"]) == 0
    assert "không có file, bỏ qua" in capsys.readouterr().out


def test_models_target_yaml_parse_error_counts_as_problem(tmp_path, capsys):
    target = tmp_path / "llm.yaml"
    target.write_text("key: [unterminated\n", encoding="utf-8")
    assert manage.main(["models", "--check", str(target), "--offline"]) == 1
    assert "đọc lỗi" in capsys.readouterr().out


# ---------- _discover_catalog: lỗi lấy tài khoản ----------


def test_discover_catalog_returns_empty_when_resolve_fails(monkeypatch, capsys):
    class FakeMgr:
        def resolve_credential_candidates(self):
            raise RuntimeError("no accounts")

    monkeypatch.setattr(manage, "AntigravityAuthManager", lambda: FakeMgr())
    assert manage._discover_catalog() == []
    assert "Không lấy được tài khoản" in capsys.readouterr().out


# ---------- main(): stdout/stderr reconfigure + dispatch ----------
#
# Vòng lặp reconfigure(encoding="utf-8", ...) trong main() đã chạy (nhánh reconfigure thành công)
# ở MỌI lần gọi manage.main() khắp bộ test này, vì sys.stdout/sys.stderr thật đều có .reconfigure().
# Từng thử thay sys.stdout/sys.stderr toàn cục bằng object giả để bắt riêng nhánh "reconfigure lỗi
# bị nuốt" — nhưng việc đó làm sai lệch số liệu coverage của các test aiohttp async chạy SAU nó
# trong cùng phiên (dường như là hạn chế của coverage.py khi tracer bám theo sys.stdout/stderr bị
# thay đổi giữa chừng, không phải lỗi thật). Vì nhánh "thành công" đã được phủ gián tiếp và nhánh
# suppress không đáng đánh đổi rủi ro đó, không test bằng cách thay sys.stdout/sys.stderr nữa.


def test_dunder_main_guard_calls_main_and_exits(monkeypatch, tmp_path):
    """`if __name__ == "__main__": sys.exit(main())` cuối manage.py — chạy qua runpy để dòng đó
    thực sự thực thi trong tiến trình pytest (khác với gateway/__main__.py, vốn bị loại khỏi coverage
    vì phải chạy bằng process thật)."""
    import runpy

    monkeypatch.setattr(sys, "argv", ["gateway-manage", "models", "--check", str(tmp_path / "nope.yaml"), "--offline"])
    with pytest.raises(SystemExit) as e:
        runpy.run_module("gateway.manage", run_name="__main__")
    assert e.value.code == 0  # file llm.yaml không tồn tại -> bỏ qua, không tính là lỗi
