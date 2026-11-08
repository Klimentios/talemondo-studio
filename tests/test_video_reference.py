from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.contract import GenerationRequest
from app.video_reference import (motion_reference_requires_full_duration, prepare_video_reference,
                                 reference_video_short_edge)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class VideoReferencePreparationTests(unittest.TestCase):
    def test_quick_video_is_24fps_output_sized_and_keeps_optional_audio(self):
        request = GenerationRequest("use <Video 1>", 5, "9:16", "quick", steps=24)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x1120:rate=30:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                "-shortest", str(source),
            ], check=True, timeout=60)
            original = source.read_bytes()
            for keep_audio in (False, True):
                with self.subTest(keep_audio=keep_audio):
                    name, prepared, mime = prepare_video_reference(
                        ("source.mp4", original, "video/mp4"), request, keep_audio=keep_audio
                    )
                    self.assertEqual(mime, "video/mp4")
                    self.assertTrue(name.endswith("-24fps.mp4"))
                    self.assertEqual(source.read_bytes(), original)
                    target = Path(directory) / f"prepared-{keep_audio}.mp4"
                    target.write_bytes(prepared)
                    probe = subprocess.run([
                        "ffprobe", "-v", "error", "-count_frames", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate,nb_read_frames", "-of", "json", str(target),
                    ], capture_output=True, text=True, check=True, timeout=30)
                    streams = json.loads(probe.stdout)["streams"]
                    video = next(stream for stream in streams if stream["codec_type"] == "video")
                    self.assertEqual(video["r_frame_rate"], "24/1")
                    self.assertEqual(video["width"], request.width)
                    self.assertEqual(video["height"], request.height)
                    self.assertEqual(int(video["nb_read_frames"]), request.length)
                    self.assertEqual(any(stream["codec_type"] == "audio" for stream in streams), keep_audio)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class MotionReferenceSizeTests(unittest.TestCase):
    def _probe(self, data: bytes, directory: str) -> dict:
        target = Path(directory) / "probe.mp4"
        target.write_bytes(data)
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
            "stream=width,height,r_frame_rate,nb_read_frames", "-of", "json", str(target),
        ], capture_output=True, text=True, check=True, timeout=30)
        return json.loads(probe.stdout)["streams"][0]

    def test_motion_video_is_encoded_small_with_every_frame_and_24fps(self):
        request = GenerationRequest("<Picture 1> repeats the movement from <Video 1>", 5, "9:16", "custom",
                                    steps=24, megapixels=0.98)
        with tempfile.TemporaryDirectory() as directory:
            for size, expected in (("1080x1920", (512, 896)), ("1920x1080", (896, 512))):
                with self.subTest(size=size):
                    source = Path(directory) / "source.mp4"
                    subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"testsrc2=size={size}:rate=30:duration=6", "-c:v", "libx264",
                        "-preset", "ultrafast", str(source)], check=True, timeout=60)
                    name, prepared, _ = prepare_video_reference(
                        ("move.mp4", source.read_bytes(), "video/mp4"), request, keep_audio=False,
                        short_edge=reference_video_short_edge("Движение"))
                    video = self._probe(prepared, directory)
                    self.assertEqual((video["width"], video["height"]), expected)
                    self.assertEqual(video["r_frame_rate"], "24/1")
                    self.assertEqual(int(video["nb_read_frames"]), request.length)
                    self.assertTrue(name.endswith("-512p.mp4"))

    def test_only_movement_roles_are_reduced_and_it_can_be_disabled(self):
        self.assertEqual(reference_video_short_edge("Движение"), 512)
        self.assertEqual(reference_video_short_edge("Камера"), 256)
        self.assertEqual(reference_video_short_edge("Движение и камера"), 512)
        self.assertEqual(reference_video_short_edge("motion_camera"), 512)
        for role in ("Актёрская игра", "Авто — определить по содержимому", None, ""):
            self.assertIsNone(reference_video_short_edge(role))
        self.assertEqual(reference_video_short_edge("Исходное видео"), 512)
        self.assertEqual(reference_video_short_edge("source"), 512)
        with patch.dict(os.environ, {"H3_MOTION_REF_SHORT_EDGE": "0"}):
            self.assertIsNone(reference_video_short_edge("Движение"))
        with patch.dict(os.environ, {"H3_MOTION_REF_SHORT_EDGE": "384"}):
            self.assertEqual(reference_video_short_edge("Движение"), 384)
            self.assertEqual(reference_video_short_edge("Исходное видео"), 384)

    def test_motion_video_shorter_than_the_scene_is_rejected(self):
        request = GenerationRequest("use <Video 1>", 5, "9:16", "custom", steps=24, megapixels=0.98)
        self.assertTrue(motion_reference_requires_full_duration("Движение"))
        self.assertTrue(motion_reference_requires_full_duration("Движение и камера"))
        self.assertFalse(motion_reference_requires_full_duration("Исходное видео"))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "short.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                "-i", "testsrc2=size=320x568:rate=24:duration=4", "-c:v", "libx264",
                "-preset", "ultrafast", str(source)], check=True, timeout=60)
            with self.assertRaisesRegex(ValueError, "длится"):
                prepare_video_reference(
                    (source.name, source.read_bytes(), "video/mp4"), request, keep_audio=False,
                    short_edge=512, minimum_duration_seconds=request.duration)

    def test_source_video_reference_is_capped_at_512p(self):
        request = GenerationRequest("use <Video 1>", 5, "9:16", "custom", steps=24, megapixels=0.98)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                "-i", "testsrc2=size=1080x1920:rate=24:duration=6", "-c:v", "libx264",
                "-preset", "ultrafast", str(source)], check=True, timeout=60)
            name, prepared, _ = prepare_video_reference(
                ("source.mp4", source.read_bytes(), "video/mp4"), request, keep_audio=False,
                short_edge=reference_video_short_edge("Исходное видео"))
            video = self._probe(prepared, directory)
            self.assertEqual((video["width"], video["height"]), (512, 896))
            self.assertEqual(video["r_frame_rate"], "24/1")
            self.assertEqual(int(video["nb_read_frames"]), request.length)
            self.assertTrue(name.endswith("-512p.mp4"))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class DirectorVideoSampleTests(unittest.TestCase):
    def test_reads_frames_when_the_mp4_index_follows_the_frames(self):
        from app.director import _video_sample_jpegs
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "phone.mp4"
            # No +faststart: the moov index is written after mdat, like IMG_8513.MP4.
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                            "-i", "testsrc2=size=464x848:rate=30:duration=4", "-c:v", "libx264",
                            "-preset", "ultrafast", str(source)], check=True, timeout=60)
            data = source.read_bytes()
            self.assertGreater(data.find(b"moov"), data.find(b"mdat"))
            frames = _video_sample_jpegs(data, "phone.mp4")
            self.assertEqual(len(frames), 3)
            self.assertTrue(all(frame[:2] == b"\xff\xd8" for frame in frames))


def test_reference_video_role_prefers_studio_then_qwen_plan():
    from app.main import reference_video_role
    manifest = {"videos": [{"asset_id": "a", "role": "Движение"},
                           {"asset_id": "b", "role": "Авто — определить по содержимому"},
                           {"asset_id": "c"}]}
    compiled = {"roles": {"a": "Камера", "b": "Движение"}}
    assert reference_video_role(manifest, compiled, 0) == "Движение"
    assert reference_video_role(manifest, compiled, 1) == "Движение"
    assert reference_video_role(manifest, compiled, 2) is None
    assert reference_video_role(None, compiled, 0) is None
