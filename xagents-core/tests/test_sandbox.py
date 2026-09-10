"""Bộ hợp đồng của `xagents_core.sandbox` (K3.2; ADR-0035 của software-company, ADR gốc 0001).

Chuyển sang core cùng lúc với mã theo bất biến 2 của K3: test đơn vị đi theo module. Trước K3.2 file này nằm ở
`Studio-creators/tests/test_sandbox.py`; phần đo *điểm gọi thật* của mỗi công ty (media của studio, workspace/smoke
của company) ở lại nơi cũ vì đó là test tích hợp, không phải test của module này.

Mọi ca dưới đây chạy cho **cả hai backend** — cùng `RunSpec` phải cho cùng `Result` (exit code, cắt output, cờ
timeout), env luôn qua bộ lọc khoá. Backend container dùng `runner=` giả trả `CompletedProcess`, không cần docker
trên CI.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from xagents_core.sandbox import (
    ContainerSandbox,
    Result,
    RunSpec,
    SandboxError,
    SubprocessSandbox,
    clean_env,
    sandbox_from_settings,
    sanitize_env,
)

PY = sys.executable


def _spec(tmp_path: Path, **kw: Any) -> RunSpec:
    return RunSpec(argv=[PY, "-c", "print('x')"], cwd=tmp_path, **kw)


class _Recorder:
    """Sandbox giả: ghi lại `RunSpec` đã nhận và trả `Result` dựng sẵn."""

    def __init__(self, result: Result | None = None, side: Any = None):
        self.name = "recorder"
        self.specs: list[RunSpec] = []
        self._result = result or Result(0, "", "", False, "recorder")
        self._side = side

    def run(self, spec: RunSpec) -> Result:
        self.specs.append(spec)
        if self._side is not None: return self._side(spec)
        return self._result

    def spawn(self, spec: RunSpec) -> Any:  # pragma: no cover - phòng video không spawn
        raise NotImplementedError


def _fake_runner(record: list[dict[str, Any]], code: int = 0, out: str = "OUT", err: str = "ERR",
                 boom: bool = False) -> Any:
    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        record.append({"argv": argv, **kw})
        if boom: raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))
        return subprocess.CompletedProcess(argv, code, out, err)
    return run


# ---------- bộ hợp đồng: hai backend, cùng lời hứa ----------

def _backends(record: list[dict[str, Any]], **kw: Any) -> list[Any]:
    return [SubprocessSandbox(runner=_fake_runner(record, **kw)),
            ContainerSandbox("docker", "python:3.12-slim", runner=_fake_runner(record, **kw))]


@pytest.mark.parametrize("i", [0, 1])
def test_hai_backend_tra_cung_khuon_result(tmp_path, i):
    rec: list[dict[str, Any]] = []
    sb = _backends(rec, code=3, out="A" * 20, err="B" * 20)[i]
    r = sb.run(_spec(tmp_path, max_output=5))
    assert r.exit_code == 3 and r.timed_out is False
    assert r.stdout == "AAAAA" and r.stderr == "BBBBB"      # cắt đuôi đúng max_output
    assert r.sandbox == sb.name and sb.name in {"subprocess", "container:python:3.12-slim",
                                                "container:python:3.12-slim:no-uid"}


@pytest.mark.parametrize("i", [0, 1])
def test_hai_backend_bao_timeout_thay_vi_nem_ngoai_le(tmp_path, i):
    rec: list[dict[str, Any]] = []
    r = _backends(rec, boom=True)[i].run(_spec(tmp_path, timeout=7))
    assert r.timed_out is True and r.exit_code is None and "quá 7" in r.stderr


@pytest.mark.parametrize("i", [0, 1])
def test_hai_backend_khong_bao_gio_chuyen_bien_giong_khoa(tmp_path, monkeypatch, i):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "kín")
    monkeypatch.setenv("STUDIO_LLM_API_KEY", "kín")
    monkeypatch.setenv("PATH_KHONG_PHAI_KHOA", "ok")
    rec: list[dict[str, Any]] = []
    # env=None: sandbox tự lấy clean_env(); nơi gọi quên lọc thì sandbox vẫn lọc.
    _backends(rec)[i].run(RunSpec(argv=["x"], cwd=tmp_path, env=dict(os.environ)))
    blob = repr(rec[0])
    assert "kín" not in blob and "PATH_KHONG_PHAI_KHOA" in blob


def test_sanitize_env_loc_lai_du_noi_goi_da_ban_env_ban(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert "OPENAI_API_KEY" not in clean_env()
    e = sanitize_env({"AWS_SECRET_ACCESS_KEY": "x", "LANG": "vi"})
    assert e == {"LANG": "vi", "PYTHONDONTWRITEBYTECODE": "1"}
    assert "OPENAI_API_KEY" not in sanitize_env(None)   # None → clean_env()


# ---------- argv của container ----------

def test_container_argv_mount_rw_mang_tat_va_env_qua_stdin(tmp_path):
    rec: list[dict[str, Any]] = []
    sb = ContainerSandbox("podman", "img:1", cpus="1", memory="1g", runner=_fake_runner(rec))
    sb.run(RunSpec(argv=["ffmpeg", "-version"], cwd=tmp_path, env={"LANG": "vi"}))
    argv = rec[0]["argv"]
    assert argv[:3] == ["podman", "run", "--rm"] and "--pids-limit" in argv
    assert f"{tmp_path}:/w:rw" in argv and argv[argv.index("-w") + 1] == "/w"
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[-2:] == ["ffmpeg", "-version"] and "--env-file" in argv
    assert "LANG=vi" in rec[0]["input"]        # env đi qua stdin, không hiện trong danh sách tiến trình


def test_container_mount_chi_doc_cho_qc_va_mo_cong_khi_can_mang(tmp_path):
    rec: list[dict[str, Any]] = []
    sb = ContainerSandbox("docker", "img:1", runner=_fake_runner(rec))
    sb.run(RunSpec(argv=["ffprobe"], cwd=tmp_path, read_only=True))
    assert f"{tmp_path}:/w:ro" in rec[0]["argv"]
    sb.run(RunSpec(argv=["srv"], cwd=tmp_path, network=True, port=8080))
    assert rec[1]["argv"][rec[1]["argv"].index("--network") + 1] == "bridge"
    assert "127.0.0.1:8080:8080" in rec[1]["argv"]


def test_container_khi_can_stdin_thi_env_buoc_phai_ra_dong_lenh(tmp_path):
    """`--env-file -` chiếm stdin, mà CommandTTS cần stdin cho văn bản → env quay về `-e` (đánh đổi có chủ ý)."""
    rec: list[dict[str, Any]] = []
    ContainerSandbox("docker", "img:1", runner=_fake_runner(rec)).run(
        RunSpec(argv=["tts"], cwd=tmp_path, env={"LANG": "vi"}, stdin="xin chào"))
    argv = rec[0]["argv"]
    assert "--env-file" not in argv and "-i" in argv and "LANG=vi" in argv
    assert rec[0]["input"] == "xin chào"


def test_container_khong_co_getuid_thi_noi_thang_trong_ten_sandbox(monkeypatch):
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.delattr(os, "getgid", raising=False)
    sb = ContainerSandbox("docker", "img:1")
    assert sb.name == "container:img:1:no-uid" and "-u" not in sb._argv(RunSpec(argv=["x"], cwd=Path(".")))
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 1000, raising=False)
    sb2 = ContainerSandbox("docker", "img:1")
    assert sb2.name == "container:img:1" and "1000:1000" in sb2._argv(RunSpec(argv=["x"], cwd=Path(".")))


# ---------- spawn: tiến trình chạy nền ----------

class _FakeProc:
    def __init__(self) -> None:
        self.stdin = None; self.killed = False; self._rc: int | None = None

    def poll(self) -> int | None: return self._rc
    def kill(self) -> None: self.killed = True; self._rc = -9
    def communicate(self, timeout: float = 0) -> tuple[str, str]: return "", "loi cuoi"


def test_spawn_tra_handle_poll_kill_stderr_cho_ca_hai_backend(tmp_path):
    proc = _FakeProc()
    h = SubprocessSandbox(popen=lambda *a, **k: proc).spawn(_spec(tmp_path))
    assert h.poll() is None
    h.kill(); assert proc.killed and h.poll() == -9
    assert h.stderr_tail(4) == "cuoi" and h.stderr_tail(4) == "cuoi"   # lần hai lấy từ cache

    class _P(_FakeProc):
        def __init__(self) -> None:
            super().__init__()
            self.written: list[str] = []
            self.stdin = type("S", (), {"write": lambda s, t: self.written.append(t), "close": lambda s: None})()

    p2 = _P()
    ContainerSandbox("docker", "img:1", popen=lambda *a, **k: p2).spawn(
        RunSpec(argv=["x"], cwd=tmp_path, env={"LANG": "vi"}))
    assert p2.written == ["LANG=vi\nPYTHONDONTWRITEBYTECODE=1"]


def test_stderr_tail_rong_khi_khong_lay_duoc_dau_ra():
    class _Hang(_FakeProc):
        def communicate(self, timeout: float = 0) -> tuple[str, str]:
            raise subprocess.TimeoutExpired("x", timeout)

    h = SubprocessSandbox(popen=lambda *a, **k: _Hang()).spawn(RunSpec(argv=["x"], cwd=Path(".")))
    assert h.stderr_tail(10) == ""




# ---------- chọn backend: core nhận giá trị đã đọc sẵn, không tự đọc env ----------

def test_subprocess_va_auto_theo_binary_co_hay_khong():
    assert sandbox_from_settings("subprocess", "docker", "img", "X_SANDBOX", which=lambda _: "/usr/bin/docker").name == "subprocess"
    assert sandbox_from_settings("auto", "docker", "img", "X_SANDBOX", which=lambda _: None).name == "subprocess"
    assert sandbox_from_settings("auto", "docker", "img", "X_SANDBOX", which=lambda _: "/x").name.startswith("container:")


def test_khai_container_va_co_binary_thi_dung_image_da_khai():
    sb = sandbox_from_settings("container", "podman", "alpine:3", "X_SANDBOX", which=lambda _: "/usr/bin/podman")
    assert isinstance(sb, ContainerSandbox) and sb.runtime == "podman" and sb.image == "alpine:3"


def test_khai_container_ma_thieu_binary_thi_bao_loi_chu_khong_tut_hang():
    """Fail-closed: `container` khai đích danh mà thiếu binary là LỖI, không bao giờ âm thầm về subprocess."""
    with pytest.raises(SandboxError, match="không tìm thấy `podman`"):
        sandbox_from_settings("container", "podman", "img", "X_SANDBOX", which=lambda _: None)


def test_cau_loi_goi_dung_ten_bien_cua_ben_goi():
    """Core không có prefix của riêng mình: company và studio đặt hai biến khác nhau, người đọc lỗi cần biết gõ
    biến nào. Đây là lý do `env_var` là tham số chứ không phải hằng trong core."""
    for var in ("COMPANY_SANDBOX", "STUDIO_SANDBOX"):
        with pytest.raises(SandboxError) as e:
            sandbox_from_settings("container", "docker", "img", var, which=lambda _: None)
        assert f"{var}=container" in str(e.value) and f"đặt {var}=subprocess" in str(e.value)


def test_che_do_la_thi_bao_loi_thay_vi_doan():
    with pytest.raises(SandboxError, match="không hợp lệ"):
        sandbox_from_settings("kín", "docker", "img", "X_SANDBOX", which=lambda _: "/x")


def test_container_spawn_khi_popen_khong_mo_duoc_stdin(tmp_path):
    """`Popen(stdin=PIPE)` vẫn có thể trả `proc.stdin is None` — hết file descriptor, hay bị wrapper thay thế.

    Không có vế `is not None` thì đây là `AttributeError` ngay khi spawn, và ContainerSandbox mất luôn đường
    báo lỗi tử tế: tiến trình ĐÃ khởi động rồi mới nổ, nên container ở lại mà không ai giữ handle."""
    proc = _FakeProc()                      # `stdin` là None
    h = ContainerSandbox("docker", "img:1", popen=lambda *a, **k: proc).spawn(
        RunSpec(argv=["x"], cwd=tmp_path, env={"LANG": "vi"}))

    assert h.poll() is None                 # vẫn trả handle dùng được, không nổ
