"""Source excerpts at a smaller short edge (A/B, 04.11.2026): ffmpeg filter and validation."""
import shutil
import subprocess

import pytest
from pydantic import ValidationError

from local_studio.reference_segments import SegmentRequest, segment_filter


def test_filter_keeps_default_and_scales_short_edge():
    assert segment_filter(None) == "fps=24"
    assert "min(480,iw)" in segment_filter(480) and "min(480,ih)" in segment_filter(480)
    with pytest.raises(ValidationError):
        SegmentRequest(start=0, duration=5, short_edge=1080)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_vertical_1080p_becomes_480_wide(tmp_path):
    src, out = tmp_path / "src.mp4", tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=1080x1920:d=1:r=24",
                    "-pix_fmt", "yuv420p", str(src)], check=True, timeout=60)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", segment_filter(480), str(out)], check=True, timeout=60)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True, timeout=30).stdout.strip()
    assert probe == "480,854"
