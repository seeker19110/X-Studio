"""Sandbox tiến trình (K2, ADR-0035 của software-company; `src/studio/sandbox.py` là bản tạm tới K3.2).

Hai phần:

1. **Bộ hợp đồng** chạy cho CẢ HAI backend — cùng `RunSpec` phải cho cùng `Result` (exit code, cắt output, cờ
   timeout), env luôn qua bộ lọc khoá, và `sandbox_from_config` fail-closed khi khai `container` mà thiếu binary.
   Backend container dùng `runner=` giả trả `CompletedProcess`: không cần docker trên máy CI.
2. **Ba điểm gọi thật của phòng video đi qua sandbox chưa** — `CommandTTS`, `FFmpegAssembler._run`, `qc._run`.
   Chúng dùng sandbox ghi lại `RunSpec` chứ không vá `subprocess.run`, nên nếu ai đó gọi thẳng subprocess trở lại
   thì test đỏ (đo hai chiều: bỏ `sandbox.run` trong `media.py` → 4 test dưới đây đỏ).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from studio import media, qc
from studio.media import CommandTTS, FFmpegAssembler, MediaConfig, MediaError, make_media
from studio.sandbox import (
    Result,
    RunSpec,
    SandboxError,
    sandbox_from_config,
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


# ---------- chọn backend: mặc định subprocess, fail-closed ----------

def test_mac_dinh_la_subprocess_du_may_co_docker(monkeypatch):
    """KHÁC software-company: media không tự bật container, vì ffmpeg đọc/ghi ngoài cwd được mount."""
    monkeypatch.delenv("STUDIO_SANDBOX", raising=False)
    assert sandbox_from_config(MediaConfig(), which=lambda b: "/usr/bin/docker").name == "subprocess"
    assert sandbox_from_config(None, which=lambda b: "/usr/bin/docker").name == "subprocess"


def test_khai_container_ma_thieu_binary_thi_bao_loi_chu_khong_tut_hang(monkeypatch):
    monkeypatch.delenv("STUDIO_SANDBOX", raising=False)
    cfg = MediaConfig(render={"sandbox": {"mode": "container", "runtime": "podman"}})
    with pytest.raises(SandboxError, match="không tìm thấy `podman`"):
        sandbox_from_config(cfg, which=lambda b: None)
    assert sandbox_from_config(cfg, which=lambda b: "/usr/bin/podman").name.startswith("container:python:3.12-slim")


def test_auto_va_che_do_la(monkeypatch):
    monkeypatch.delenv("STUDIO_SANDBOX", raising=False)
    cfg = MediaConfig(render={"sandbox": {"mode": "auto", "image": "ffmpeg:7"}})
    assert sandbox_from_config(cfg, which=lambda b: "/usr/bin/docker").name.startswith("container:ffmpeg:7")
    assert sandbox_from_config(cfg, which=lambda b: None).name == "subprocess"
    with pytest.raises(SandboxError, match="không hợp lệ"):
        sandbox_from_config(MediaConfig(render={"sandbox": {"mode": "chroot"}}), which=lambda b: None)


def test_bien_moi_truong_thang_file_cau_hinh(monkeypatch):
    monkeypatch.setenv("STUDIO_SANDBOX", "container")
    monkeypatch.setenv("STUDIO_SANDBOX_RUNTIME", "podman")
    monkeypatch.setenv("STUDIO_SANDBOX_IMAGE", "vi/ffmpeg:1")
    cfg = MediaConfig(render={"sandbox": {"mode": "subprocess"}})
    sb = sandbox_from_config(cfg, which=lambda b: f"/usr/bin/{b}")
    assert sb.name.startswith("container:vi/ffmpeg:1") and sb.runtime == "podman"


# ---------- ba điểm gọi thật của phòng video ----------

def test_command_tts_di_qua_sandbox_voi_env_da_loc_va_van_ban_o_stdin(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "kín")
    rec = _Recorder(side=lambda spec: (Path(spec.argv[-1]).write_bytes(b"WAV"), Result(0, "", "", False, "recorder"))[1])
    cfg = MediaConfig(tts={"provider": "command", "command": "piper -f {out}", "timeout_s": 42})
    r = CommandTTS(cfg, sandbox=rec).synthesize("xin chào", {}, tmp_path / "S1.wav")
    spec = rec.specs[0]
    assert r.provider == "command" and spec.argv[0] == "piper"
    assert spec.stdin == "xin chào" and spec.timeout == 42
    assert "ELEVENLABS_API_KEY" not in spec.env and spec.env["PYTHONIOENCODING"] == "utf-8"


def test_command_tts_bao_timeout_ro_rang(tmp_path):
    rec = _Recorder(Result(None, "", "quá 300.0s", True, "recorder"))
    cfg = MediaConfig(tts={"provider": "command", "command": "piper -f {out}"})
    with pytest.raises(MediaError, match=r"TTS quá 300\.0s"):
        CommandTTS(cfg, sandbox=rec).synthesize("x", {}, tmp_path / "a.wav")


def test_lenh_tts_that_khong_thay_khoa_cua_phong(tmp_path, monkeypatch):
    """Đo hai chiều bằng tiến trình thật: bỏ `clean_env()` trong `CommandTTS.synthesize` → test này ĐỎ."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "kín")
    s = tmp_path / "cmd.py"
    s.write_text("import os, sys, wave\n"
                 "assert 'ELEVENLABS_API_KEY' not in os.environ, 'lệnh TTS thấy khoá!'\n"
                 "w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)\n"
                 "w.writeframes(b'\\x00' * 8000); w.close()\n", encoding="utf-8")
    cfg = MediaConfig(tts={"provider": "command", "command": f'"{PY}" "{s}" {{out}}'})
    assert CommandTTS(cfg).synthesize("x", {}, tmp_path / "S1.wav").path.is_file()


def test_ffmpeg_co_tran_thoi_gian_va_bao_dung_khoa_cau_hinh(tmp_path, monkeypatch):
    """ffmpeg treo trước đây treo luôn cả phòng: không ai giết. Bỏ `timeout` trong `_run` → test này ĐỎ."""
    monkeypatch.setattr(media.shutil, "which", lambda b: f"/usr/bin/{b}")
    rec = _Recorder(Result(None, "", "quá 30.0s", True, "recorder"))
    # trần KHÁC mặc định 600: quên chuyền `timeout=self.timeout` xuống RunSpec là test đỏ, không phải trùng số.
    with pytest.raises(MediaError, match=r"ffmpeg quá 30\.0s \(media\.yaml render\.timeout_s\)"):
        FFmpegAssembler(timeout=30, sandbox=rec).assemble([(tmp_path / "i.png", tmp_path / "a.wav", 1.0)],
                                                          tmp_path / "o.mp4", 30, "640x360")
    assert rec.specs[0].timeout == 30.0 and rec.specs[0].cwd == tmp_path
    assert "ELEVENLABS_API_KEY" not in rec.specs[0].env


def test_timeout_va_sandbox_cua_ffmpeg_lay_tu_media_yaml(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.delenv("STUDIO_SANDBOX", raising=False)
    suite = make_media(MediaConfig(video={"provider": "ffmpeg"}, render={"timeout_s": 30}))
    assert suite.video.timeout == 30.0 and suite.sandbox.name == "subprocess"
    assert suite.video.sandbox is suite.sandbox


def test_qc_do_qua_sandbox_chi_doc(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "kín")
    monkeypatch.setattr(qc.shutil, "which", lambda b: f"/usr/bin/{b}")
    f = tmp_path / "v.mp4"; f.write_bytes(b"x")
    rec = _Recorder(Result(0, "{}", "", False, "recorder"))
    monkeypatch.setattr(qc, "qc_sandbox", lambda: rec)
    qc.probe(f)
    spec = rec.specs[0]
    assert spec.read_only is True and "ELEVENLABS_API_KEY" not in spec.env and spec.timeout == qc.TIMEOUT_S
    assert spec.max_output > 6000          # stderr của ffmpeg LÀ số đo, cắt 6000 là mất chính con số cần đọc


def test_qc_bao_het_gio_thay_vi_treo(monkeypatch):
    monkeypatch.setattr(qc, "qc_sandbox", lambda: _Recorder(Result(None, "", "", True, "recorder")))
    assert qc._run(["ffprobe"]) == (1, "", f"quá {qc.TIMEOUT_S}s")


def test_qc_dung_mot_sandbox_duy_nhat_cho_ca_phien(monkeypatch):
    monkeypatch.setattr(qc, "_SANDBOX", None)
    monkeypatch.delenv("STUDIO_SANDBOX", raising=False)
    first = qc.qc_sandbox()
    assert first is qc.qc_sandbox() and first.name == "subprocess"


def test_audit_render_noi_ro_da_chay_trong_sandbox_nao(tmp_path):
    """Người duyệt publish thấy video này dựng bằng tiến trình trần hay trong container."""
    import json

    from studio.bus import InMemoryBus
    from studio.events import Scene, SceneManifest
    from studio.renderer import Renderer

    bus = InMemoryBus()
    r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    r.render(SceneManifest(video_id="V1", scenes=[Scene(scene_id="S1", order=0, narration="Một câu.",
                                                        visual_prompt="bàn")]))
    ev = next(e for e in bus.replay("audit-log") if e.payload["action"] == "render.draft")
    assert json.loads(ev.payload["evidence"])["sandbox"] == "subprocess"
