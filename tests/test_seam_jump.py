"""scripts/h3_seam_jump.py and the harness hook for «Продолжение» A/B."""
import json
import shutil
import subprocess

import pytest

from scripts.h3_ab import continuation_source
from scripts.h3_seam_jump import seam_jump

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")


def clip(path, *filters):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=192x108:rate=24:duration=6", "-vf", ",".join(filters), "-frames:v", "72",
                    "-pix_fmt", "yuv420p", str(path)], check=True, timeout=60)


def test_a_clean_join_reads_about_one_and_a_jump_reads_high(tmp_path):
    clip(tmp_path / "prev.mp4", "null")
    clip(tmp_path / "next.mp4", "trim=start_frame=50,setpts=PTS-STARTPTS")
    clip(tmp_path / "jump.mp4", "trim=start_frame=50,setpts=PTS-STARTPTS", "eq=brightness='if(gte(n\\,22)\\,0.2\\,0)':eval=frame")
    assert seam_jump(tmp_path / "prev.mp4", tmp_path / "next.mp4", 22)["jump"] < 1.5
    assert seam_jump(tmp_path / "prev.mp4", tmp_path / "jump.mp4", 22)["jump"] > 5


def test_continuation_source_follows_the_manifest_order():
    job = {"manifest_json": json.dumps({"videos": [{"role": "Движение"}, {"role": "Продолжение"}]})}
    files = [("images", "a.png", b"img", "image/png"), ("videos", "m.mp4", b"motion", "video/mp4"),
             ("videos", "c.mp4", b"prev", "video/mp4")]
    assert continuation_source(job, files) == b"prev"
    assert continuation_source({"manifest_json": json.dumps({"videos": []})}, files) is None
