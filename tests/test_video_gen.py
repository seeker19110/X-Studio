"""Scene generation is local code; tests never submit a live GPU/API job."""
from __future__ import annotations

import base64
import io
import json
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from studio.media import MediaConfig, MediaError
from studio.sandbox import Result, SandboxError
from studio.video_gen import (
    ComfyUIVideo,
    CommandVideo,
    FakeSceneVideo,
    VeoVideo,
    _NoRedirect,
    _probe,
    make_video_generator,
)

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 40
PROBE = json.dumps({"streams": [{"codec_type": "video"}], "format": {"duration": "2.5"}})


def config(tmp_path: Path, **options: Any) -> MediaConfig:
    cfg = MediaConfig(output_dir=tmp_path / "output")
    cfg.scene_video = {"provider": "comfyui", **options}
    return cfg


def workflow(tmp_path: Path, **options: Any) -> MediaConfig:
    data = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
            "2": {"class_type": "LocalVideo", "inputs": {"seed": 0, "width": 64, "height": 64,
                    "fps": 8, "seconds": 1, "negative": ""}},
            "3": {"class_type": "VHS_VideoCombine", "inputs": {"format": "video/h264-mp4"}}}
    p = tmp_path / "workflow.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    bindings = {"prompt": {"node_id": "1", "input": "text"},
                **{k: {"node_id": "2", "input": v} for k, v in
                   {"seed": "seed", "width": "width", "height": "height", "fps": "fps",
                    "duration_s": "seconds", "negative_prompt": "negative"}.items()}}
    return config(tmp_path, workflow_path=str(p), bindings=bindings, output_node="3", **options)


def generate(provider: Any, tmp_path: Path, **overrides: Any):
    values = {"prompt": "Mèo đi dưới mưa", "negative_prompt": "blur", "duration_s": 2.5,
              "fps": 24, "resolution": "640x360", "seed": 17, "out": tmp_path / "scene.mp4"}
    values.update(overrides)
    return provider.generate(**values)


class SandboxStub:
    name = "test"

    def __init__(self, *, payload: bytes = MP4, result: Result | None = None):
        self.specs: list[Any] = []
        self.payload, self.result = payload, result

    def run(self, spec):
        self.specs.append(spec)
        if spec.argv[0] == "ffprobe":
            return Result(0, PROBE, "", False, self.name)
        if self.result:
            return self.result
        Path(spec.argv[-1]).write_bytes(self.payload)
        return Result(0, "", "", False, self.name)


class Response(io.BytesIO):
    def __init__(self, data: Any):
        super().__init__(data if isinstance(data, bytes) else json.dumps(data).encode())


def mock_http(monkeypatch, items: list[Any]):
    requests = []

    def open_request(req, timeout):
        requests.append((req, timeout))
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return Response(item)

    monkeypatch.setattr("studio.video_gen.urllib.request.build_opener", lambda *_: SimpleNamespace(open=open_request))
    monkeypatch.setattr("studio.video_gen.sandbox_from_config", lambda _: SandboxStub())
    return requests


def history(**overrides):
    record = {"outputs": {"3": {"gifs": [{"filename": "film.mp4", "subfolder": "movies", "type": "output"}]}},
              "status": {"status_str": "success", "completed": True, "messages": []}}
    record.update(overrides)
    return {"job-1": record}


def test_factory_and_fake(tmp_path):
    cfg = config(tmp_path, provider="none")
    assert make_video_generator(cfg) is None
    cfg.scene_video["provider"] = "fake"
    fake = make_video_generator(cfg)
    assert isinstance(fake, FakeSceneVideo)
    result = generate(fake, tmp_path)
    assert result.provider == "fake" and result.duration_s == 2.5
    assert json.loads(result.path.read_text())["fake_scene_video"] is True
    assert "placeholder" in result.notes
    cfg.scene_video["provider"] = "unknown"
    with pytest.raises(MediaError, match="provider"):
        make_video_generator(cfg)


def test_comfyui_binding_output_and_replay(tmp_path, monkeypatch):
    cfg = workflow(tmp_path)
    calls = mock_http(monkeypatch, [{"prompt_id": "job-1"}, history(), MP4])
    provider = make_video_generator(cfg)
    assert isinstance(provider, ComfyUIVideo)
    result = generate(provider, tmp_path)
    assert result.path.read_bytes() == MP4 and result.duration_s == 2.5
    posted = json.loads(calls[0][0].data)["prompt"]
    assert posted["1"]["inputs"]["text"] == "Mèo đi dưới mưa"
    assert posted["2"]["inputs"] == {"seed": 17, "width": 640, "height": 360, "fps": 24,
                                           "seconds": 2.5, "negative": "blur"}
    assert "filename=film.mp4" in calls[2][0].full_url
    assert "subfolder=movies" in calls[2][0].full_url
    cached = generate(ComfyUIVideo(cfg), tmp_path, out=tmp_path / "again.mp4")
    assert cached.path.read_bytes() == MP4 and len(calls) == 3


def test_command_utf8_json_no_shell_and_probe(tmp_path):
    cfg = config(tmp_path, provider="command", command=["animate", "--output", "{output}"])
    sb = SandboxStub()
    result = generate(CommandVideo(cfg, sandbox=sb), tmp_path)
    assert result.provider == "command" and result.duration_s == 2.5
    spec = sb.specs[0]
    assert spec.env["PYTHONIOENCODING"] == "utf-8"
    assert json.loads(spec.stdin)["prompt"] == "Mèo đi dưới mưa"
    assert spec.argv == ["animate", "--output", str(result.path)]


@pytest.mark.parametrize("overrides,match", [
    ({"prompt": " "}, "prompt"), ({"prompt": 1}, "prompt"), ({"prompt": "x" * 32_001}, "prompt"),
    ({"negative_prompt": 1}, "negative_prompt"), ({"negative_prompt": "x" * 32_001}, "negative_prompt"),
    ({"duration_s": float("nan")}, "finite"), ({"duration_s": float("inf")}, "finite"),
    ({"duration_s": True}, "finite"), ({"duration_s": "2"}, "finite"),
    ({"duration_s": 61}, "duration_s"), ({"duration_s": 0}, "duration_s"),
    ({"fps": 24.0}, "integer"), ({"fps": 0}, "fps"), ({"fps": 121}, "fps"),
    ({"seed": -1}, "seed"), ({"seed": 2**64}, "seed"),
    ({"resolution": "bad"}, "WIDTHxHEIGHT"), ({"resolution": "2x8193"}, "width"),
    ({"resolution": "64x8193"}, "height"),
])
def test_generation_input_bounds(tmp_path, overrides, match):
    with pytest.raises(MediaError, match=match):
        generate(FakeSceneVideo(config(tmp_path, provider="fake")), tmp_path, **overrides)


def test_num_frames_override_and_default_factory(tmp_path):
    assert make_video_generator(MediaConfig()) is None
    cfg = config(tmp_path, provider="fake", num_frames=81)
    assert json.loads(generate(FakeSceneVideo(cfg), tmp_path).path.read_text())["frames"] == 81
    cfg.scene_video["num_frames"] = 0
    with pytest.raises(MediaError, match="num_frames"):
        generate(FakeSceneVideo(cfg), tmp_path)


@pytest.mark.parametrize("command", [None, [], [1], [""], 42, "", 'animate "'])
def test_command_config_invalid(tmp_path, command):
    with pytest.raises(MediaError, match="command"):
        CommandVideo(config(tmp_path, provider="command", command=command))


def test_command_string_config_and_factory(tmp_path, monkeypatch):
    sb = SandboxStub()
    monkeypatch.setattr("studio.video_gen.sandbox_from_config", lambda _: sb)
    cfg = config(tmp_path, provider="command", command='animate --model "local model" {out}')
    assert isinstance(make_video_generator(cfg), CommandVideo)
    generate(CommandVideo(cfg), tmp_path)
    assert sb.specs[0].argv[2] == "local model"
    cfg.scene_video["output_format"] = "webm"
    with pytest.raises(MediaError, match="mp4"):
        CommandVideo(cfg)


@pytest.mark.parametrize("argv", [["animate", "{unknown}"], ["animate", "{out"]])
def test_command_unknown_placeholder(tmp_path, argv):
    with pytest.raises(MediaError, match="placeholder"):
        generate(CommandVideo(config(tmp_path, command=argv), sandbox=SandboxStub()), tmp_path)


@pytest.mark.parametrize("result,match", [
    (Result(None, "", "", True, "test"), "timed out"),
    (Result(3, "", "CUDA OOM", False, "test"), "failed.*CUDA OOM"),
])
def test_command_process_errors(tmp_path, result, match):
    provider = CommandVideo(config(tmp_path, command=["animate", "{out}"]), sandbox=SandboxStub(result=result))
    with pytest.raises(MediaError, match=match):
        generate(provider, tmp_path)


@pytest.mark.parametrize("exc", [OSError("missing executable"), subprocess.SubprocessError("err"), SandboxError("err")])
def test_command_start_failure(tmp_path, exc):
    def fail(_):
        raise exc
    provider = CommandVideo(config(tmp_path, command=["animate", "{out}"]), sandbox=SimpleNamespace(run=fail))
    with pytest.raises(MediaError, match="could not run"):
        generate(provider, tmp_path)


def test_command_rejects_stale_and_empty_output(tmp_path):
    out = tmp_path / "scene.mp4"
    out.write_bytes(MP4)
    sb = SandboxStub(result=Result(0, "", "", False, "test"))
    provider = CommandVideo(config(tmp_path, command=["animate", "{out}"]), sandbox=sb)
    with pytest.raises(MediaError, match="nonempty MP4"):
        generate(provider, tmp_path)
    assert not out.exists()


@pytest.mark.parametrize("result,match", [
    (Result(None, "", "", True, "test"), "could not validate"),
    (Result(1, "", "", False, "test"), "could not validate"),
    (Result(0, "not JSON", "", False, "test"), "ffprobe required"),
    (Result(0, json.dumps({"streams": []}), "", False, "test"), "no video stream"),
    (Result(0, json.dumps({"streams": [{"codec_type": "video"}], "format": {"duration": "nan"}}), "", False, "test"), "duration"),
    (Result(0, json.dumps({"streams": [{"codec_type": "video"}], "format": {"duration": 0}}), "", False, "test"), "duration"),
    (Result(0, "[]", "", False, "test"), "ffprobe required"),
])
def test_probe_validation(tmp_path, result, match):
    path = tmp_path / "probe.mp4"
    path.write_bytes(MP4)
    with pytest.raises(MediaError, match=match):
        _probe(path, SimpleNamespace(run=lambda _: result))


def test_probe_wrong_container_and_missing_binary(tmp_path):
    path = tmp_path / "probe.mp4"
    path.write_bytes(b"some entirely invalid content")
    with pytest.raises(MediaError, match="container"):
        _probe(path, SandboxStub())
    path.write_bytes(MP4)
    def fail(_):
        raise OSError("missing")
    with pytest.raises(MediaError, match="ffprobe required"):
        _probe(path, SimpleNamespace(run=fail))


@pytest.mark.parametrize("url", ["file:///tmp", "http://user:pw@localhost:8188", "http://localhost?q=1",
                                  "http://localhost/#fragment", "http://localhost:wrong", "http://[",
                                  "http://localhost:0", "https:///missing"])
def test_comfyui_url_invalid(tmp_path, url):
    with pytest.raises(MediaError, match="base_url"):
        ComfyUIVideo(workflow(tmp_path, base_url=url))


@pytest.mark.parametrize("key,value,match", [
    ("output_format", "webm", "mp4"), ("timeout_s", 0, "timeout_s"),
    ("poll_interval_s", float("inf"), "finite"), ("max_download_bytes", 1.5, "integer"),
    ("output_node", "missing", "output_node"), ("bindings", {}, "prompt binding"),
    ("bindings", {"prompt": "bad"}, "semantic"),
    ("bindings", {"prompt": {"node_id": "1", "input": "missing"}}, "does not exist"),
    ("bindings", {"prompt": {"node_id": "1", "input": "clip"}}, "linked"),
    ("bindings", {"prompt": {"node_id": "1", "input": "text"}}, "seed binding"),
    ("bindings", {"prompt": {"node_id": "1", "input": "text"}, "seed": {"node_id": "1", "input": "text"}}, "duplicate"),
    ("api_key_env", "STUDIO_TEST_NO_KEY", "credential missing"),
])
def test_comfyui_config_invalid(tmp_path, key, value, match, monkeypatch):
    monkeypatch.delenv("STUDIO_TEST_NO_KEY", raising=False)
    cfg = workflow(tmp_path)
    cfg.scene_video[key] = value
    with pytest.raises(MediaError, match=match):
        ComfyUIVideo(cfg)


@pytest.mark.parametrize("payload", ["not-json", "[]", "{}", '{"nodes": []}', '{"1": {"inputs": {}}}'])
def test_comfyui_workflow_format_invalid(tmp_path, payload):
    cfg = workflow(tmp_path)
    Path(cfg.scene_video["workflow_path"]).write_text(payload)
    with pytest.raises(MediaError, match=r"API-format|API format"):
        ComfyUIVideo(cfg)


def test_comfyui_missing_relative_workflow_and_auth(tmp_path, monkeypatch):
    cfg = workflow(tmp_path, api_key_env="STUDIO_TEST_COMFY_KEY", num_frames=81)
    monkeypatch.setenv("STUDIO_TEST_COMFY_KEY", "secret-test")
    monkeypatch.setattr("studio.video_gen.ROOT", tmp_path)
    cfg.scene_video["workflow_path"] = "workflow.json"
    cfg.scene_video["bindings"]["frames"] = {"node_id": "2", "input": "fps"}
    del cfg.scene_video["bindings"]["fps"]
    calls = mock_http(monkeypatch, [{"prompt_id": "job-1"}, history(), MP4])
    provider = ComfyUIVideo(cfg)
    generate(provider, tmp_path)
    assert calls[0][0].get_header("Authorization") == "Bearer secret-test"
    assert json.loads(calls[0][0].data)["prompt"]["2"]["inputs"]["fps"] == 81
    original_identity = provider.cache_identity
    data = json.loads((tmp_path / "workflow.json").read_text())
    data["3"]["inputs"]["format"] = "video/another-mp4"
    (tmp_path / "workflow.json").write_text(json.dumps(data))
    assert ComfyUIVideo(cfg).cache_identity != original_identity
    cfg.scene_video["workflow_path"] = "missing.json"
    with pytest.raises(MediaError, match="readable"):
        ComfyUIVideo(cfg)


@pytest.mark.parametrize("payload,match", [
    (b"not JSON", "invalid JSON"), ([], "JSON object"), ({"error": "bad graph"}, "rejected"),
    ({"prompt_id": "../escape"}, "prompt_id"), ({}, "prompt_id"),
])
def test_comfyui_rejected_or_bad_json(tmp_path, monkeypatch, payload, match):
    mock_http(monkeypatch, [payload])
    with pytest.raises(MediaError, match=match):
        generate(ComfyUIVideo(workflow(tmp_path)), tmp_path)


@pytest.mark.parametrize("exc,match", [
    (urllib.error.HTTPError("http://localhost", 400, "bad", {}, None), "HTTP 400"),
    (urllib.error.URLError("offline"), "request failed"), (TimeoutError("timeout"), "request failed"),
])
def test_comfyui_network_errors(tmp_path, monkeypatch, exc, match):
    mock_http(monkeypatch, [exc])
    with pytest.raises(MediaError, match=match):
        generate(ComfyUIVideo(workflow(tmp_path)), tmp_path)


def test_comfyui_redirects_disabled():
    with pytest.raises(MediaError, match="redirects"):
        _NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:1/secret")


@pytest.mark.parametrize("status", [{"status_str": "error"},
    {"messages": [["execution_error", {"exception_message": "OOM"}]]},
    {"messages": [["execution_interrupted", {}]]}])
def test_comfyui_terminal_error_checkpoint(tmp_path, monkeypatch, status):
    calls = mock_http(monkeypatch, [{"prompt_id": "job-1"}, history(status=status)])
    cfg = workflow(tmp_path)
    for _ in range(2):
        with pytest.raises(MediaError, match="job job-1 failed"):
            generate(ComfyUIVideo(cfg), tmp_path)
    assert len(calls) == 2


def test_comfyui_poll_timeout_then_resume_without_resubmission(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("studio.video_gen.time.monotonic", lambda: now[0])
    monkeypatch.setattr("studio.video_gen.time.sleep", lambda duration: now.__setitem__(0, now[0] + duration))
    items = [{"prompt_id": "job-1"}, {}, {}]
    calls = mock_http(monkeypatch, items)
    cfg = workflow(tmp_path, timeout_s=0.02, poll_interval_s=0.01)
    with pytest.raises(MediaError, match=r"timeout.*retained"):
        generate(ComfyUIVideo(cfg), tmp_path)
    checkpoint = next((cfg.output_dir / ".cache" / "video-jobs").glob("*.json"))
    assert json.loads(checkpoint.read_text())["prompt_id"] == "job-1"
    items.extend([history(), MP4])
    generate(ComfyUIVideo(cfg), tmp_path)
    assert [req.method for req, _ in calls].count("POST") == 1


def test_comfyui_download_failure_resume_and_byte_cap(tmp_path, monkeypatch):
    items = [{"prompt_id": "job-1"}, history(), b"x" * 65]
    calls = mock_http(monkeypatch, items)
    cfg = workflow(tmp_path, max_download_bytes=64)
    with pytest.raises(MediaError, match="byte limit"):
        generate(ComfyUIVideo(cfg), tmp_path)
    items.append(MP4)
    generate(ComfyUIVideo(cfg), tmp_path)
    assert len(calls) == 4 and calls[-1][0].full_url.startswith("http://127.0.0.1:8188/view?")


def test_comfyui_bad_download_does_not_poison_cache(tmp_path, monkeypatch):
    items = [{"prompt_id": "job-1"}, history(), b"not an mp4 file content"]
    mock_http(monkeypatch, items)
    cfg = workflow(tmp_path)
    with pytest.raises(MediaError, match="container"):
        generate(ComfyUIVideo(cfg), tmp_path)
    assert not list((cfg.output_dir / ".cache" / "video-jobs").glob("*.mp4"))
    items.append(MP4)
    generate(ComfyUIVideo(cfg), tmp_path)


@pytest.mark.parametrize("node,match", [
    ({}, "did not return"), ({"images": [{"filename": "image.png"}]}, "did not return"),
    ({"gifs": [None, {"filename": "bad.webm"}]}, "did not return"),
    ({"videos": [{"filename": "../secret.mp4"}]}, "unsafe"),
    ({"videos": [{"filename": "C:secret.mp4"}]}, "unsafe"),
    ({"videos": [{"filename": "safe.mp4", "subfolder": "../private"}]}, "unsafe"),
    ({"videos": [{"filename": "safe.mp4", "subfolder": "C:/private"}]}, "unsafe"),
    ({"videos": [{"filename": "safe.mp4", "type": "input"}]}, "output type"),
])
def test_comfyui_selected_node_only_safe_mp4(tmp_path, monkeypatch, node, match):
    output = {"3": node, "another": {"videos": [{"filename": "valid.mp4"}]}}
    mock_http(monkeypatch, [{"prompt_id": "job-1"}, history(outputs=output)])
    with pytest.raises(MediaError, match=match):
        generate(ComfyUIVideo(workflow(tmp_path)), tmp_path)


def test_comfyui_native_video_image_preview(tmp_path, monkeypatch):
    mock_http(monkeypatch, [{"prompt_id": "job-1"}, history(outputs={"3": {"images": [
        {"filename": "native.mp4", "type": "temp"}]}}), MP4])
    assert generate(ComfyUIVideo(workflow(tmp_path)), tmp_path).provider == "comfyui"


@pytest.mark.parametrize("contents,match", [("bad", "invalid job checkpoint"),
    ("[]", "invalid checkpoint"), ('{"prompt_id":"../private"}', "invalid checkpoint")])
def test_comfyui_corrupt_checkpoint_refuses_duplicate_job(tmp_path, monkeypatch, contents, match):
    calls = mock_http(monkeypatch, [{"prompt_id": "job-1"}, history(), MP4])
    cfg = workflow(tmp_path)
    generate(ComfyUIVideo(cfg), tmp_path)
    checkpoint = next((cfg.output_dir / ".cache" / "video-jobs").glob("*.json"))
    checkpoint.write_text(contents)
    with pytest.raises(MediaError, match=match):
        generate(ComfyUIVideo(cfg), tmp_path)
    assert len(calls) == 3


@pytest.mark.parametrize("record", [
    {"job-1": ["not a record"]},
    history(status=["not a status"]),
    history(status={"completed": True, "messages": 7}),
    history(outputs=["not outputs"]),
    history(outputs={"3": ["not a node"]}),
    history(outputs={"3": {"videos": 7}}),
])
def test_comfyui_malformed_history_is_media_error(tmp_path, monkeypatch, record):
    mock_http(monkeypatch, [{"prompt_id": "job-1"}, record])
    with pytest.raises(MediaError, match="malformed"):
        generate(ComfyUIVideo(workflow(tmp_path)), tmp_path)


def test_input_absurd_integer_is_media_error(tmp_path):
    with pytest.raises(MediaError, match="seed"):
        generate(FakeSceneVideo(config(tmp_path)), tmp_path, seed=10**400)


# ---------- Veo (Gemini API, ADR-0018): predictLongRunning → operation → download ----------

def veo(tmp_path: Path, monkeypatch, **options: Any) -> MediaConfig:
    monkeypatch.setenv("GEMINI_API_KEY", "veo-key")
    return config(tmp_path, provider="veo", **options)


def operation(name: str = "models/veo-3.1-fast-generate-001/operations/op-1", **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"name": name, "done": True, "response": {"generateVideoResponse": {
        "generatedSamples": [{"video": {"uri": "https://generativelanguage.googleapis.com/v1beta/files/f1:download?alt=media"}}]}}}
    record.update(overrides)
    return record


def test_veo_submit_poll_download_and_replay(tmp_path, monkeypatch):
    cfg = veo(tmp_path, monkeypatch)
    calls = mock_http(monkeypatch, [{"name": "models/veo-3.1-fast-generate-001/operations/op-1"},
                                    {"name": "models/veo-3.1-fast-generate-001/operations/op-1", "done": False},
                                    operation(), MP4])
    monkeypatch.setattr("studio.video_gen.time.sleep", lambda _: None)
    provider = make_video_generator(cfg)
    assert isinstance(provider, VeoVideo)
    result = generate(provider, tmp_path)
    assert result.path.read_bytes() == MP4 and result.duration_s == 2.5
    assert result.provider == "veo" and result.model == "veo-3.1-fast-generate-001"
    assert "operation=models/veo-3.1-fast-generate-001/operations/op-1" in result.notes and "SynthID" in result.notes
    submit = calls[0][0]
    assert submit.full_url.endswith("/v1beta/models/veo-3.1-fast-generate-001:predictLongRunning")
    assert submit.get_header("X-goog-api-key") == "veo-key" and submit.get_header("Authorization") is None
    body = json.loads(submit.data)
    assert body["instances"] == [{"prompt": "Mèo đi dưới mưa"}]
    assert body["parameters"] == {"aspectRatio": "16:9", "resolution": "720p", "durationSeconds": 4,
                                  "seed": 17, "negativePrompt": "blur"}
    assert calls[1][0].full_url.endswith("/v1beta/models/veo-3.1-fast-generate-001/operations/op-1")
    assert calls[1][0].method == "GET"
    assert calls[3][0].full_url == "https://generativelanguage.googleapis.com/v1beta/files/f1:download?alt=media"
    assert calls[3][0].get_header("X-goog-api-key") == "veo-key"
    cached = generate(VeoVideo(cfg), tmp_path, out=tmp_path / "again.mp4")
    assert cached.path.read_bytes() == MP4 and len(calls) == 4


@pytest.mark.parametrize("overrides, expected", [
    ({"duration_s": 5.5, "seed": 2**40 + 9}, {"aspectRatio": "16:9", "resolution": "720p", "durationSeconds": 6, "seed": 9}),
    ({"duration_s": 7, "negative_prompt": "  "}, {"aspectRatio": "16:9", "resolution": "720p", "durationSeconds": 8, "seed": 17}),
    ({"duration_s": 30, "resolution": "1080x1920"}, {"aspectRatio": "9:16", "resolution": "1080p", "durationSeconds": 8, "seed": 17}),
    ({"duration_s": 2, "resolution": "1920x1080"}, {"aspectRatio": "16:9", "resolution": "1080p", "durationSeconds": 8, "seed": 17}),
    ({"duration_s": 2, "resolution": "3840x2160"}, {"aspectRatio": "16:9", "resolution": "4k", "durationSeconds": 8, "seed": 17}),
])
def test_veo_parameters_snap_to_what_the_api_accepts(tmp_path, monkeypatch, overrides, expected):
    cfg = veo(tmp_path, monkeypatch)
    calls = mock_http(monkeypatch, [operation(), operation(), MP4])
    generate(VeoVideo(cfg), tmp_path, **overrides)
    parameters = json.loads(calls[0][0].data)["parameters"]
    assert {k: v for k, v in parameters.items() if k != "negativePrompt"} == expected
    assert ("negativePrompt" in parameters) == bool(overrides.get("negative_prompt", "blur").strip())


def test_veo_reference_images_inline_and_limits(tmp_path, monkeypatch):
    for i in range(2):
        (tmp_path / f"ref{i}.png").write_bytes(b"\x89PNG" + bytes([i]))
    cfg = veo(tmp_path, monkeypatch, reference_images=[str(tmp_path / "ref0.png"), str(tmp_path / "ref1.png")])
    calls = mock_http(monkeypatch, [operation(), operation(), MP4])
    generate(VeoVideo(cfg), tmp_path)
    body = json.loads(calls[0][0].data)
    refs = body["instances"][0]["referenceImages"]
    assert [r["referenceType"] for r in refs] == ["asset", "asset"]
    assert refs[1]["image"] == {"bytesBase64Encoded": base64.b64encode(b"\x89PNG\x01").decode(), "mimeType": "image/png"}
    assert body["parameters"]["durationSeconds"] == 8
    for bad in (["a"] * 4, "ref0.png", [str(tmp_path / "missing.png")], [str(tmp_path / "scene.txt")]):
        (tmp_path / "scene.txt").write_text("x")
        with pytest.raises(MediaError, match="reference_images"):
            VeoVideo(veo(tmp_path, monkeypatch, reference_images=bad))


@pytest.mark.parametrize("key, value, match", [
    ("model", "gemini-2.5-flash", "scene_video.model"),
    ("base_url", "http://127.0.0.1:9", "HTTPS"),
    ("output_format", "webm", "only mp4"),
])
def test_veo_config_invalid(tmp_path, monkeypatch, key, value, match):
    with pytest.raises(MediaError, match=match):
        VeoVideo(veo(tmp_path, monkeypatch, **{key: value}))


def test_veo_requires_gemini_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("STUDIO_MEDIA_API_KEY", raising=False)
    with pytest.raises(MediaError, match="GEMINI_API_KEY"):
        VeoVideo(config(tmp_path, provider="veo"))
    monkeypatch.setenv("MY_VEO", "k")
    assert VeoVideo(config(tmp_path, provider="veo", api_key_env="MY_VEO")).auth == {"x-goog-api-key": "k"}


@pytest.mark.parametrize("payload, match", [
    ({"error": {"message": "quota"}}, "no operation name"),
    ({"name": "../etc"}, "no operation name"),
    (b"<html>", "invalid JSON"),
    (urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"")), "Veo HTTP 429"),
])
def test_veo_submit_errors(tmp_path, monkeypatch, payload, match):
    mock_http(monkeypatch, [payload])
    with pytest.raises(MediaError, match=match):
        generate(VeoVideo(veo(tmp_path, monkeypatch)), tmp_path)
    assert not list((tmp_path / "output" / ".cache" / "video-jobs").glob("*.json"))


@pytest.mark.parametrize("done, match", [
    (operation(error={"code": 3, "message": "unsafe prompt"}), "failed: unsafe prompt"),
    (operation(response={"generateVideoResponse": {"generatedSamples": [], "raiMediaFilteredCount": 1,
                                                    "raiMediaFilteredReasons": ["violence"]}}), "filtered.*violence"),
    (operation(response={"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "http://evil.example/x.mp4"}}]}}),
     "googleapis"),
    (operation(response={"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://a.googleapis.com@evil/x"}}]}}),
     "googleapis"),
    (operation(response={"generateVideoResponse": {"generatedSamples": ["nope"]}}), "no video"),
    (operation(response="nope"), "malformed"),
])
def test_veo_operation_outcomes(tmp_path, monkeypatch, done, match):
    cfg = veo(tmp_path, monkeypatch)
    calls = mock_http(monkeypatch, [{"name": "models/veo-3.1-fast-generate-001/operations/op-1"}, done])
    with pytest.raises(MediaError, match=match):
        generate(VeoVideo(cfg), tmp_path)
    checkpoint = json.loads(next((cfg.output_dir / ".cache" / "video-jobs").glob("*.json")).read_text())
    assert checkpoint["operation"] == "models/veo-3.1-fast-generate-001/operations/op-1"
    if "error" in done:
        assert checkpoint["error"] is True
        with pytest.raises(MediaError, match=r"failed \(checkpoint retained\)"):
            generate(VeoVideo(cfg), tmp_path)
        assert len(calls) == 2


def test_veo_timeout_then_resume_and_download_cap(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("studio.video_gen.time.monotonic", lambda: now[0])
    monkeypatch.setattr("studio.video_gen.time.sleep", lambda duration: now.__setitem__(0, now[0] + duration))
    items: list[Any] = [{"name": "models/veo-3.1-fast-generate-001/operations/op-1"}, {"done": False}, {"done": False}]
    calls = mock_http(monkeypatch, items)
    cfg = veo(tmp_path, monkeypatch, timeout_s=0.02, poll_interval_s=0.01, max_download_bytes=64)
    with pytest.raises(MediaError, match=r"Veo timeout.*retained"):
        generate(VeoVideo(cfg), tmp_path)
    items.extend([operation(), b"x" * 65])
    with pytest.raises(MediaError, match="byte limit"):
        generate(VeoVideo(cfg), tmp_path)
    items.append(MP4)
    generate(VeoVideo(cfg), tmp_path)
    assert [req.method for req, _ in calls].count("POST") == 1
    assert calls[-1][0].full_url.startswith("https://generativelanguage.googleapis.com/v1beta/files/")


def test_veo_cache_identity_ignores_prices_and_key(tmp_path, monkeypatch):
    base = VeoVideo(veo(tmp_path, monkeypatch)).cache_identity
    assert VeoVideo(veo(tmp_path, monkeypatch, price_per_second=0.4, api_key="x")).cache_identity == base
    assert VeoVideo(veo(tmp_path, monkeypatch, model="veo-3.1-generate-001")).cache_identity != base
