"""Scene continuity: the last frame of a finished clip becomes the next clip's «Кадр сцены»."""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .store import StudioStore


DARK_FRAME = 10  # mean luma of 0–255; a night interior stays well above it, a fade to black is near 0


def last_frame_png(video: Path, output: Path) -> None:
    """Exact last decoded frame, lossless PNG at the clip's own resolution."""
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    # -sseof seeks near the end; -update 1 keeps overwriting so the file holds the final frame.
    result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-sseof", "-1", "-i", str(video),
                             "-update", "1", "-frames:v", "1000", str(output)],
                            capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise ValueError(f"Не удалось взять последний кадр: {result.stderr[:300]}")
    from PIL import Image, ImageStat
    with Image.open(output) as frame:
        brightness = ImageStat.Stat(frame.convert("L")).mean[0]
    if brightness < DARK_FRAME:
        # The next shot starts from this picture: a fade to black would open it in the dark.
        raise ValueError(f"Последний кадр почти чёрный (яркость {brightness:.0f} из 255): дубль кончается затемнением, "
                         "следующий шот начнётся в темноте. Переснимите дубль без затемнения в конце или начните "
                         "следующий шот с нового кадра")


def make_router(store: StudioStore) -> APIRouter:
    router = APIRouter()

    @router.post("/api/jobs/{job_id}/last-frame", status_code=201)
    async def last_frame(job_id: str) -> dict:
        job = store.get_job(job_id)
        if not job or job["state"] != "completed" or not job.get("output_path"):
            raise HTTPException(404, "Ролик ещё не готов")
        video = Path(job["output_path"])
        if not video.is_file():
            raise HTTPException(404, "MP4 не найден")
        temporary = store.assets_dir / f".scene-frame-{job_id}.{os.getpid()}.png"
        try:
            try:
                await asyncio.to_thread(last_frame_png, video, temporary)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            data = await asyncio.to_thread(temporary.read_bytes)
            sha256 = hashlib.sha256(data).hexdigest()
            # The same clip gives the same frame: reuse its asset instead of a new one per click.
            existing = store.find_asset(sha256=sha256, kind="image", role="Кадр сцены")
            if existing:
                return existing
            folder = store.assets_dir / sha256[:2]
            folder.mkdir(exist_ok=True)
            target = folder / sha256
            if not target.exists():
                os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return store.add_asset(kind="image", role="Кадр сцены", original_name=f"Последний кадр {job_id[:8]}.png",
                               mime="image/png", size=len(data), sha256=sha256, path=target)

    return router
