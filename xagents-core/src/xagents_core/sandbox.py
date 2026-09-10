"""Sandbox tiến trình cho lệnh con — lõi chung hai công ty (ADR-0035 của software-company, ADR gốc 0001 K3.2).

Trước ADR-0035, "sandbox" chỉ là *đường dẫn + env*: lệnh của khách (lint/test theo stack, lệnh khởi động do model
viết ra) và lệnh media (TTS, ffmpeg, ffprobe) chạy bằng quyền người vận hành và thấy cả `HOME`. Module này gói mọi
điểm gọi subprocess của cả hai công ty sau một giao diện duy nhất, để đổi sang container mà không sửa nơi gọi.

Hai backend: `SubprocessSandbox` (giữ NGUYÊN hành vi cũ — cùng cách cắt output, cùng timeout, cùng `clean_env`) và
`ContainerSandbox` (docker/podman `run --rm`, mạng tắt mặc định). **Fail-closed**: khai đích danh `container` mà
không có binary thì `SandboxError`, không bao giờ âm thầm tụt về subprocess.

Git KHÔNG đi qua đây (ADR-0035): argv hard-code, hook đã bị vô hiệu, push cần credential của người vận hành.

`RunSpec` là hợp của nhu cầu hai bên, mỗi trường có đúng một chỗ dùng thật:

- `network` + `port` — `company.smoke` probe 127.0.0.1 trong container.
- `stdin` — `studio.media.CommandTTS` đưa văn bản vào stdin lệnh TTS. Với `ContainerSandbox`, stdin đã bị
  `--env-file -` chiếm, nên khi có `stdin` thì env buộc quay về `-e KEY=VALUE` (giá trị **hiện trong danh sách
  tiến trình** của máy — đánh đổi ghi ở đây để không ai tưởng là kín).
- `read_only` — `studio.qc` chỉ đo file, mount `:ro`.

**Chọn backend là việc của công ty, không phải của core**: `sandbox_from_settings` dưới đây nhận mode/runtime/image
đã đọc sẵn, còn *đọc từ đâu* thì mỗi bên tự làm — nguồn cấu hình khác nhau (`cfg.sandbox` của company vs
`media.yaml render.sandbox` của studio), tên biến môi trường khác nhau, và **mặc định cũng khác**: company `auto`,
studio `subprocess`. Studio cố ý không `auto` vì ffmpeg/ffprobe nhận đường dẫn tuyệt đối trải trên nhiều thư mục
mà container chỉ thấy `cwd` được mount — muốn container thì phải khai đích danh và tự lo mount.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# Hợp đồng của shim hai công ty (`company.sandbox`, `studio.sandbox`): `import *` chỉ mang tên trong đây.
__all__ = ["SECRET_ENV", "ContainerSandbox", "Handle", "Result", "RunSpec", "Sandbox", "SandboxError",
           "SubprocessSandbox", "clean_env", "sandbox_from_settings", "sanitize_env"]

SECRET_ENV = re.compile(
    r"(API_?KEY|TOKEN|SECRET|PASSW(OR)?D|CREDENTIAL|ACCESS_KEY|PRIVATE_KEY|SESSION_KEY|SIGNING_KEY|AUTH(?!OR)"
    r"|_URL$|_URI$|_DSN$|DATABASE|CONNECTION_STRING|SSH_AUTH_SOCK|^GITHUB_|^GH_|^NPM_|^PYPI_|^AWS_|^AZURE_|^GOOGLE_"
    r"|^OPENAI_|^ANTHROPIC_|^COMPANY_LLM|^STUDIO_LLM|^CLAUDE_CONFIG_DIR$|^CODEX_HOME$)",
    re.IGNORECASE)


def clean_env() -> dict[str, str]:
    """Env cho lệnh con: bỏ mọi biến trông như khoá. Lệnh TTS cục bộ (Piper, edge-tts) và ffmpeg là mã của người
    khác chạy dưới quyền người vận hành — không có lý do gì để chúng thấy `ELEVENLABS_API_KEY`."""
    return {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)} | {"PYTHONDONTWRITEBYTECODE": "1"}


class SandboxError(Exception):
    """Không dựng được sandbox đã yêu cầu. Cố ý là lỗi, không phải cảnh báo (fail-closed)."""


@dataclass(frozen=True)
class RunSpec:
    """Một lệnh cần chạy. `network=False` là mặc định; `read_only=True` mount cwd `:ro` (qc chỉ đo, không ghi)."""
    argv: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = 600.0
    network: bool = False
    port: int | None = None
    max_output: int = 6000
    stdin: str | None = None
    read_only: bool = False


@dataclass(frozen=True)
class Result:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    sandbox: str


@runtime_checkable
class Handle(Protocol):
    """Tiến trình đang chạy: poll trong lúc chờ, giết, rồi lấy đuôi stderr."""
    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def stderr_tail(self, n: int) -> str: ...


class Sandbox(Protocol):
    name: str
    def run(self, spec: RunSpec) -> Result: ...
    def spawn(self, spec: RunSpec) -> Handle: ...


def sanitize_env(env: dict[str, str] | None) -> dict[str, str]:
    """Env cuối cùng của lệnh con. Lọc lại lần nữa ngay tại sandbox dù nơi gọi đã `clean_env()`: sandbox là chỗ
    cuối cùng biến môi trường đi qua, không dựa vào kỷ luật của nơi gọi."""
    base = clean_env() if env is None else dict(env)
    return {k: v for k, v in base.items() if not SECRET_ENV.search(k)} | {"PYTHONDONTWRITEBYTECODE": "1"}


class _ProcHandle:
    """Bọc `Popen` đúng vòng đời: poll → kill → communicate(timeout=5) → đuôi stderr."""

    def __init__(self, proc: Any):
        self.proc = proc
        self._tail = ""

    def poll(self) -> int | None:
        rc = self.proc.poll()
        return None if rc is None else int(rc)

    def kill(self) -> None:
        self.proc.kill()

    def stderr_tail(self, n: int) -> str:
        if not self._tail:
            try:
                _, err = self.proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, ValueError):
                err = ""
            self._tail = err or ""
        return self._tail[-n:]


class SubprocessSandbox:
    """Hành vi hiện tại: tiến trình con của chính người vận hành, cô lập bằng cwd + env đã lọc khoá."""

    def __init__(self, runner: Any = subprocess.run, popen: Any = subprocess.Popen):
        self.name = "subprocess"
        self._runner, self._popen = runner, popen

    def run(self, spec: RunSpec) -> Result:
        try:
            r = self._runner(spec.argv, cwd=str(spec.cwd), capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=spec.timeout, env=sanitize_env(spec.env), input=spec.stdin)
        except subprocess.TimeoutExpired:
            return Result(None, "", f"quá {spec.timeout}s", True, self.name)
        return Result(int(r.returncode), (r.stdout or "")[-spec.max_output:], (r.stderr or "")[-spec.max_output:],
                      False, self.name)

    def spawn(self, spec: RunSpec) -> Handle:
        return _ProcHandle(self._popen(spec.argv, cwd=str(spec.cwd), env=sanitize_env(spec.env),
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                       encoding="utf-8", errors="replace"))


class ContainerSandbox:
    """`docker`/`podman run --rm` với cwd mount vào `/w`, mạng tắt, hạn mức pid/cpu/ram.

    Env đi qua `--env-file -` (stdin) chứ không phải `-e`: giá trị không hiện trong danh sách tiến trình của máy.
    Ngoại lệ: `RunSpec.stdin` cần chính stdin đó, lúc ấy env buộc phải quay về `-e` (xem docstring module)."""

    def __init__(self, runtime: str, image: str, cpus: str = "2", memory: str = "2g",
                 runner: Any = subprocess.run, popen: Any = subprocess.Popen):
        self.runtime, self.image, self.cpus, self.memory = runtime, image, cpus, memory
        self._runner, self._popen = runner, popen
        self.name = f"container:{image}" + ("" if self._uid() else ":no-uid")

    @staticmethod
    def _uid() -> str | None:
        """Windows không có `os.getuid` → bỏ cờ `-u` (container chạy user mặc định của image) và nói thẳng trong
        tên sandbox để audit không tưởng là đã hạ quyền."""
        getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
        if getuid is None or getgid is None:
            return None
        return f"{getuid()}:{getgid()}"

    def _argv(self, spec: RunSpec) -> list[str]:
        base = [self.runtime, "run", "--rm", "--pids-limit", "256", "--cpus", self.cpus, "--memory", self.memory]
        uid = self._uid()
        if uid: base += ["-u", uid]
        base += ["-v", f"{spec.cwd}:/w:{'ro' if spec.read_only else 'rw'}", "-w", "/w"]
        if spec.stdin is None:
            base += ["--env-file", "-"]
        else:
            base += ["-i"]
            for k, v in sanitize_env(spec.env).items():
                base += ["-e", f"{k}={v}"]
        base += (["--network", "bridge", "-p", f"127.0.0.1:{spec.port}:{spec.port}"] if spec.network
                 else ["--network", "none"])
        return [*base, self.image, *spec.argv]

    @staticmethod
    def _input(spec: RunSpec) -> str:
        # `--env-file -` đọc từng dòng KEY=VALUE trên stdin; giá trị nhiều dòng không hợp lệ nên bỏ.
        if spec.stdin is not None:
            return spec.stdin
        return "\n".join(f"{k}={v}" for k, v in sanitize_env(spec.env).items() if "\n" not in v)

    def run(self, spec: RunSpec) -> Result:
        try:
            r = self._runner(self._argv(spec), input=self._input(spec), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=spec.timeout)
        except subprocess.TimeoutExpired:
            return Result(None, "", f"quá {spec.timeout}s", True, self.name)
        return Result(int(r.returncode), (r.stdout or "")[-spec.max_output:], (r.stderr or "")[-spec.max_output:],
                      False, self.name)

    def spawn(self, spec: RunSpec) -> Handle:
        proc = self._popen(self._argv(spec), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        if proc.stdin is not None:
            proc.stdin.write(self._input(spec))
            proc.stdin.close()
        return _ProcHandle(proc)


def sandbox_from_settings(mode: str, runtime: str, image: str, env_var: str,
                          which: Any = shutil.which) -> Sandbox:
    """Dựng backend từ ba giá trị đã đọc sẵn. Core cố ý KHÔNG tự đọc `os.environ` hay file cấu hình: nguồn và tên
    biến khác nhau giữa hai công ty (xem docstring module), nên nơi gọi đọc rồi truyền vào.

    `env_var` chỉ để dựng câu lỗi đúng tên biến người vận hành phải đặt — người đọc lỗi cần biết gõ gì, và
    `COMPANY_SANDBOX` hay `STUDIO_SANDBOX` là hai câu trả lời khác nhau.

    `auto` chọn container nếu có binary; `container` khai đích danh mà thiếu binary → `SandboxError`
    (fail-closed — không bao giờ âm thầm tụt về subprocess)."""
    if mode == "subprocess":
        return SubprocessSandbox()
    if mode == "container":
        if not which(runtime):
            raise SandboxError(f"{env_var}=container nhưng không tìm thấy `{runtime}` trên PATH; "
                               f"cài runtime hoặc đặt {env_var}=subprocess (không tự tụt hạng bảo vệ)")
        return ContainerSandbox(runtime, image)
    if mode != "auto":
        raise SandboxError(f"chế độ sandbox không hợp lệ: {mode!r} (auto | container | subprocess)")
    return ContainerSandbox(runtime, image) if which(runtime) else SubprocessSandbox()
