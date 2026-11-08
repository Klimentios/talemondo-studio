"""Hero swap A/B kit in scripts/h3_ab.py (04.11.2026)."""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("h3_ab", Path(__file__).parents[1] / "scripts/h3_ab.py")
h3_ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h3_ab)


def test_swap_kit_variants_reach_the_form():
    names = h3_ab.KITS["swap"].split(",")
    assert names[0] == "baseline" and set(names) <= set(h3_ab.VARIANTS)
    assert h3_ab.VARIANTS["swap-v15"] == {"style_adapter": "character_swap_v15"}
    assert h3_ab.VARIANTS["ref-beta"] == {"sampling_profile": "ref_beta"}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_src_480_shrinks_only_videos(tmp_path):
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=1080x1920:d=1:r=24", "-pix_fmt", "yuv420p",
                    str(clip)], check=True, timeout=60)
    files = [("images", "a.png", b"png", "image/png"), ("videos", "c.mp4", clip.read_bytes(), "video/mp4")]
    out = h3_ab.shrink_videos(files, 480)
    assert out[0] == files[0] and out[1][1] == "480p-c.mp4"
    shrunk = tmp_path / "s.mp4"
    shrunk.write_bytes(out[1][2])
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(shrunk)], capture_output=True, text=True, timeout=30).stdout.strip()
    assert size == "480,854"
