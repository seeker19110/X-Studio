"""CLI: setup ghi llm.yaml; reset/logout thao tác pool."""

from __future__ import annotations

import os
import sys

import pytest
import yaml

from gateway import auth as gw_auth
from gateway import manage


def test_setup_writes_llm_yaml_preserving_other_keys(tmp_path):
    target = tmp_path / "llm.yaml"
    target.write_text("provider: anthropic\nmodels:\n  strong: claude-opus-5\nextra: {temperature: 0}\n", encoding="utf-8")
    assert manage.main(["setup", "--target", str(target), "--port", "9000"]) == 0
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["provider"] == "openai"
    assert data["base_url"] == "http://127.0.0.1:9000/v1"
    assert data["models"] == {"strong": manage.DEFAULT_STRONG_MODEL, "standard": manage.DEFAULT_STANDARD_MODEL}
    assert data["extra"] == {"temperature": 0}


def test_reset_and_logout(tmp_path, monkeypatch):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    mgr = gw_auth.AntigravityAuthManager()
    c = gw_auth.AntigravityCredentials(access_token="t", email="a@example.com", project_id="p")
    mgr.save_credentials(c)
    mgr.mark_account_unavailable(c, 429)
    assert manage.main(["reset", "a@example.com"]) == 0
    assert mgr.load_stored_credentials().unavailable_until == 0
    assert manage.main(["logout", "a@example.com"]) == 0
    assert manage.main(["logout", "a@example.com"]) == 1
    assert manage.main(["reset"]) == 1


def test_start_warns_when_host_not_loopback(monkeypatch, capsys):
    monkeypatch.setattr(manage, "is_server_running", lambda host, port: True)
    assert manage.main(["start", "--host", "0.0.0.0", "--port", "1"]) == 0
    assert "không phải loopback" in capsys.readouterr().out


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc chỉ có trên Linux")
def test_stop_skips_pid_that_is_not_gateway(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    killed: list[int] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))
    pid_file = manage.get_pid_file()
    # Giả /proc/<pid>/cmdline: PID được tái dùng bởi tiến trình khác → không SIGTERM, chỉ xoá PID file.
    cmdlines = {424242: b"python3\0-m\0something_else\0", 424243: b"python3\0-c\0from gateway.manage import ...\0"}
    real_read_bytes = manage.Path.read_bytes

    def fake_read_bytes(self):
        if str(self).startswith("/proc/"):
            pid = int(self.parts[2])
            if pid not in cmdlines:
                raise FileNotFoundError(str(self))
            return cmdlines[pid]
        return real_read_bytes(self)

    monkeypatch.setattr(manage.Path, "read_bytes", fake_read_bytes)
    pid_file.write_text("424242", encoding="utf-8")
    assert manage.main(["stop"]) == 0
    assert killed == [] and not pid_file.exists()
    assert "không phải tiến trình gateway" in capsys.readouterr().out
    # cmdline chứa "gateway" → SIGTERM như bình thường.
    pid_file.write_text("424243", encoding="utf-8")
    assert manage.main(["stop"]) == 0
    assert killed == [424243]
    assert not manage._pid_is_gateway(999999999)  # /proc không có → không phải gateway


def test_daemon_entry_removes_pid_file_at_exit(tmp_path, monkeypatch):
    monkeypatch.setenv(gw_auth.ENV_HOME, str(tmp_path))
    registered = []
    import atexit

    monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))
    monkeypatch.setattr("gateway.server.run_server", lambda host, port: None)
    pid_file = manage.get_pid_file()
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    manage._run_daemon("127.0.0.1", 1)
    assert len(registered) == 1
    registered[0]()
    assert not pid_file.exists()
