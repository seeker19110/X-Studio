"""Kill the uploader process at the remote-commit boundary; all transport stays offline."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

CHILD = r'''
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from studio.events import MetadataPackage
from studio.platform import API_URL, UPLOAD_URL, Tokens, TokenStore, YouTubePlatform
import studio.platform as platform

root = Path(sys.argv[1])
phase, mode = sys.argv[2:]
server_path = root / "mock-server.json"
store = TokenStore(root / "auth" / "tokens.json")
if not store.path.exists():
    store.save(Tokens(access_token="offline", refresh_token="offline-refresh", client_id="offline-client",
                      expiry="2099-01-01T00:00:00+00:00"))
platform.UPLOAD_CHUNK_BYTES = 256 * 1024
size = (256 * 1024 + 7) if mode == "partial" else 7
video = root / "video.mp4"
if not video.exists():
    video.write_bytes(b"x" * size)

def fetch(method, url, headers, body):
    server = json.loads(server_path.read_text()) if server_path.exists() else {"posts": 0, "bytes": 0, "probes": 0, "ranges": []}
    def save():
        server_path.write_text(json.dumps(server))
    if method == "GET" and url.startswith(API_URL + "/channels"):
        return 200, {}, json.dumps({"items": [{"id": "UC-offline-channel"}]}).encode()
    if method == "POST":
        server["posts"] += 1
        save()
        assert server["posts"] == 1, "duplicate session init after process restart"
        return 200, {"location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=PRIVATE_SESSION"}, b""
    assert method == "PUT"
    if not body:
        server["probes"] += 1
        save()
        if server["bytes"] == size:
            return 201, {}, b'{"id":"confirmed-video","status":{"privacyStatus":"private"}}'
        return 308, {"range": "bytes=0-" + str(server["bytes"] - 1)}, b""
    assert headers["Content-Range"].startswith("bytes " + str(server["bytes"]) + "-")
    server["ranges"].append(headers["Content-Range"])
    server["bytes"] += len(body)
    save()
    if phase == "crash":
        os._exit(23)  # no finally/exception cleanup: server accepted bytes, client received nothing
    assert server["bytes"] == size
    return 201, {}, b'{"id":"confirmed-video","status":{"privacyStatus":"private"}}'

result = YouTubePlatform(store, fetch, lambda: datetime(2026, 10, 11, tzinfo=UTC), sleep=lambda _: None).upload_video(
    video, MetadataPackage(video_id="CH1-V1", title="Offline restart", description="test", tags=[]))
print(json.dumps({"id": result.platform_ref, "status": result.status, "evidence": result.evidence}))
'''


@pytest.mark.parametrize("mode", ["partial", "complete"])
def test_upload_recovers_after_hard_process_exit(tmp_path, mode):
    child = tmp_path / "child.py"
    child.write_text(CHILD)

    def run(phase):
        return subprocess.run([sys.executable, str(child), str(tmp_path), phase, mode],
                              capture_output=True, text=True, timeout=30)

    crashed = run("crash")
    assert crashed.returncode == 23, crashed.stderr
    recovered = run("resume")
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["id"] == "confirmed-video" and result["status"] == "private"
    assert "PRIVATE_SESSION" not in recovered.stdout + recovered.stderr
    state = json.loads((tmp_path / "mock-server.json").read_text())
    assert state["posts"] == 1 and state["probes"] == 1
    assert len(state["ranges"]) == (2 if mode == "partial" else 1)
    again = run("resume")
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["id"] == "confirmed-video"
    assert json.loads((tmp_path / "mock-server.json").read_text()) == state
