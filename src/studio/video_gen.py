"""Generate individual film/animation shots from an operator-owned workflow or a hosted model.

ComfyUI protocol: POST /prompt, GET /history/{prompt_id}, GET /view. Only
configured existing workflow inputs are changed. Veo (ADR-0018): POST
models/{model}:predictLongRunning, GET {operation}, download the sample URI
(googleapis host only). Omni (ADR-0018): POST interactions (delivery uri),
GET files/{id} until ACTIVE, download. The model never supplies a graph, executable, download
URL, or credentials. MP4 is the v1 interchange format.
"""
from __future__ import annotations

import base64
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

from .media import ROOT, MediaConfig, MediaError, MediaResult, require_key, section_api_key
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
        "fake": FakeSceneVideo, "comfyui": ComfyUIVideo, "command": CommandVideo, "veo": VeoVideo,
        "omni": OmniVideo}
    if provider not in providers:
        raise MediaError(f"scene_video: unknown provider `{provider}` (none, fake, comfyui, command, veo, omni)")
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


class _HttpJob:
    """Submit → checkpoint → poll → download, shared by hosted/remote providers. Messages name `label`."""
    label = "service"
    cfg: MediaConfig
    base_url: str
    model: str
    poll_interval: float
    max_bytes: int
    sandbox: Sandbox
    jobs_dir: Path
    timeout: float
    request_timeout: float
    auth: dict[str, str]
    opener: Any

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MediaError(f"scene_video: {self.label} timeout; accepted job retained for retry")
        return min(self.request_timeout, remaining)

    def _request(self, path: str, deadline: float, body: dict[str, Any] | None = None,
                 limit: int = 4 * 1024 * 1024) -> bytes:
        url = path if path.startswith("https://") else self.base_url + path
        req = urllib.request.Request(url, headers={"Content-Type": "application/json", **self.auth},
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
            raise MediaError(f"scene_video: {self.label} HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise MediaError(f"scene_video: {self.label} request failed; accepted job retained for retry") from exc

    def _json(self, path: str, deadline: float, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            data = json.loads(self._request(path, deadline, body))
        except ValueError as exc:
            raise MediaError(f"scene_video: {self.label} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise MediaError(f"scene_video: {self.label} JSON object required")
        return data

    @staticmethod
    def _save(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def _load(self, checkpoint: Path, field: str, pattern: str) -> dict[str, Any]:
        if not checkpoint.exists():
            return {}
        try:
            record = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MediaError("scene_video: invalid job checkpoint; refusing duplicate submission") from exc
        if not isinstance(record, dict) or not re.fullmatch(pattern, str(record.get(field, ""))):
            raise MediaError(f"scene_video: invalid checkpoint {field}")
        return record

    def _download(self, url: str, cached: Path, deadline: float, probe_binary: str, sandbox: Sandbox,
                  limit: int) -> None:
        raw = self._request(url, deadline, limit=limit)
        temporary = cached.with_suffix(f".{uuid4().hex}.mp4")
        try:
            temporary.write_bytes(raw)
            _probe(temporary, sandbox, probe_binary, timeout=self._remaining(deadline))
            temporary.replace(cached)
        finally:
            temporary.unlink(missing_ok=True)


class ComfyUIVideo(_HttpJob):
    """An API-format graph exported by the operator; persist accepted GPU jobs."""
    label = "ComfyUI"
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
        self.auth = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self.opener = urllib.request.build_opener(_NoRedirect())
        self.sandbox = sandbox_from_config(cfg)
        self.jobs_dir = cfg.output_dir / ".cache" / "video-jobs"
        self.cache_identity = hashlib.sha256(json.dumps({"workflow": self.workflow,
            "settings": {k: v for k, v in without_prices(section).items() if k != "api_key"}},
            sort_keys=True).encode()).hexdigest()

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
        record = self._load(checkpoint, "prompt_id", r"[a-zA-Z0-9_-]{1,128}")
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
            self._download("/view?" + urllib.parse.urlencode(output), cached, deadline,
                           str(self.cfg.scene_video.get("probe_binary", "ffprobe")), self.sandbox, self.max_bytes)
        duration = _probe(cached, self.sandbox, str(self.cfg.scene_video.get("probe_binary", "ffprobe")))
        target = out.with_suffix(".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, target)
        return MediaResult(target, "comfyui", self.model, duration, notes=f"ComfyUI prompt_id={prompt_id}")


VEO_DEFAULT_MODEL = "veo-3.1-fast-generate-001"
VEO_DURATIONS = (4, 6, 8)
VEO_OPERATION = r"models/[A-Za-z0-9._-]+/operations/[A-Za-z0-9_-]{1,128}"
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _references(section: dict[str, Any]) -> list[tuple[str, str]]:
    """`scene_video.reference_images` → [(base64, mime)]: ≤ 3 ảnh nhân vật/sản phẩm do người vận hành chọn."""
    refs = section.get("reference_images") or []
    if not isinstance(refs, list) or len(refs) > 3 or not all(isinstance(r, str) and r for r in refs):
        raise MediaError("scene_video.reference_images: list of up to 3 PNG/JPEG/WebP paths")
    out = []
    for ref in refs:
        path = Path(ref) if Path(ref).is_absolute() else ROOT / ref
        mime = _IMAGE_MIME.get(path.suffix.lower())
        if not mime or not path.is_file():
            raise MediaError(f"scene_video.reference_images: readable PNG/JPEG/WebP required ({path.name})")
        out.append((base64.b64encode(path.read_bytes()).decode("ascii"), mime))
    return out


def _tier(width: int, height: int, tiers: tuple[str, ...]) -> str:
    """Bậc độ phân giải Gemini theo cạnh ngắn: làm tròn LÊN bậc có trong `tiers` (dựng to rồi thu nhỏ, không phóng
    clip nhỏ); 4k chỉ khi khung thật sự ≥ 2160."""
    short = min(width, height)
    if "360p" in tiers and short <= 360:
        return "360p"
    return "720p" if short <= 720 else "1080p" if short < 2160 else "4k"


class _GeminiJob(_HttpJob):
    """Provider sinh clip qua Gemini API: HTTPS, khoá `x-goog-api-key`, tải chỉ từ *.googleapis.com, SynthID."""
    provider = "gemini"
    default_model = ""
    model_pattern = ""

    def __init__(self, cfg: MediaConfig, *, request_timeout: float, poll_interval: float):
        self.cfg, section = cfg, cfg.scene_video
        if section.get("output_format", "mp4") != "mp4":
            raise MediaError("scene_video.output_format: only mp4 supported")
        self.base_url = str(section.get("base_url") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        if not self.base_url.startswith("https://"):
            raise MediaError(f"scene_video.base_url: {self.label} requires an HTTPS Gemini API endpoint")
        self.model = str(section.get("model") or self.default_model)
        if not re.fullmatch(self.model_pattern, self.model):
            raise MediaError(f"scene_video.model: {self.label} model id expected (default {self.default_model})")
        self.timeout = _timeout(section, "timeout_s", 900)
        self.request_timeout = _timeout(section, "request_timeout_s", request_timeout)
        self.poll_interval = _timeout(section, "poll_interval_s", poll_interval)
        self.max_bytes = int(_number(section.get("max_download_bytes", 256 * 1024 * 1024),
                                     "max_download_bytes", 16, 1024**3, integer=True))
        key = require_key("scene_video", self.provider,
                          section_api_key(section, cfg, "GEMINI_API_KEY", "GOOGLE_API_KEY"), "GEMINI_API_KEY")
        self.auth = {"x-goog-api-key": key}
        self.references = _references(section)
        self.opener = urllib.request.build_opener(_NoRedirect())
        self.sandbox = sandbox_from_config(cfg)
        self.jobs_dir = cfg.output_dir / ".cache" / "video-jobs"
        settings = {k: v for k, v in without_prices(section).items() if k not in {"api_key", "api_key_env"}}
        self.cache_identity = hashlib.sha256(json.dumps(
            {"provider": self.provider, "model": self.model, "settings": settings,
             "references": [data for data, _ in self.references]}, sort_keys=True).encode()).hexdigest()

    def _checked_uri(self, uri: str) -> str:
        parsed = urllib.parse.urlsplit(uri)
        if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".googleapis.com")
                or parsed.username or parsed.password):
            raise MediaError(f"scene_video: {self.label} video URI must be an HTTPS googleapis.com URL")
        return uri

    def _job_paths(self, body: dict[str, Any]) -> tuple[Path, Path]:
        key = hashlib.sha256(json.dumps({"base_url": self.base_url, "model": self.model, "body": body},
                                        sort_keys=True).encode()).hexdigest()
        return self.jobs_dir / f"{key}.json", self.jobs_dir / f"{key}.mp4"

    def _finish(self, cached: Path, out: Path, notes: str) -> MediaResult:
        duration = _probe(cached, self.sandbox, str(self.cfg.scene_video.get("probe_binary", "ffprobe")))
        target = out.with_suffix(".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, target)
        return MediaResult(target, self.provider, self.model, duration, notes=notes + "; SynthID watermark")


class VeoVideo(_GeminiJob):
    """Google Veo 3.1 through the Gemini API (ADR-0018): predictLongRunning → operation → download. The operator
    keeps `license: generated` and the publish gate sees `synthetic_media`."""
    label, provider = "Veo", "veo"
    default_model, model_pattern = VEO_DEFAULT_MODEL, r"veo-[a-z0-9.-]+"

    def __init__(self, cfg: MediaConfig):
        super().__init__(cfg, request_timeout=60, poll_interval=5)
        self.reference_images = [{"image": {"bytesBase64Encoded": data, "mimeType": mime}, "referenceType": "asset"}
                                 for data, mime in self.references]

    def _select_uri(self, op: dict[str, Any]) -> str:
        response = op.get("response") or {}
        video = response.get("generateVideoResponse") if isinstance(response, dict) else None
        samples = video.get("generatedSamples") if isinstance(video, dict) else None
        if not isinstance(samples, list):
            raise MediaError("scene_video: malformed Veo operation response")
        for sample in samples:
            uri = (sample.get("video") or {}).get("uri") if isinstance(sample, dict) else None
            if isinstance(uri, str):
                return self._checked_uri(uri)
        if isinstance(video, dict) and video.get("raiMediaFilteredCount"):
            raise MediaError(f"scene_video: Veo filtered the request: {video.get('raiMediaFilteredReasons')}")
        raise MediaError("scene_video: Veo operation returned no video")

    def generate(self, prompt: str, *, negative_prompt: str, duration_s: float, fps: int,
                 resolution: str, seed: int, out: Path) -> MediaResult:
        values = _inputs(prompt, negative_prompt, duration_s, fps, resolution, seed, self.cfg.scene_video)
        tier = _tier(values["width"], values["height"], ("720p", "1080p", "4k"))
        seconds = next((d for d in VEO_DURATIONS if d >= duration_s), VEO_DURATIONS[-1])
        if tier != "720p" or self.reference_images:
            seconds = VEO_DURATIONS[-1]
        instance: dict[str, Any] = {"prompt": prompt}
        if self.reference_images:
            instance["referenceImages"] = self.reference_images
        parameters: dict[str, Any] = {"aspectRatio": "16:9" if values["width"] >= values["height"] else "9:16",
                                      "resolution": tier, "durationSeconds": seconds, "seed": seed % 2**32}
        if negative_prompt.strip():
            parameters["negativePrompt"] = negative_prompt
        body = {"instances": [instance], "parameters": parameters}
        checkpoint, cached = self._job_paths(body)
        deadline = time.monotonic() + self.timeout
        record = self._load(checkpoint, "operation", VEO_OPERATION)
        if not record:
            accepted = self._json(f"/models/{self.model}:predictLongRunning", deadline, body)
            name = str(accepted.get("name") or "")
            if not re.fullmatch(VEO_OPERATION, name):
                raise MediaError("scene_video: Veo returned no operation name")
            record = {"operation": name}
            self._save(checkpoint, record)
        name = record["operation"]
        if record.get("error"):
            raise MediaError(f"scene_video: Veo operation {name} failed (checkpoint retained)")
        while not record.get("uri"):
            op = self._json("/" + name, deadline)
            if op.get("error"):
                record["error"] = True
                self._save(checkpoint, record)
                message = op["error"].get("message") if isinstance(op["error"], dict) else op["error"]
                raise MediaError(f"scene_video: Veo operation {name} failed: {str(message)[:200]}")
            if op.get("done"):
                record["uri"] = self._select_uri(op)
                self._save(checkpoint, record)
                break
            time.sleep(min(self.poll_interval, self._remaining(deadline)))
        if not cached.exists():
            self._download(self._checked_uri(record["uri"]), cached, deadline,
                           str(self.cfg.scene_video.get("probe_binary", "ffprobe")), self.sandbox, self.max_bytes)
        return self._finish(cached, out, f"Veo operation={name}; durationSeconds={seconds}")


OMNI_DEFAULT_MODEL = "gemini-omni-1.1-flash"
OMNI_FILE = re.compile(r"/v1beta/(files/[A-Za-z0-9_-]{1,128}):download$")


class OmniVideo(_GeminiJob):
    """Gemini Omni through the Interactions API (ADR-0018). Unary call with `delivery: uri`; the interaction id and
    file URI are checkpointed before the file is polled to ACTIVE and downloaded, so a retry never pays twice for a
    clip already generated. Omni takes no duration, seed or negative prompt: clip length is probed, seed only keys
    the cache."""
    label, provider = "Omni", "omni"
    default_model, model_pattern = OMNI_DEFAULT_MODEL, r"gemini-omni-[a-z0-9.-]+"

    def __init__(self, cfg: MediaConfig):
        super().__init__(cfg, request_timeout=600, poll_interval=5)

    def _file_name(self, uri: str) -> str:
        """`files/{id}` của một URI tải Omni; URI ngoài googleapis hoặc không phải file download thì từ chối."""
        match = OMNI_FILE.search(urllib.parse.urlsplit(self._checked_uri(uri)).path)
        if not match:
            raise MediaError("scene_video: Omni video URI must be a googleapis files download URL")
        return match.group(1)

    def _select_video(self, interaction: dict[str, Any]) -> dict[str, str]:
        steps = interaction.get("steps") or []
        if not isinstance(steps, list):
            raise MediaError("scene_video: malformed Omni interaction")
        for step in steps:
            for block in (step.get("content") or [] if isinstance(step, dict) else []):
                if not isinstance(block, dict) or block.get("type") != "video":
                    continue
                if isinstance(block.get("uri"), str):
                    self._file_name(block["uri"])
                    return {"uri": block["uri"]}
                if isinstance(block.get("data"), str):
                    return {"data": block["data"]}
        raise MediaError("scene_video: Omni interaction returned no video")

    def generate(self, prompt: str, *, negative_prompt: str, duration_s: float, fps: int,
                 resolution: str, seed: int, out: Path) -> MediaResult:
        values = _inputs(prompt, negative_prompt, duration_s, fps, resolution, seed, self.cfg.scene_video)
        body: dict[str, Any] = {"model": self.model, "store": False, "response_format": {
            "type": "video", "aspect_ratio": "16:9" if values["width"] >= values["height"] else "9:16",
            "resolution": _tier(values["width"], values["height"], ("360p", "720p", "1080p", "4k")), "delivery": "uri"}}
        if self.references:
            body["input"] = [*({"type": "image", "data": data, "mime_type": mime} for data, mime in self.references),
                             {"type": "text", "text": prompt}]
            body["generation_config"] = {"video_config": {"task": "reference_to_video"}}
        else:
            body["input"] = prompt
        checkpoint, cached = self._job_paths({**body, "seed": seed})
        deadline = time.monotonic() + self.timeout
        record = self._load(checkpoint, "interaction", r"[A-Za-z0-9_.-]{1,256}")
        if not record and not cached.exists():
            result = self._json("/interactions", deadline, body)
            interaction_id = str(result.get("id") or "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", interaction_id):
                raise MediaError("scene_video: Omni returned no interaction id")
            if result.get("status") in {"failed", "cancelled"}:
                error = result.get("error")
                message = error.get("message") if isinstance(error, dict) else error
                raise MediaError(f"scene_video: Omni interaction {interaction_id} failed: {str(message)[:200]}")
            video = self._select_video(result)
            if "data" in video:
                self._store_inline(video["data"], cached, deadline)
                return self._finish(cached, out, f"Omni interaction={interaction_id}")
            record = {"interaction": interaction_id, "uri": video["uri"]}
            self._save(checkpoint, record)
        if not cached.exists():
            uri, name = record["uri"], self._file_name(str(record["uri"]))
            while True:
                state = str(self._json(f"/{name}", deadline).get("state") or "")
                if state == "ACTIVE":
                    break
                if state == "FAILED":
                    raise MediaError(f"scene_video: Omni file {name} failed (checkpoint retained)")
                time.sleep(min(self.poll_interval, self._remaining(deadline)))
            self._download(uri, cached, deadline, str(self.cfg.scene_video.get("probe_binary", "ffprobe")),
                           self.sandbox, self.max_bytes)
        return self._finish(cached, out, f"Omni interaction={record.get('interaction', '?')}")

    def _store_inline(self, data: str, cached: Path, deadline: float) -> None:
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError as exc:
            raise MediaError("scene_video: Omni returned invalid base64 video") from exc
        if len(raw) > self.max_bytes:
            raise MediaError("scene_video: HTTP response exceeds configured byte limit")
        cached.parent.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_suffix(f".{uuid4().hex}.mp4")
        try:
            temporary.write_bytes(raw)
            _probe(temporary, self.sandbox, str(self.cfg.scene_video.get("probe_binary", "ffprobe")),
                   timeout=self._remaining(deadline))
            temporary.replace(cached)
        finally:
            temporary.unlink(missing_ok=True)
