"""Evidence the Studio agent reviews a shot from: full decode and hard cuts of the MP4."""
from __future__ import annotations

import unittest

class ShotEvidenceTests(unittest.TestCase):
    def test_scene_detector_finds_abrupt_change_without_new_dependency(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from local_studio.scene_qc import scene_change_times
        ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
        if not Path(ffmpeg).exists():
            self.skipTest("ffmpeg недоступен")
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / "cut.mp4"
            subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=red:s=64x64:r=10:d=1", "-f", "lavfi", "-i",
                            "color=c=blue:s=64x64:r=10:d=1", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
                            "-c:v", "mpeg4", str(clip)], check=True)
            self.assertTrue(any(0.8 <= t <= 1.2 for t in scene_change_times(clip, 2)))

    def test_full_mp4_scan_reports_video_and_audio(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from local_studio.scene_qc import technical_scan
        ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
        if not Path(ffmpeg).exists():
            self.skipTest("ffmpeg недоступен")
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / "test.mp4"
            subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=10",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "1",
                            "-c:v", "mpeg4", "-c:a", "aac", str(clip)], check=True)
            result = technical_scan(clip)
            self.assertEqual((result["full_decode"], result["audio_present"], result["width"]), (True, True, 64))
            with self.assertRaises(ValueError): technical_scan(Path(temp) / "missing.mp4")
