import shutil

import pytest

from studio.media import FFmpegAssembler, probe_duration
from studio.motion import MotionSpec, render_motion


def test_chart_requires_sourced_values_and_refuses_executable_payload():
    from pydantic import ValidationError
    with pytest.raises(ValidationError): MotionSpec(template="bars", title="Data", values=[2, 3], labels=["A", "B"])
    with pytest.raises(ValidationError): MotionSpec(template="title", title="Hi", code="import os")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires ffmpeg")
@pytest.mark.parametrize("template", ["title", "quote", "bars"])
def test_motion_templates_render_real_video_and_utf8(tmp_path, template):
    spec = MotionSpec(template=template, title="ĐỒNG HÀNH", subtitle="Học hỏi mỗi ngày", labels=["Hôm qua", "Hôm nay"],
                      values=[30, 60], source="Số liệu do người dùng cung cấp")
    out = tmp_path / "motion.mp4"
    render_motion(spec, out, FFmpegAssembler(threads=1), 1.2, 10, "320x180")
    assert out.stat().st_size > 1000
    assert abs(probe_duration(out) - 1.2) < .15
