"""Prepare standalone audio references for the requested H3 clip duration."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tempfile

from .contract import GenerationRequest


def prepare_audio_reference(item: tuple[str, bytes, str], request: GenerationRequest) -> tuple[str, bytes, str]:
    """Trim a copy of an uploaded audio reference to the generated clip length.

    PCM WAV keeps the conversion deterministic and avoids codec padding beyond the
    requested duration. The uploaded source remains unchanged.
    """
    filename, data, _content_type = item
    duration = request.length / 24
    with tempfile.TemporaryDirectory(prefix="h3-audio-reference-") as directory:
        source = Path(directory) / Path(filename).name
        output = Path(directory) / "reference.wav"
        source.write_bytes(data)
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source), "-map", "0:a:0", "-vn", "-t", f"{duration:.6f}",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(output),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"Не удалось подготовить аудиореференс {filename}: {exc}") from exc
        if result.returncode != 0 or not output.is_file() or output.stat().st_size < 44:
            raise ValueError(f"Не удалось подготовить аудиореференс {filename}: {result.stderr[-300:]}")
        digest = hashlib.sha256(data).hexdigest()[:16]
        return f"reference-{digest}-{request.length}f.wav", output.read_bytes(), "audio/wav"
