from pathlib import Path
import shutil
import subprocess

import pytest

from app.audio_mix import extract_original_audio, mix_project, probe


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg нужен для медиа-теста")


def _fixture(path: Path, *, video: bool = False, duration: float = 2) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if video:
        command += ["-f", "lavfi", "-i", f"color=c=blue:s=64x64:r=24:d={duration}"]
    command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-t", str(duration)]
    command += ["-c:v", "mpeg4", "-c:a", "aac"] if video else ["-c:a", "pcm_s16le"]
    subprocess.run([*command, str(path)], check=True, capture_output=True, timeout=30)


def test_mix_keeps_original_and_creates_distinct_mp4(tmp_path):
    source = tmp_path / "source.mp4"
    speech = tmp_path / "speech.wav"
    _fixture(source, video=True)
    _fixture(speech, duration=1)
    original_bytes = source.read_bytes()
    separated = extract_original_audio(source, tmp_path / "original.wav")
    output = mix_project(destination=tmp_path / "result.mp4", duration=2, output_format="mp4",
                         video=source, original_audio=separated, speech=[(speech, .4)], ducking=True,
                         music=speech, music_level=.2)
    assert source.read_bytes() == original_bytes
    assert separated.is_file()
    assert {s["codec_type"] for s in probe(output)["streams"]} == {"video", "audio"}


def test_audio_only_and_invalid_levels(tmp_path):
    speech = tmp_path / "speech.wav"
    _fixture(speech)
    output = mix_project(destination=tmp_path / "result.wav", duration=2, output_format="wav",
                         speech=[(speech, 0)])
    assert any(s["codec_type"] == "audio" for s in probe(output)["streams"])
    with pytest.raises(ValueError, match="Уровень"):
        mix_project(destination=tmp_path / "bad.wav", duration=2, output_format="wav",
                    speech=[(speech, 0)], speech_level=float("nan"))
