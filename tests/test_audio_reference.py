from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from app.audio_reference import prepare_audio_reference
from app.contract import GenerationRequest


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_standalone_audio_reference_is_trimmed_to_clip_duration_and_source_is_unchanged():
    request = GenerationRequest("use the voice reference", 5, "9:16", "custom", steps=24, megapixels=0.98)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "voice.wav"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=48000:duration=8", "-c:a", "pcm_s16le", str(source),
        ], check=True, timeout=30)
        original = source.read_bytes()
        name, prepared, content_type = prepare_audio_reference((source.name, original, "audio/wav"), request)
        target = Path(directory) / name
        target.write_bytes(prepared)
        info = json.loads(subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration,sample_rate,channels",
            "-of", "json", str(target),
        ], capture_output=True, text=True, check=True, timeout=30).stdout)
        audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
        assert abs(float(audio["duration"]) - request.length / 24) < 0.001
        assert (audio["sample_rate"], audio["channels"]) == ("48000", 2)
        assert name.endswith(f"-{request.length}f.wav")
        assert content_type == "audio/wav"
        assert source.read_bytes() == original
