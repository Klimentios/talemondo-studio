"""Director preview frames (Qwen input) must never fail a job.

2026-10-03 jobs d673fb35 and b684921a failed with "Не удалось прочитать кадры видео IMG_8513.MP4
для локального Qwen": an iPhone clip whose frames ffmpeg could not grab stopped the whole job
before H3. Now HDR is tone-mapped, a failed fast seek falls back to an exact seek, unreadable
samples are skipped, and the GPU API plans that asset without pictures.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app import director as d

FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
def test_iphone_like_hdr_hevc_with_index_at_the_end_gives_three_frames():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "IMG_8513.MOV"
        made = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
             "testsrc2=size=320x568:rate=30:duration=3", "-c:v", "libx265", "-preset", "ultrafast",
             "-x265-params", "log-level=none:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
             "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
             str(source)], capture_output=True, timeout=120)
        if made.returncode:
            pytest.skip("libx265 unavailable")
        data = source.read_bytes()
        assert data.find(b"moov") > data.find(b"mdat")
        duration, hdr = d._probe_video(str(source))
        assert hdr and duration and duration > 2
        frames = d._video_sample_jpegs(data, source.name)
    assert len(frames) == 3 and all(frame[:2] == b"\xff\xd8" for frame in frames)


def test_frame_grab_falls_back_to_exact_seek_and_skips_unreadable_samples():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, json.dumps(
                {"format": {"duration": "N/A"}, "streams": [{"duration": "10.0"}]}).encode(), b"")
        seek_before_input = cmd.index("-i") > cmd.index("-ss") if "-ss" in cmd else False
        stamp = cmd[cmd.index("-ss") + 1] if "-ss" in cmd else None
        if seek_before_input or stamp == "8.000":  # fast seek broken; last sample unreadable
            return subprocess.CompletedProcess(cmd, 1, b"", b"Invalid NAL unit size")
        return subprocess.CompletedProcess(cmd, 0, b"\xff\xd8frame", b"")

    with patch("subprocess.run", fake_run):
        frames = d._video_sample_jpegs(b"data", "phone.mov")
    assert frames == [b"\xff\xd8frame", b"\xff\xd8frame"]
    assert any(cmd.index("-ss") > cmd.index("-i") for cmd in calls if "-ss" in cmd)


def test_unreadable_video_raises_only_after_every_fallback():
    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 1, b"", b"moov atom not found")
        return subprocess.CompletedProcess(cmd, 1, b"", b"moov atom not found")

    with patch("subprocess.run", fake_run):
        with pytest.raises(ValueError, match="IMG_8513.MP4.*moov atom not found"):
            d._video_sample_jpegs(b"broken", "IMG_8513.MP4")


def test_gpu_api_plans_without_frames_when_a_preview_cannot_be_decoded():
    from app.main import _build_director_references

    def broken(data, filename):
        raise ValueError(f"Не удалось прочитать кадры видео {filename} для локального Qwen")

    manifest = {"images": [{"asset_id": "hero", "role": "Лицо героя"}],
                "videos": [{"asset_id": "clip", "role": "Движение"}], "audios": []}
    groups = {"images": [("hero.png", b"png", "image/png")],
              "videos": [("IMG_8513.MP4", b"mp4", "video/mp4")], "audios": []}
    with patch("app.main._video_sample_jpegs", broken), \
         patch("app.main._image_jpeg", lambda data, filename: b"\xff\xd8jpeg"):
        records, kinds = asyncio.run(_build_director_references(manifest, groups))
    assert kinds == {"hero": "image", "clip": "video"}
    assert records[0]["frames"] == [b"\xff\xd8jpeg"] and records[1]["frames"] == []
