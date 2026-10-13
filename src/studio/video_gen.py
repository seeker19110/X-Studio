"""Generate individual film/animation shots from an operator-owned local workflow.

ComfyUI protocol: POST /prompt, GET /history/{prompt_id}, GET /view. Only
configured existing workflow inputs are changed. The model never supplies a
graph, executable, download URL, or credentials. MP4 is the v1 interchange format.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .media import ROOT, MediaConfig, MediaError, MediaResult
from .media_cost import without_prices
from .sandbox import RunSpec, Sandbox, SandboxError, clean_env, sandbox_from_config


class VideoGenerator(Protocol):
    def generate(self, prompt: str, *, negative_prompt: str, duration_s: float, fps: int,
                 resolution: str, seed: int, out: Path) -> MediaResult: ...


def make_video_generator(cfg: MediaConfig) -> VideoGenerator | None:
    provider = str(cfg.scene_video.get("provider") or "none")
    if provider == "none":
        return None
    providers: dict[str, Callable[[MediaConfig], VideoGenerator]] = {
        "fake": FakeSceneVideo, "comfyui": ComfyUIVideo, "command": CommandVideo}
    if provider not in providers:
        raise MediaError(f"scene_video: unknown provider `{provider}` (none, fake, comfyui, command)")
    return providers[provider](cfg)


def _number(value: Any, name: str, low: float, high: float, *, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
        raise MediaError(f"scene_video.{name}: finite number required")
    if not low <= value <= high or (integer and not isinstance(value, int)):
        raise MediaError(f"scene_video.{name}: expected {'integer ' if integer else ''}{low:g}..{high:g}")
    return float(value)


def _inputs(prompt: str, negative_prompt: str, duration_s: float, fps: int, resolution: str,
            seed: int, section: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32_000:
        raise MediaError("scene_video.prompt: nonempty text up to 32000 characters required")
    if not isinstance(negative_prompt, str) or len(negative_prompt) > 32_000:
        raise MediaError("scene_video.negative_prompt: text up to 32000 characters required")
    _number(duration_s, "duration_s", 0.1, 60)
    _number(fps, "fps", 1, 120, integer=True)
    _number(seed, "seed", 0, 2**64 - 1, integer=True)
    match = re.fullmatch(r"(\d+)x(\d+)", str(resolution))
    if not match:
        raise MediaError("scene_video.resolution: expected WIDTHxHEIGHT")
    width, height = map(int, match.groups())
    _number(width, "width", 16, 8192, integer=True)
    _number(height, "height", 16, 8192, integer=True)
    frames = section.get("num_frames", max(1, round(duration_s * fps)))
    _number(frames, "num_frames", 1, 7200, integer=True)
    return {"prompt": prompt, "negative_prompt": negative_prompt, "duration_s": duration_s,
            "fps": fps, "resolution": resolution, "width": width, "height": height,
            "seed": seed, "frames": frames}


def _timeout(section: dict[str, Any], key: str, default: float) -> float:
    return _number(section.get(key, default), key, 0.01, 86_400)


# Renderer validates before synthesizing narration or submitting a GPU job.
validate_video_inputs = _inputs


def _probe(path: Path, sandbox: Sandbox, binary: str = "ffprobe", timeout: float = 30) -> float:
    if not path.is_file() or path.stat().st_size < 16:
        raise MediaError("scene_video: provider did not create a nonempty MP4")
    with path.open("rb") as stream:
        if stream.read(12)[4:8] != b"ftyp":
            raise MediaError("scene_video: output is not an MP4 container")
    try:
        result = sandbox.run(RunSpec(argv=[binary, "-v", "error", "-show_entries",
                            "stream=codec_type:format=duration", "-of", "json", str(path)],
                            cwd=Path.cwd(), env=clean_env(), timeout=timeout))
        if result.timed_out or result.exit_code != 0:
            raise MediaError("scene_video: ffprobe could not validate generated MP4")
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
            raise MediaError("scene_video: MP4 has no video stream")
        if not math.isfinite(duration) or duration <= 0:
            raise MediaError("scene_video: MP4 has no finite positive duration")
        return duration
    except (OSError, subprocess.SubprocessError, SandboxError, ValueError, TypeError, AttributeError) as exc:
        raise MediaError("scene_video: ffprobe required to validate generated MP4") from exc


class FakeSceneVideo:
    def __init__(self, cfg: MediaConfig):
        self.cfg = cfg

    def generate(self, prompt: str, *, negative_prompt: str, duration_s: float, fps: int,
                 resolution: str, seed: int, out: Path) -> MediaResult:
        values = _inputs(prompt, negative_prompt, duration_s, fps, resolution, seed, self.cfg.scene_video)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fake_scene_video": True, **values}, ensure_ascii=False), encoding="utf-8")
        return MediaResult(out, "fake", "fake-scene-video", duration_s,
                           notes="fake: JSON placeholder; no playable film or animation generated")


class CommandVideo:
    """Configured argv, UTF-8 JSON on stdin, never a shell or model-owned command."""
    def __init__(self, cfg: MediaConfig, sandbox: Sandbox | None = None):
        section = cfg.scene_video
        command = section.get("command")
        if isinstance(command, str):
            try:
                command = shlex.split(command)
            except ValueError as exc:
                raise MediaError("scene_video.command: invalid quoted argv") from exc
        if not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command):
            raise MediaError("scene_video.command: nonempty argv list or command string required")
        if section.get("output_format", "mp4") != "mp4":
            raise MediaError("scene_video.output_format: only mp4 supported")
        self.cfg, self.argv = cfg, command
        self.model = str(section.get("model") or Path(command[0]).name)
        self.timeout = _timeout(section, "timeout_s", 900)
        self.sandbox = sandbox if sandbox is not None else sandbox_from_config(cfg)

    def generate(self, prompt: str, *, negative_prompt: str, duration_s: float, fps: int,
                 resolution: str, seed: int, out: Path) -> MediaResult:
        values = _inputs(prompt, negative_prompt, duration_s, fps, resolution, seed, self.cfg.scene_video)
        target = out.with_suffix(".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        fields = {k: str(v) for k, v in {**values, "output": target, "out": target}.items()}
        try:
            argv = [arg.format_map(fields) for arg in self.argv]
        except (KeyError, ValueError) as exc:
            raise MediaError("scene_video.command: unknown or invalid argv placeholder") from exc
        try:
            result = self.sandbox.run(RunSpec(argv=argv, cwd=Path.cwd(),
                        env={**clean_env(), "PYTHONIOENCODING": "utf-8"}, timeout=self.timeout,
                        stdin=json.dumps(values, ensure_ascii=False)))
        except (OSError, subprocess.SubprocessError, SandboxError) as exc:
            raise MediaError("scene_video: command could not run") from exc
        if result.timed_out:
            raise MediaError(f"scene_video: command timed out after {self.timeout:g}s")
        if result.exit_code != 0:
            raise MediaError(f"scene_video: command failed ({result.exit_code}): {result.stderr[-400:]}")
        duration = _probe(target, self.sandbox, str(self.cfg.scene_video.get("probe_binary", "ffprobe")))
        return MediaResult(target, "command", self.model, duration)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MediaError("scene_video: HTTP redirects are disabled")


class ComfyUIVideo:
    """An API-format graph exported by the operator; persist accepted GPU jobs."""
    def __init__(self, cfg: MediaConfig):
        self.cfg, section = cfg, cfg.scene_video
        self.base_url = str(section.get("base_url") or "http://127.0.0.1:8188").rstrip("/")
        try:
            parsed = urllib.parse.urlsplit(self.base_url)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
                     and not (parsed.username or parsed.password or parsed.query or parsed.fragment))
        except ValueError:
            valid = False
        if not valid:
            raise MediaError("scene_video.base_url: HTTP(S) URL without credentials, query, or fragment required")
        if section.get("output_format", "mp4") != "mp4":
            raise MediaError("scene_video.output_format: only mp4 supported")
        self.timeout = _timeout(section, "timeout_s", 900)
        self.request_timeout = _timeout(section, "request_timeout_s", 30)
        self.poll_interval = _timeout(section, "poll_interval_s", 1)
        self.max_bytes = int(_number(section.get("max_download_bytes", 256 * 1024 * 1024),
                                     "max_download_bytes", 16, 1024**3, integer=True))
        self.model = str(section.get("model") or "operator-workflow")
        self.output_node = str(section.get("output_node") or "")
        path = Path(str(section.get("workflow_path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        try:
            self.workflow = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MediaError("scene_video.workflow_path: readable API-format JSON required") from exc
        if (not isinstance(self.workflow, dict) or not self.workflow
                or any(not isinstance(n, dict) or not isinstance(n.get("inputs"), dict)
                       or not isinstance(n.get("class_type"), str) for n in self.workflow.values())):
            raise MediaError("scene_video.workflow_path: export ComfyUI API format (not editor graph)")
        if self.output_node not in self.workflow:
            raise MediaError("scene_video.output_node: select an existing video output node")
        self.bindings: dict[str, Any] = section.get("bindings") or {}
        allowed = {"prompt", "negative_prompt", "seed", "width", "height", "fps", "duration_s", "frames"}
        if not isinstance(self.bindings, dict) or "prompt" not in self.bindings:
            raise MediaError("scene_video.bindings: explicit prompt binding required")
        occupied: set[tuple[str, str]] = set()
        for semantic, binding in self.bindings.items():
            if semantic not in allowed or not isinstance(binding, dict):
                raise MediaError("scene_video.bindings: unknown semantic or malformed binding")
            node, field = str(binding.get("node_id", "")), str(binding.get("input", ""))
            if node not in self.workflow or field not in self.workflow[node]["inputs"]:
                raise MediaError(f"scene_video.bindings.{semantic}: node input does not exist")
            if isinstance(self.workflow[node]["inputs"][field], (list, dict)):
                raise MediaError(f"scene_video.bindings.{semantic}: cannot replace a linked input")
            if (node, field) in occupied:
                raise MediaError("scene_video.bindings: duplicate target input")
            occupied.add((node, field))
        if "seed" not in self.bindings:
            raise MediaError("scene_video.bindings: explicit seed binding required for deterministic revisions")
        env_name = section.get("api_key_env")
        self.api_key = os.environ.get(str(env_name)) if env_name else None
        if env_name and not self.api_key:
            raise MediaError("scene_video.api_key_env: configured credential missing")
        self.opener = urllib.request.build_opener(_NoRedirect())
        self.sandbox = sandbox_from_config(cfg)
        self.jobs_dir = cfg.output_dir / ".cache" / "video-jobs"
        self.cache_identity = hashlib.sha256(json.dumps({"workflow": self.workflow,
            "settings": {k: v for k, v in without_prices(section).items() if k != "api_key"}},
            sort_keys=True).encode()).hexdigest()

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MediaError("scene_video: ComfyUI timeout; accepted job retained for retry")
        return min(self.request_timeout, remaining)

    def _request(self, path: str, deadline: float, body: dict[str, Any] | None = None,
                 limit: int = 4 * 1024 * 1024) -> bytes:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + path, headers=headers,
                    data=json.dumps(body).encode("utf-8") if body is not None else None,
                    method="POST" if body is not None else "GET")
        try:
            with self.opener.open(req, timeout=self._remaining(deadline)) as response:
                content = response.read(limit + 1)
                self._remaining(deadline)
                if len(content) > limit:
                    raise MediaError("scene_video: HTTP response exceeds configured byte limit")
                return content
        except urllib.error.HTTPError as exc:
            raise MediaError(f"scene_video: ComfyUI HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise MediaError("scene_video: ComfyUI request failed; accepted job retained for retry") from exc

    def _json(self, path: str, deadline: float, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            data = json.loads(self._request(path, deadline, body))
        except ValueError as exc:
            raise MediaError("scene_video: ComfyUI returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise MediaError("scene_video: ComfyUI JSON object required")
        return data

    @staticmethod
    def _save(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def _select_output(self, record: dict[str, Any]) -> dict[str, str]:
        outputs = record.get("outputs") or {}
        if not isinstance(outputs, dict):
            raise MediaError("scene_video: malformed ComfyUI outputs")
        node = outputs.get(self.output_node) or {}
        if not isinstance(node, dict):
            raise MediaError("scene_video: malformed ComfyUI output node")
        groups = [node.get(name) or [] for name in ("videos", "gifs", "images")]
        if any(not isinstance(group, list) for group in groups):
            raise MediaError("scene_video: malformed ComfyUI video list")
        for item in [item for group in groups for item in group]:
            if not isinstance(item, dict):
                continue
            filename, subfolder = str(item.get("filename", "")), str(item.get("subfolder", ""))
            if not filename.lower().endswith(".mp4"):
                continue
            if ("/" in filename or "\\" in filename or ".." in filename or ":" in filename
                    or subfolder.startswith(("/", "\\")) or ".." in subfolder or "\\" in subfolder or ":" in subfolder):
                raise MediaError("scene_video: unsafe ComfyUI output filename/subfolder")
            kind = str(item.get("type", "output"))
            if kind not in {"output", "temp"}:
                raise MediaError("scene_video: output type must be output or temp")
            return {"filename": filename, "subfolder": subfolder, "type": kind}
        raise MediaError("scene_video: selected output node did not return an MP4 video")

    def generate(self, prompt: str, *, negative_prompt: str, duration_s: float, fps: int,
                 resolution: str, seed: int, out: Path) -> MediaResult:
        values = _inputs(prompt, negative_prompt, duration_s, fps, resolution, seed, self.cfg.scene_video)
        graph = copy.deepcopy(self.workflow)
        for semantic, binding in self.bindings.items():
            graph[str(binding["node_id"])]["inputs"][str(binding["input"])] = values[semantic]
        identity = {"base_url": self.base_url, "workflow": graph, "bindings": self.bindings,
                    "inputs": values, "output_node": self.output_node, "model": self.model}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        checkpoint, cached = self.jobs_dir / f"{key}.json", self.jobs_dir / f"{key}.mp4"
        deadline = time.monotonic() + self.timeout
        record: dict[str, Any] = {}
        if checkpoint.exists():
            try:
                record = json.loads(checkpoint.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise MediaError("scene_video: invalid job checkpoint; refusing duplicate submission") from exc
            if not isinstance(record, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", str(record.get("prompt_id", ""))):
                raise MediaError("scene_video: invalid checkpoint prompt_id")
        if not record:
            accepted = self._json("/prompt", deadline, {"prompt": graph})
            prompt_id = str(accepted.get("prompt_id") or "")
            if accepted.get("error") or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", prompt_id):
                raise MediaError("scene_video: ComfyUI rejected graph or returned no prompt_id")
            record = {"prompt_id": prompt_id}
            self._save(checkpoint, record)
        prompt_id = record["prompt_id"]
        if record.get("error"):
            raise MediaError(f"scene_video: ComfyUI job {prompt_id} failed (checkpoint retained)")
        while not record.get("output"):
            item = self._json("/history/" + urllib.parse.quote(prompt_id, safe=""), deadline).get(prompt_id) or {}
            if not isinstance(item, dict):
                raise MediaError("scene_video: malformed ComfyUI history record")
            status = item.get("status") or {}
            if not isinstance(status, dict):
                raise MediaError("scene_video: malformed ComfyUI job status")
            messages = status.get("messages") or []
            if not isinstance(messages, list):
                raise MediaError("scene_video: malformed ComfyUI job messages")
            if status.get("status_str") == "error" or any(
                    isinstance(m, list) and m and m[0] in {"execution_error", "execution_interrupted"} for m in messages):
                record["error"] = True
                self._save(checkpoint, record)
                raise MediaError(f"scene_video: ComfyUI job {prompt_id} failed")
            if status.get("completed"):
                record["output"] = self._select_output(item)
                self._save(checkpoint, record)
                break
            time.sleep(min(self.poll_interval, self._remaining(deadline)))
        if not cached.exists():
            # Do not use server-supplied fullpath/URL. Revalidate checkpoint data too.
            output = self._select_output({"outputs": {self.output_node: {"videos": [record["output"]]}}})
            raw = self._request("/view?" + urllib.parse.urlencode(output), deadline, limit=self.max_bytes)
            temporary = cached.with_suffix(f".{uuid4().hex}.mp4")
            try:
                temporary.write_bytes(raw)
                _probe(temporary, self.sandbox, str(self.cfg.scene_video.get("probe_binary", "ffprobe")),
                       timeout=self._remaining(deadline))
                temporary.replace(cached)
            finally:
                temporary.unlink(missing_ok=True)
        duration = _probe(cached, self.sandbox, str(self.cfg.scene_video.get("probe_binary", "ffprobe")))
        target = out.with_suffix(".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, target)
        return MediaResult(target, "comfyui", self.model, duration, notes=f"ComfyUI prompt_id={prompt_id}")
