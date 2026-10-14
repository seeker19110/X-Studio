"""Contact sheet của bản dựng cuối (ADR-0016): một khung hình giữa mỗi cảnh, trích từ CHÍNH file MP4 cuối, ghép lưới
có nhãn cảnh + mốc giờ — người duyệt gate publish soi cả video trong một ảnh thay vì tua từng đoạn."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studio import media as media_mod
from studio.bus import InMemoryBus
from studio.events import Envelope, MediaAsset, Provenance, Scene, SceneManifest
from studio.media import FFmpegAssembler, MediaConfig, MediaError, make_media, sample_marks
from studio.renderer import Renderer
from studio.sandbox import SubprocessSandbox

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _manifest(n: int, vid: str = "VC") -> SceneManifest:
    return SceneManifest(video_id=vid, scenes=[
        Scene(scene_id=f"S{i + 1}", order=i, narration=f"Câu thứ {i + 1} của cảnh.", visual_prompt=f"cảnh {i + 1}",
              duration_s=2.0) for i in range(n)])


def _dims(path: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def test_sample_marks_keeps_every_scene_until_the_cap_then_spreads_evenly():
    marks = [(float(i), f"S{i}") for i in range(10)]
    assert media_mod.sample_marks(marks, 24) == marks
    picked = sample_marks(marks, 4)
    assert len(picked) == 4 and picked[0] == marks[0] and picked[-1] == marks[-1]
    assert picked == sorted(picked)
    assert sample_marks(marks, 1) == [marks[0]] and sample_marks([], 4) == []


@pytest.mark.skipif(not HAS_FFMPEG, reason="cần ffmpeg + ffprobe")
def test_finalize_publishes_a_contact_sheet_built_from_the_final_render(tmp_path):
    cfg = MediaConfig(output_dir=tmp_path, video={"provider": "ffmpeg", "fps": 12, "resolution": "320x180"})
    bus = InMemoryBus(); r = Renderer(bus, make_media(cfg), tmp_path)
    m = _manifest(3); r.render(m); r.finalize(m)
    sheet = next(e.payload for e in bus.replay("media-assets", "VC") if e.payload["kind"] == "contact_sheet")
    path = Path(sheet["path"])
    assert path == tmp_path / "VC" / "contact_sheet_v1.jpg" and path.is_file()
    assert sheet["provider"] == "ffmpeg" and sheet["provenance"]["generated_by"] == "ffmpeg:contact-sheet"
    assert sheet["manifest_version"] == 1 and sheet["checksum"] and sheet["provenance"]["license"] == "generated"
    # 3 cảnh → một hàng 3 ô kích thước gốc (không phóng to khung 320x180), lề 6 px
    assert _dims(path) == (3 * 320 + 4 * 6, 180 + 2 * 6)
    final_audit = next(json.loads(e.payload["evidence"]) for e in bus.replay("audit-log")
                       if e.payload["action"] == "render.final")
    assert ["contact_sheet", None] in final_audit["assets"]
    assert not list((tmp_path / "VC").glob(".finalize-*")), "thư mục tạm phải được dọn"


def test_contact_sheet_seeks_mid_scene_labels_panels_and_tiles_them(tmp_path, monkeypatch):
    calls: list[tuple[list[str], Path]] = []
    font = tmp_path / "font.ttf"; font.write_bytes(b"ttf")

    def run(argv, *a, cwd=None, **k):
        calls.append((list(argv), Path(cwd)))
        Path(cwd, argv[-1]).write_bytes(b"jpg")  # ffmpeg luôn ghi file đích ở đối số cuối
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(media_mod.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(media_mod, "probe_duration", lambda p: 9.0)
    asm = FFmpegAssembler(font=font, sandbox=SubprocessSandbox(runner=run))
    video = tmp_path / "final.mp4"; video.write_bytes(b"mp4")
    out = tmp_path / "sheet.jpg"
    marks = [(1.0, "S1 · 00:00"), (4.5, "S2 · 00:03"), (20.0, "S3 · 00:08")]
    res = asm.contact_sheet(video, marks, out)
    assert out.read_bytes() == b"jpg" and res.provider == "ffmpeg" and res.model == "contact-sheet"
    assert "3/3 cảnh" in res.notes and "không nhãn" not in res.notes
    grabs, tile = calls[:-1], calls[-1]
    seeks = [float(argv[argv.index("-ss") + 1]) for argv, _ in grabs]
    assert seeks == [1.0, 4.5, 8.95], "mốc vượt quá thời lượng phải kẹp về trước khung cuối"
    assert all("drawtext=textfile=l00" in argv[argv.index("-vf") + 1] for argv, _ in grabs)
    assert (grabs[0][1] / "l000.txt").exists() is False, "thư mục tạm phải bị xoá"
    assert "tile=3x1" in tile[0][tile[0].index("-vf") + 1]
    # nhiều cảnh hơn trần: lấy mẫu đều, lưới 4 cột
    calls.clear()
    asm.contact_sheet(video, [(float(i) / 4, f"S{i}") for i in range(30)], out)
    assert len(calls) == media_mod.CONTACT_SHEET_MAX + 1
    assert f"tile=4x{media_mod.CONTACT_SHEET_MAX // 4}" in calls[-1][0][calls[-1][0].index("-vf") + 1]


def test_contact_sheet_without_a_font_says_so_and_refuses_empty_marks(tmp_path, monkeypatch):
    def run(argv, *a, cwd=None, **k):
        Path(cwd, argv[-1]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(media_mod.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(media_mod, "find_font", lambda video=None: None)
    monkeypatch.setattr(media_mod, "probe_duration", lambda p: None)
    asm = FFmpegAssembler(sandbox=SubprocessSandbox(runner=run))
    video = tmp_path / "final.mp4"; video.write_bytes(b"mp4")
    res = asm.contact_sheet(video, [(1.0, "S1")], tmp_path / "s.jpg")
    assert "không nhãn" in res.notes
    with pytest.raises(MediaError):
        asm.contact_sheet(video, [], tmp_path / "s.jpg")


@pytest.mark.skipif(not HAS_FFMPEG, reason="cần ffmpeg + ffprobe")
def test_a_failed_contact_sheet_never_costs_the_final_render(tmp_path, monkeypatch):
    def boom(self, video, marks, out):
        raise MediaError("tile hỏng")

    monkeypatch.setattr(FFmpegAssembler, "contact_sheet", boom)
    cfg = MediaConfig(output_dir=tmp_path, video={"provider": "ffmpeg", "fps": 12, "resolution": "320x180"})
    bus = InMemoryBus(); r = Renderer(bus, make_media(cfg), tmp_path)
    m = _manifest(2); r.render(m); final = r.finalize(m)
    assert Path(final.path).is_file()
    kinds = [e.payload["kind"] for e in bus.replay("media-assets", "VC")]
    assert "final_video" in kinds and "contact_sheet" not in kinds
    failed = [json.loads(e.payload["evidence"]) for e in bus.replay("audit-log")
              if e.payload["action"] == "render.contact_sheet_failed"]
    assert failed and "tile hỏng" in failed[0]["error"]


def test_fake_video_has_no_contact_sheet(tmp_path):
    bus = InMemoryBus(); r = Renderer(bus, make_media(MediaConfig(output_dir=tmp_path)), tmp_path)
    m = _manifest(2); r.render(m); r.finalize(m)
    assert all(e.payload["kind"] != "contact_sheet" for e in bus.replay("media-assets", "VC"))


def test_publish_checklist_points_the_reviewer_at_the_contact_sheet_of_the_same_final(tmp_path):
    """Gate publish chỉ trỏ tới contact sheet của ĐÚNG phiên bản bản cuối: bản sheet cũ của v1 không được đứng tên
    cho bản cuối v2 (lần dựng v2 có thể đã hỏng phần sheet)."""
    from test_orchestrator import _to_publish_gate
    bus, o = _to_publish_gate(tmp_path)
    vid = "CH1-V1"
    final = next(e.payload for e in reversed(list(bus.replay("media-assets", vid))) if e.payload["kind"] == "final_video")
    assert not any(c.startswith("contact_sheet:") for c in o._publish_checklist(vid)), "bản giả không có sheet"

    def sheet(version: int, name: str) -> None:
        a = MediaAsset(video_id=vid, kind="contact_sheet", path=str(tmp_path / name), manifest_version=version,
                       provider="ffmpeg", checksum="x", provenance=Provenance(generated_by="ffmpeg:contact-sheet"))
        bus.publish(Envelope(topic="media-assets", key=vid, actor="renderer", payload=a.model_dump()))

    sheet(final["manifest_version"] + 1, "khac.jpg")
    assert not any(c.startswith("contact_sheet:") for c in o._publish_checklist(vid))
    sheet(final["manifest_version"], "dung.jpg")
    assert f"contact_sheet:{tmp_path / 'dung.jpg'}" in o._publish_checklist(vid)


def test_rights_checker_is_not_shown_the_internal_contact_sheet(tmp_path):
    """Sheet là công cụ soát nội bộ, không lên nền tảng: đưa nó vào payload rights-checker là đổi đầu vào model mà
    không ghi lại eval (CLAUDE.md), và bắt model xét quyền một file không ai phát hành."""
    from studio.orchestrator import _with_provenance
    from test_orchestrator import _to_publish_gate
    bus, o = _to_publish_gate(tmp_path)
    a = MediaAsset(video_id="CH1-V1", kind="contact_sheet", path=str(tmp_path / "s.jpg"), manifest_version=1,
                   provider="ffmpeg", checksum="x", provenance=Provenance(generated_by="ffmpeg:contact-sheet"))
    env = bus.publish(Envelope(topic="media-assets", key="CH1-V1", actor="renderer", payload=a.model_dump()))
    kinds = {x["kind"] for x in _with_provenance(env, o)["assets"]}
    assert "final_video" in kinds and "contact_sheet" not in kinds


def test_publish_checklist_shows_the_synthetic_media_decision(tmp_path):
    """ADR-0018: dòng `synthetic_media:` luôn có ở gate publish. Explainer bằng ảnh: code không đoán → "(chưa khai)";
    chính sách kênh `production.synthetic_media` thắng và được code ghi vào metadata sau bản cuối."""
    from test_orchestrator import _to_publish_gate
    bus, o = _to_publish_gate(tmp_path)
    vid = "CH1-V1"
    assert "synthetic_media:(chưa khai — người duyệt quyết; YouTube tự gắn nhãn AI nếu bỏ trống)" in o._publish_checklist(vid)
    assert bus.latest("metadata-packages", vid).payload.get("synthetic_media") is None
    o.renderer.media.cfg.production["synthetic_media"] = False
    from studio.events import SceneManifest
    m = SceneManifest.model_validate(bus.latest("scene-manifests", vid).payload)
    final = next(a for a in reversed([e.payload for e in bus.replay("media-assets", vid)]) if a["kind"] == "final_video")
    from studio.events import MediaAsset
    from studio.orchestrator import StepResult
    o._after_final(m, None, MediaAsset.model_validate(final), StepResult("e1", "media-assets", vid))
    assert bus.latest("metadata-packages", vid).payload["synthetic_media"] is False
    assert "synthetic_media:False" in o._publish_checklist(vid)
    assert any(e.payload["action"] == "synthetic_media.decided" for e in bus.replay("audit-log"))


def test_publish_checklist_shows_the_paid_product_placement_policy(tmp_path):
    """Khai tài trợ (YouTube 7/2026) là chính sách kênh `production.paid_product_placement`; code ghi vào metadata sau
    bản cuối và gate publish luôn có dòng `paid_product_placement:`."""
    from studio.events import MediaAsset, SceneManifest
    from studio.orchestrator import StepResult
    from test_orchestrator import _to_publish_gate
    bus, o = _to_publish_gate(tmp_path)
    vid = "CH1-V1"
    assert any(x.startswith("paid_product_placement:(chưa khai") for x in o._publish_checklist(vid))
    o.renderer.media.cfg.production["paid_product_placement"] = True
    m = SceneManifest.model_validate(bus.latest("scene-manifests", vid).payload)
    final = next(a for a in reversed([e.payload for e in bus.replay("media-assets", vid)]) if a["kind"] == "final_video")
    o._after_final(m, None, MediaAsset.model_validate(final), StepResult("e1", "media-assets", vid))
    assert bus.latest("metadata-packages", vid).payload["paid_product_placement"] is True
    assert "paid_product_placement:True" in o._publish_checklist(vid)
    assert any(e.payload["action"] == "paid_product_placement.decided" for e in bus.replay("audit-log"))
