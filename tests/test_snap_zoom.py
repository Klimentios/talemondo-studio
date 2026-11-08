"""«Резкий зум»: the zoom of a finished take is found and played in a few frames (LoopForge snap.py, MIT)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from local_studio import snap_zoom

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _zoom_clip(path: Path) -> None:
    """1 s hold, a 2 s zoom in, 2 s hold, with sound: 120 frames at 24 fps, as H3 paces a «crash» zoom."""
    zoom = "if(lt(on,24),1,if(lt(on,72),1+(on-24)/48*3,4))"
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=720x1280:r=24:d=5", "-f", "lavfi", "-i", "sine=frequency=330:duration=5",
                    "-vf", f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=360x640:fps=24",
                    "-shortest", "-pix_fmt", "yuv420p", str(path)], check=True)


def test_the_zoom_is_found_and_played_in_three_frames(tmp_path):
    raw, snapped = tmp_path / "raw.mp4", tmp_path / "snapped.mp4"
    _zoom_clip(raw)
    report = snap_zoom.snap(raw, snapped)
    assert report["ramped"]
    start, end = report["window"]
    assert 15 <= start <= 30 and 65 <= end <= 80  # the 24–72 zoom, give or take the detector's margins
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(snapped)],
                           capture_output=True, text=True, check=True)
    expected = (120 - (end - start) + snap_zoom.TARGET_FRAMES) / 24
    assert float(probe.stdout) == pytest.approx(expected, abs=0.15)


def test_a_held_shot_is_left_alone(tmp_path):
    raw = tmp_path / "held.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=360x640:r=24:d=3", "-pix_fmt", "yuv420p", str(raw)], check=True)
    assert snap_zoom.snap(raw, tmp_path / "out.mp4")["ramped"] is False
    assert not (tmp_path / "out.mp4").exists()
