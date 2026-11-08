"""Exact, cached video/audio excerpts prepared on Mac before GPU upload."""
import asyncio
import fcntl
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .reference_segments import file_hash
from .store import _media_duration_seconds


class ExcerptRequest(BaseModel):
    start: float = Field(default=0, ge=0, allow_inf_nan=False)
    duration: float = Field(gt=0, le=15, allow_inf_nan=False)


def media_source(store, uid):
    asset = store.get_asset(uid)
    if not asset or asset['kind'] not in {'video', 'audio'}:
        raise ValueError('Нужен видео- или аудиореференс из библиотеки')
    path = Path(asset['path'])
    if not path.is_file() or not path.resolve().is_relative_to(store.assets_dir.resolve()):
        raise ValueError('Референс отсутствует в библиотеке')
    duration = _media_duration_seconds(path)
    if not duration or duration <= 0:
        raise ValueError('Не удалось прочитать длительность референса')
    return asset, path, duration


def prepare_excerpt(store, uid, request):
    with (store.assets_dir / '.reference-excerpts.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        asset, source, total = media_source(store, uid)
        if file_hash(source) != asset['sha256']:
            raise ValueError('Исходник изменился: добавь его заново')
        if request.start >= total - 0.02:
            raise ValueError('Начало участка выходит за длительность референса')
        length = min(request.duration, total - request.start)
        if request.start == 0 and total <= request.duration + 0.02:
            return {**asset, 'excerpt': {'start': 0, 'duration': total, 'source_asset_id': uid}}
        key = hashlib.sha256(f"{asset['sha256']}:{request.start:.6f}:{length:.6f}:excerpt-1".encode()).hexdigest()
        video = asset['kind'] == 'video'
        target = store.assets_dir / ('reference-excerpt-' + key + ('.mp4' if video else '.wav'))
        with store.connect() as db:
            previous = db.execute('SELECT * FROM assets WHERE path=?', (str(target),)).fetchone()
        metadata = {'start': request.start, 'duration': length, 'source_asset_id': uid}
        if previous and target.is_file():
            if file_hash(target) != previous['sha256']:
                raise ValueError('Подготовленный участок изменился; кэш не используется')
            return {**dict(previous), 'excerpt': metadata}
        ffmpeg = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
        with tempfile.TemporaryDirectory(dir=store.assets_dir, prefix='.excerpt-') as temporary:
            output = Path(temporary) / target.name
            command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                       '-ss', str(request.start), '-i', str(source), '-t', str(length)]
            command += (['-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'veryfast',
                         '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
                         '-movflags', '+faststart'] if video else ['-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le'])
            result = subprocess.run(command + [str(output)], capture_output=True, timeout=180)
            actual = _media_duration_seconds(output) if result.returncode == 0 else None
            if actual is None or abs(actual - length) > 0.15:
                raise ValueError('Не удалось подготовить выбранный участок')
            if previous and file_hash(output) != previous['sha256']:
                raise ValueError('Восстановленный участок отличается от сохранённого')
            os.replace(output, target)
        clipped = dict(previous) if previous else store.add_asset(
            kind=asset['kind'], role=asset['role'], original_name=f"{asset['original_name']} · {request.start:g}–{request.start + length:g} с",
            mime='video/mp4' if video else 'audio/wav', size=target.stat().st_size,
            sha256=file_hash(target), path=target)
        return {**clipped, 'excerpt': metadata}


def make_router(store):
    router = APIRouter()

    @router.get('/api/assets/{uid}/excerpt-info')
    async def info(uid: str):
        try:
            asset, _, duration = await asyncio.to_thread(media_source, store, uid)
            return {'asset_id': uid, 'kind': asset['kind'], 'duration': duration}
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post('/api/assets/{uid}/excerpt')
    async def excerpt(uid: str, request: ExcerptRequest):
        try:
            return await asyncio.to_thread(prepare_excerpt, store, uid, request)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
