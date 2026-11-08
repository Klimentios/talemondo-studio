"""Private GPU-side LivePortrait preparation, isolated from the H3 Python runtime."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import tempfile

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .acting_core import capability, cache_key, gpu_admission_dependency, runtime_paths
from .qwen_lifecycle import GpuBusy, exclusive_gpu_task


router = APIRouter(prefix="/api/acting", tags=["acting"])
MAX_IMAGE = 25 * 1024 * 1024
MAX_VIDEO = 200 * 1024 * 1024


def _ffprobe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=width,height,duration:format=duration",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=15, check=True)
    import json
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    duration = float(stream.get("duration") or payload.get("format", {}).get("duration") or 0)
    if not 0 < duration <= 30 or not 1 <= int(stream.get("width", 0)) <= 4096 or not 1 <= int(stream.get("height", 0)) <= 4096:
        raise ValueError("Видеообразец должен быть до 30 секунд и до 4096×4096")
    return {"duration": duration, "width": int(stream["width"]), "height": int(stream["height"])}


def _run(source: bytes, driving: bytes, mode: str, key: str) -> Path:
    root, python = runtime_paths()
    cache = Path(os.environ.get("H3_ACTING_CACHE", "/workspace/h3/acting-cache"))
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = cache / f"{key}.mp4"
    if target.is_file() and target.stat().st_size > 0:
        return target
    with tempfile.TemporaryDirectory(prefix="acting-", dir=cache) as temporary:
        work = Path(temporary)
        image = work / "source.jpg"
        image.write_bytes(source)
        video = work / "driving.mp4"
        video.write_bytes(driving)
        _ffprobe(video)
        # The upload is validated as image/video, so upstream's pickle input
        # path is unreachable. No shell and no untrusted filename in argv.
        script = Path(__file__).with_name("acting_runner.py")
        command = [str(python), str(script), "--root", str(root), "--mode", mode,
                   "--source", str(image), "--driving", str(video), "--output-dir", str(work / "output")]
        # One model on the GPU at a time: H3 unloaded, no render or Qwen wake until LivePortrait exits.
        with exclusive_gpu_task():
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=900)
        if result.returncode:
            raise RuntimeError(f"LivePortrait завершился с кодом {result.returncode}: {result.stderr[-600:]}")
        original = work / "output" / "source--driving.mp4"
        if not original.is_file():
            raise RuntimeError("LivePortrait не создал ожидаемый MP4")
        silent = work / "silent.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(original),
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                        "-b:a", "64k", "-shortest", str(silent)], check=True, timeout=90)
        _ffprobe(silent)
        os.replace(silent, target)
    return target


@router.get("/capabilities")
async def acting_capabilities() -> dict:
    return capability()


@router.post("/prepare")
async def prepare_acting(
    mode: str = Form(...), source: UploadFile = File(...), driving: UploadFile = File(...),
    _admission: None = Depends(gpu_admission_dependency),
) -> FileResponse:
    if mode not in {"human", "animal"}:
        raise HTTPException(422, "Выбери человека или животное")
    if not capability()[mode]:
        raise HTTPException(503, f"LivePortrait {mode} не установлен или smoke test не пройден")
    image = await source.read(MAX_IMAGE + 1)
    video = await driving.read(MAX_VIDEO + 1)
    if len(image) > MAX_IMAGE or len(video) > MAX_VIDEO:
        raise HTTPException(413, "Фото или видеообразец слишком большие")
    if not (image.startswith(b"\xff\xd8\xff") or image.startswith(b"\x89PNG\r\n\x1a\n") or image.startswith(b"RIFF") and image[8:12] == b"WEBP"):
        raise HTTPException(422, "Нужно фото JPEG, PNG или WebP")
    if not (len(video) >= 12 and video[4:8] == b"ftyp"):
        raise HTTPException(422, "Нужен MP4 или MOV видеообразец")
    root, _ = runtime_paths()
    version = (root / ".h3-acting-smoke-ok").read_text().strip()
    key = cache_key(image, video, mode, {"stitching": mode == "human", "audio": False}, version)
    cache = Path(os.environ.get("H3_ACTING_CACHE", "/workspace/h3/acting-cache"))
    target = cache / f"{key}.mp4"
    if target.is_file() and target.stat().st_size > 0:
        return FileResponse(target, media_type="video/mp4", filename="acting-reference.mp4")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{os.environ.get('H3_COMFY_URL', 'http://127.0.0.1:18188').rstrip('/')}/queue")
            response.raise_for_status()
            queue = response.json()
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise HTTPException(409, "H3 сейчас занят; подготовка актёрской игры доступна после очереди")
        path = await asyncio.to_thread(_run, image, video, mode, key)
        return FileResponse(path, media_type="video/mp4", filename="acting-reference.mp4")
    except HTTPException:
        raise
    except GpuBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except (httpx.HTTPError, ValueError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
        raise HTTPException(502, f"Подготовка актёрской игры не выполнена: {str(exc)[:500]}") from exc
