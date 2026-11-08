"""Evidence of a finished shot or a library video for the Studio agent (MCP job_frames / asset_frames):
full decode, contact sheets, hard cuts and sound. The agent does the visual review; no GPU, no Qwen."""
from __future__ import annotations

import asyncio

import base64
import json
import math
import re
from pathlib import Path
import shutil
import subprocess

from fastapi import APIRouter, HTTPException

from .store import StudioStore


def technical_scan(path: Path) -> dict:
    """Decode the entire clip (video and audio), not just the frames on the contact sheet."""
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                            "-of", "json", str(path)], capture_output=True, timeout=15, check=False)
    if probe.returncode:
        raise ValueError("MP4 не читается ffprobe")
    info = json.loads(probe.stdout)
    streams = info.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video or not video.get("width") or not video.get("height"):
        raise ValueError("В MP4 нет корректного видеопотока")
    duration = float(info.get("format", {}).get("duration") or 0)
    if not 0 < duration <= 20:
        raise ValueError("Длительность MP4 вне диапазона шота")
    decoded = subprocess.run([ffmpeg, "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-map", "0:a?",
                              "-f", "null", "-"], capture_output=True, timeout=max(45, duration * 5), check=False)
    if decoded.returncode:
        raise ValueError("Полный декод MP4 не прошёл: " + decoded.stderr.decode("utf-8", "replace")[-300:])
    return {"full_decode": True, "duration": round(duration, 3), "width": video["width"], "height": video["height"],
            "audio_present": any(stream.get("codec_type") == "audio" for stream in streams)}


def scene_change_times(path: Path, duration: float) -> list[float]:
    """Find abrupt visual cuts with FFmpeg's built-in scene detector."""
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "info", "-i", str(path),
                             "-vf", "scale=256:-2,scdet=threshold=10,metadata=print", "-an", "-f", "null", "-"],
                            capture_output=True, timeout=max(45, duration * 5), check=False)
    if result.returncode:
        raise ValueError("Не удалось найти переходы MP4")
    matches = re.findall(rb"lavfi\.scd\.time=([0-9.]+)", result.stderr)
    return [time for raw in matches if 0.1 < (time := float(raw)) < duration - 0.15]


def contact_sheet(path: Path, count: int = 8, max_seconds: float = 20,
                  start_s: float = 0, end_s: float | None = None) -> tuple[bytes, list[float]]:
    """One JPEG of `count` evenly spaced frames, left to right and top to bottom, for the Studio agent to look at."""
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                           capture_output=True, timeout=15, check=False)
    if probe.returncode != 0:
        raise ValueError("Не удалось прочитать длительность MP4")
    duration = float(json.loads(probe.stdout)["format"]["duration"])
    if not 0 < duration <= max_seconds:
        raise ValueError("Недопустимая длительность видео для листа кадров")
    end = duration if end_s is None else float(end_s)
    if not math.isfinite(start_s) or not math.isfinite(end) or not 0 <= start_s < end <= duration + 0.02:
        raise ValueError("Недопустимый интервал видео для листа кадров")
    span = min(end, duration) - start_s
    columns = 4
    rows = -(-count // columns)
    result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(start_s), "-i", str(path),
                             "-t", str(span), "-vf",
                             f"fps={count}/{span:.3f},scale=320:-2,tile={columns}x{rows}:padding=4:color=black",
                             "-frames:v", "1", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                            capture_output=True, timeout=max(45, duration * 5), check=False)
    if result.returncode != 0 or not result.stdout.startswith(b"\xff\xd8"):
        raise ValueError("Не удалось собрать кадры MP4")
    return result.stdout, [round(start_s + (index + 0.5) * span / count, 2) for index in range(count)]


def has_audio(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    probe = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                            str(path)], capture_output=True, timeout=15, check=False)
    return probe.returncode == 0 and bool(probe.stdout.strip())


def make_router(store: StudioStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/assets/{asset_id}/frames")
    async def asset_frames(asset_id: str, count: int = 12, start_s: float = 0, end_s: float | None = None) -> dict:
        """A library video for the Studio agent (MCP asset_frames): what happens in it, who is where, its
        length, sound and hard cuts — e.g. before replacing a performer with a hero or taking its motion."""
        asset = store.get_asset(asset_id)
        if not asset or asset.get("kind") != "video":
            raise HTTPException(404, "Видео нет в библиотеке")
        path = Path(asset["path"])
        if not path.is_file() or not path.resolve().is_relative_to(store.assets_dir.resolve()):
            raise HTTPException(404, "Файл видео не найден")
        if not 4 <= count <= 16:
            raise HTTPException(422, "Кадров: от 4 до 16")
        try:
            probe = await asyncio.to_thread(subprocess.run,
                [shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "json", str(path)], capture_output=True, timeout=15, check=False)
            if probe.returncode:
                raise ValueError("Не удалось прочитать длительность MP4")
            duration = float(json.loads(probe.stdout)["format"]["duration"])
            sheet, times = await asyncio.to_thread(contact_sheet, path, count, 600, start_s, end_s)
            cuts = await asyncio.to_thread(scene_change_times, path, duration)
            sound = await asyncio.to_thread(has_audio, path)
        except (ValueError, OSError, subprocess.TimeoutExpired, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(422, f"Не удалось подготовить кадры: {exc}") from exc
        return {"asset_id": asset_id, "name": asset.get("original_name") or "", "duration_s": round(duration, 2),
                "reviewed_range_s": [start_s, min(end_s, duration) if end_s is not None else duration],
                "has_sound": sound, "frame_times_s": times,
                "hard_cuts_s": [round(time, 2) for time in cuts if start_s <= time <= (end_s or duration)],
                "sheet_jpeg_base64": base64.b64encode(sheet).decode("ascii")}

    @router.get("/api/jobs/{job_id}/frames")
    async def job_frames(job_id: str, count: int = 8) -> dict:
        """What the Studio agent reviews a finished shot from (MCP job_frames): a contact sheet, the full
        decode/audio check and the hard cuts found in the clip. No GPU, no Qwen."""
        job = store.get_job(job_id)
        if not job or job["state"] != "completed" or not job.get("output_path"):
            raise HTTPException(409, "Шот ещё не готов")
        path = Path(job["output_path"])
        if not path.is_file() or not path.resolve().is_relative_to(store.results_dir.resolve()):
            raise HTTPException(404, "MP4 шота не найден")
        if not 4 <= count <= 12:
            raise HTTPException(422, "Кадров: от 4 до 12")
        try:
            technical = await asyncio.to_thread(technical_scan, path)
            sheet, times = await asyncio.to_thread(contact_sheet, path, count)
            cuts = await asyncio.to_thread(scene_change_times, path, technical["duration"])
        except (ValueError, OSError, subprocess.TimeoutExpired, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(422, f"Не удалось подготовить кадры: {exc}") from exc
        return {"job_id": job_id, "technical": technical, "frame_times_s": times,
                "hard_cuts_s": [round(time, 2) for time in cuts], "sheet_jpeg_base64": base64.b64encode(sheet).decode("ascii")}

    return router
