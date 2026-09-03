"""CLI: setup ghi llm.yaml; reset/logout thao tác pool."""

from __future__ import annotations

import os
import sys
import types

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


def test_models_lists_and_accepts_valid_llm_yaml(tmp_path, capsys):
    target = tmp_path / "llm.yaml"
    target.write_text(
        "provider: claude-code\n"
        "backends:\n"
        "  - {name: claude-sub, provider: claude-code, models: {strong: claude-opus-5}}\n"
        "  - name: antigravity\n"
        "    provider: openai\n"
        "    base_url: http://127.0.0.1:8100/v1\n"
        "    models: {strong: claude-sonnet-4-6, standard: gemini-3.7-flash, light: gemini-3.7-flash-low}\n",
        encoding="utf-8",
    )
    assert manage.main(["models", "--check", str(target)]) == 0
    out = capsys.readouterr().out
    assert "gemini-3-flash-agent" in out                 # bảng model hỗ trợ
    assert "KHÔNG HỖ TRỢ" not in out
    # Backend CLI được liệt kê nhưng không phán xét — gateway không đứng giữa chúng.
    assert "claude-opus-5" in out and "--probe-cli" in out


def test_models_flags_unknown_model_in_llm_yaml(tmp_path, capsys):
    target = tmp_path / "llm.yaml"
    target.write_text(
        "backends:\n"
        "  - name: antigravity\n"
        "    provider: openai\n"
        "    base_url: http://127.0.0.1:8100/v1\n"
        "    models: {strong: gemini-9-ultra, standard: gemini-3.7-flash}\n",
        encoding="utf-8",
    )
    assert manage.main(["models", "--check", str(target)]) == 1
    out = capsys.readouterr().out
    assert "gemini-9-ultra" in out and "KHÔNG HỖ TRỢ" in out


def test_models_ignores_backend_pointing_elsewhere(tmp_path, capsys):
    target = tmp_path / "llm.yaml"
    target.write_text(
        "provider: openai\nbase_url: https://api.openai.com/v1\nmodels: {strong: gpt-5.6}\n", encoding="utf-8"
    )
    assert manage.main(["models", "--check", str(target)]) == 0
    assert "không có backend nào để đối chiếu" in capsys.readouterr().out


def test_models_probe_reports_dead_model_and_exits_1(tmp_path, monkeypatch, capsys):
    """Probe: model gateway khai hợp lệ nhưng upstream đã nghỉ hưu → exit 1, kể cả khi llm.yaml sạch."""
    target = tmp_path / "llm.yaml"
    target.write_text("backends: []\n", encoding="utf-8")
    monkeypatch.setattr(manage, "_probe_antigravity", lambda ids: len(ids))
    assert manage.main(["models", "--check", str(target), "--probe"]) == 1
    assert "nghỉ hưu" in capsys.readouterr().out


def test_models_probe_cli_flags_failing_cli_model(tmp_path, monkeypatch, capsys):
    target = tmp_path / "llm.yaml"
    target.write_text(
        "backends:\n  - {name: claude-sub, provider: claude-code, models: {strong: claude-opus-99}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(manage, "_probe_cli", lambda provider, model: ("LỖI", "exit 1: unknown model"))
    assert manage.main(["models", "--check", str(target), "--probe-cli"]) == 1
    assert "claude-opus-99" in capsys.readouterr().out


def test_models_probe_id_does_not_treat_missing_candidate_as_config_error(tmp_path, monkeypatch, capsys):
    """`--probe-id gemini-3.8-flash` dò model chưa ra mắt: báo 'chưa có', không phải cấu hình sai → exit 0."""
    target = tmp_path / "llm.yaml"
    target.write_text("backends: []\n", encoding="utf-8")
    monkeypatch.setattr(manage, "_probe_antigravity", lambda ids: len(ids))
    assert manage.main(["models", "--check", str(target), "--probe", "--probe-id", "gemini-3.8-flash"]) == 0
    assert "không có trên kênh Antigravity" in capsys.readouterr().out


# ---------- probe: xoay tài khoản, nhận diện model chết ----------

class _Creds:
    def __init__(self, email: str) -> None:
        self.email, self.access_token, self.project_id = email, "tok-" + email, "proj"


def _pool(monkeypatch, emails):
    mgr = type("M", (), {"resolve_credential_candidates": lambda self: [_Creds(e) for e in emails]})()
    monkeypatch.setattr(manage, "AntigravityAuthManager", lambda *a, **k: mgr)


def test_probe_rotates_accounts_on_429(monkeypatch, capsys):
    """429 không kết luận được model sống hay chết — phải hỏi tài khoản khác trước khi bỏ cuộc."""
    _pool(monkeypatch, ["a@x", "b@x"])
    seen: list[str] = []

    def probe(model, token, project, **kw):
        seen.append(token)
        return (429, "quota") if token == "tok-a@x" else (200, '{"ok":1}')

    monkeypatch.setattr(manage, "probe_code_assist_model", probe)
    assert manage._probe_antigravity(["gemini-x"]) == 0
    assert seen == ["tok-a@x", "tok-b@x"]
    assert "OK" in capsys.readouterr().out


def test_probe_counts_retired_and_missing_as_broken(monkeypatch, capsys):
    _pool(monkeypatch, ["a@x"])
    bodies = {
        "chet": (404, '{"error":{"code":404}}'),
        "nghi-huu": (200, '{"text":"Gemini 3.5 Flash is no longer available. Please switch to X."}'),
        "song": (200, '{"text":"hi"}'),
    }
    monkeypatch.setattr(manage, "probe_code_assist_model", lambda m, t, p, **k: bodies[m])
    assert manage._probe_antigravity(["chet", "nghi-huu", "song"]) == 2
    out = capsys.readouterr().out
    assert "KHÔNG TỒN TẠI" in out and "NGHỈ HƯU" in out


def test_probe_survives_network_error_and_empty_pool(monkeypatch, capsys):
    _pool(monkeypatch, ["a@x"])

    def boom(*a, **k):
        raise OSError("mạng hỏng")

    monkeypatch.setattr(manage, "probe_code_assist_model", boom)
    manage._probe_antigravity(["gemini-x"])
    assert "LỖI MẠNG" in capsys.readouterr().out

    _pool(monkeypatch, [])
    assert manage._probe_antigravity(["gemini-x"]) == 1
    assert "Pool trống" in capsys.readouterr().out


def test_probe_cli_reports_missing_binary_and_failure(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert manage._probe_cli("claude-code", "claude-opus-5")[0] == "THIẾU CLI"

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: types.SimpleNamespace(returncode=1, stderr="unknown model", stdout=""),
    )
    verdict, note = manage._probe_cli("claude-code", "claude-opus-99")
    assert verdict == "LỖI" and "unknown model" in note

    monkeypatch.setattr("subprocess.run", lambda *a, **k: types.SimpleNamespace(returncode=0, stderr="", stdout="ok"))
    assert manage._probe_cli("claude-code", "claude-opus-5")[0] == "OK"


def test_discover_catalog_falls_through_accounts(monkeypatch):
    _pool(monkeypatch, ["a@x", "b@x"])
    calls: list[str] = []

    def fetch(token, project, **kw):
        calls.append(token)
        if token == "tok-a@x":
            raise OSError("hỏng")
        return [{"id": "gemini-9", "name": "G9", "code_assist_model": "gemini-9"}]

    monkeypatch.setattr(manage, "fetch_available_models", fetch)
    assert manage._discover_catalog() == [{"id": "gemini-9", "name": "G9", "code_assist_model": "gemini-9"}]
    assert calls == ["tok-a@x", "tok-b@x"]
