"""Immutable source-video excerpts for successive storyboard shots."""
from __future__ import annotations

import asyncio
import hashlib
import fcntl
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .store import StudioStore, _media_duration_seconds


class SegmentRequest(BaseModel):
    start: float = Field(ge=0, le=180, allow_inf_nan=False)
    duration: int = Field(ge=4, le=15)
    # None keeps the source size (ComfyUI then encodes it at a 768 short edge). 480 shrinks the excerpt so
    # Ref2VA takes it as is: ~0.46x attention in a 10 s swap (app/h3_budget.py). A/B option, 04.11.2026.
    short_edge: int | None = Field(default=None, ge=384, le=768)


def segment_filter(short_edge: int | None) -> str:
    if not short_edge:
        return 'fps=24'
    # shorter side to short_edge (never upscaled), even sides for yuv420p
    return (f"fps=24,scale='if(gt(iw,ih),-2,min({short_edge},iw))':'if(gt(iw,ih),min({short_edge},ih),-2)'"
            ":flags=lanczos")


def file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def reference_segment(store: StudioStore, asset_id: str, request: SegmentRequest) -> dict:
    # Both the public endpoint and production executor use the same process-safe lock.
    with (store.assets_dir / '.reference-segments.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _reference_segment(store, asset_id, request)


def _reference_segment(store: StudioStore, asset_id: str, request: SegmentRequest) -> dict:
    asset = store.get_asset(asset_id)
    if not asset or asset['kind'] != 'video':
        raise ValueError('Исходное видео не найдено')
    source = Path(asset['path'])
    if not source.is_file() or not source.resolve().is_relative_to(store.assets_dir.resolve()):
        raise ValueError('Исходное видео отсутствует в медиатеке')
    if file_hash(source) != asset['sha256']:
        raise ValueError('Исходное видео изменилось; добавь его в медиатеку заново')
    duration = _media_duration_seconds(source)
    end = request.start + request.duration
    # A whole-second part of a 9.96 s source (H3 takes whole seconds) ends a frame or two past it: the excerpt
    # is then that much shorter, inside the 0.15 s check of its real length below.
    if duration is None or end > duration + 0.1:
        raise ValueError(f'Участок {request.start:g}–{end:g} с выходит за длительность исходного видео')
    size_tag = f":se{request.short_edge}" if request.short_edge else ""  # old excerpts keep their cache keys
    key = hashlib.sha256(f"{asset['sha256']}:{request.start:.6f}:{request.duration}:v1{size_tag}".encode()).hexdigest()
    target = store.assets_dir / f'scene-segment-{key}.mp4'
    with store.connect() as db:
        previous = db.execute('SELECT * FROM assets WHERE path=?', (str(target),)).fetchone()
        if previous and target.is_file():
            if not target.resolve().is_relative_to(store.assets_dir.resolve()) or file_hash(target) != previous['sha256']:
                raise ValueError('Сохранённый участок изменился; кэш не используется')
            return dict(previous)
    ffmpeg = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
    with tempfile.TemporaryDirectory(dir=store.assets_dir, prefix='.segment-') as temporary:
        output = Path(temporary) / 'segment.mp4'
        result = subprocess.run([
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-ss', str(request.start), '-i', str(source), '-t', str(request.duration),
            '-map', '0:v:0', '-map', '0:a:0?', '-vf', segment_filter(request.short_edge),
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output),
        ], capture_output=True, text=True, timeout=180, check=False)
        actual = _media_duration_seconds(output) if result.returncode == 0 else None
        if actual is None or abs(actual - request.duration) > 0.15:
            raise ValueError('Не удалось подготовить полный участок видео для шота')
        digest = file_hash(output)
        if previous and digest != previous['sha256']:
            raise ValueError('Восстановленный участок отличается от сохранённой версии')
        os.replace(output, target)
    if previous:
        return dict(previous)
    digest = file_hash(target)
    return store.add_asset(kind='video', role=asset['role'],
                           original_name=f"{asset['original_name']} · {request.start:g}–{end:g} с"
                                         f"{f' · {request.short_edge}p' if request.short_edge else ''}.mp4",
                           mime='video/mp4', size=target.stat().st_size, sha256=digest, path=target)


def make_router(store: StudioStore) -> APIRouter:
    router = APIRouter()
    lock = asyncio.Lock()

    @router.post('/api/assets/{asset_id}/segment')
    async def segment(asset_id: str, payload: SegmentRequest) -> dict:
        try:
            async with lock:
                return await asyncio.to_thread(reference_segment, store, asset_id, payload)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
