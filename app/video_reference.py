"""Prepare H3 video references without changing the user's original upload."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from .contract import GenerationRequest


# Ref2VA encodes a reference video at its own size (never upscaled, capped at a 768x1344 canvas):
# a full-size reference adds as many tokens to every sampling step as the generated clip itself.
# Roles that only carry movement or timing do not need that detail; the generated frames keep
# their own resolution. Everything else (source scene, acting, unknown roles) stays full size.
# Body motion keeps 512: at 384 (13ac71ee, 2026-10-04) arms and hands of a full-body dancer were a
# few pixels wide and H3 did not reproduce the choreography.
_MOTION_ROLE_SHORT_EDGE = {
    "Движение": 512, "motion": 512, "structure": 512,
    "Движение и камера": 512, "motion_camera": 512,
    # Source footage often arrives at 1080p/4K and is encoded as reference tokens
    # at every H3 sampling step. Keep the same 512p ceiling as motion references.
    "Исходное видео": 512, "source": 512,
    "Камера": 256, "camera": 256, "Ритм сцены": 256,
}
_FULL_LENGTH_MOTION_ROLES = {
    "Движение", "motion", "structure", "Движение и камера", "motion_camera",
}


def reference_video_short_edge(role: str | None) -> int | None:
    """Short edge for a role-limited reference video, or None to keep it full size.

    H3_MOTION_REF_SHORT_EDGE=0 keeps every reference video full size.
    """
    if os.environ.get("H3_MOTION_REF_SHORT_EDGE") == "0":
        return None
    edge = _MOTION_ROLE_SHORT_EDGE.get((role or "").strip())
    override = os.environ.get("H3_MOTION_REF_SHORT_EDGE")
    if edge and override:
        edge = int(override)
    return edge


def motion_reference_requires_full_duration(role: str | None) -> bool:
    return (role or "").strip() in _FULL_LENGTH_MOTION_ROLES


def _video_duration_seconds(path: Path, filename: str) -> float:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Не удалось проверить длительность видео {filename}: {exc}") from exc
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        detail = result.stderr[-200:] if result.stderr else "неизвестная длительность"
        raise ValueError(f"Не удалось проверить длительность видео {filename}: {detail}") from exc
    if result.returncode != 0 or duration <= 0:
        raise ValueError(f"Не удалось проверить длительность видео {filename}: {result.stderr[-200:]}")
    return duration


def prepare_video_reference(
    item: tuple[str, bytes, str], request: GenerationRequest, *, keep_audio: bool,
    exact_geometry: bool = False, short_edge: int | None = None,
    minimum_duration_seconds: float | None = None,
) -> tuple[str, bytes, str]:
    """Match H3's 24 fps time base and pad control/quick inputs to its geometry.

    The caller verifies the original bytes against the immutable job manifest
    before calling this function. Only the copy uploaded to ComfyUI is changed.
    """
    filename, data, _mime = item
    with tempfile.TemporaryDirectory(prefix="h3-reference-") as directory:
        source = Path(directory) / "source"
        output = Path(directory) / "reference.mp4"
        source.write_bytes(data)
        if minimum_duration_seconds is not None:
            source_duration = _video_duration_seconds(source, filename)
            if source_duration + 0.04 < minimum_duration_seconds:
                raise ValueError(
                    f"Видео «Движение» длится {source_duration:.2f} с, а сцена — "
                    f"{minimum_duration_seconds:.2f} с. Добавь более длинный референс "
                    "или сократи длительность генерации."
                )
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source), "-map", "0:v:0",
        ]
        if keep_audio:
            command += ["-map", "0:a:0?"]
        filters = ["fps=24"]
        if short_edge and not exact_geometry:
            # Short side to `short_edge`, long side kept proportional; both multiples of 32 (H3 canvas).
            filters.append(
                f"scale='if(lt(iw,ih),{short_edge},trunc(iw*{short_edge}/ih/32)*32)'"
                f":'if(lt(iw,ih),trunc(ih*{short_edge}/iw/32)*32,{short_edge})':flags=lanczos"
            )
        elif request.quality in {"compact", "quick"} or exact_geometry:
            filters.extend([
                f"scale={request.width}:{request.height}:force_original_aspect_ratio=decrease",
                f"pad={request.width}:{request.height}:(ow-iw)/2:(oh-ih)/2",
            ])
        command += [
            "-vf", ",".join(filters), "-frames:v", str(request.length),
            "-t", f"{request.length / 24:.6f}", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        ]
        command += ["-c:a", "aac", "-b:a", "192k"] if keep_audio else ["-an"]
        command += ["-movflags", "+faststart", str(output)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"Не удалось подготовить видео-референс {filename}: {exc}") from exc
        if result.returncode != 0 or not output.is_file() or output.stat().st_size < 1024:
            raise ValueError(f"Не удалось подготовить видео-референс {filename}: {result.stderr[-300:]}")
        digest = hashlib.sha256(data).hexdigest()[:16]
        suffix = f"-{short_edge}p" if short_edge and not exact_geometry else ""
        return f"reference-{digest}-24fps{suffix}.mp4", output.read_bytes(), "video/mp4"
