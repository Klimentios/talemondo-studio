"""Bounded visual evidence for the existing Qwen VL scene planner."""
from __future__ import annotations

import base64
from pathlib import Path
import re
import shutil
import subprocess

from .store import _media_duration_seconds


def video_evidence(path: Path, seconds: int, budget: int, number: int) -> tuple[list[dict], list[float]]:
    duration = _media_duration_seconds(path)
    if duration is None or duration + 1 / 24 < seconds:
        raise ValueError('План выходит за длительность исходного видео')
    ffmpeg = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
    detection = subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-i', str(path), '-t', str(seconds),
        '-an', '-vf', 'scale=320:-2,scdet=threshold=10', '-f', 'null', '-'],
        capture_output=True, text=True, timeout=120, check=False)
    if detection.returncode:
        raise ValueError('Не удалось определить смены кадров исходного видео')
    cuts = sorted({float(value) for value in re.findall(r'lavfi.scd.time: ([0-9.]+)', detection.stderr)
                   if 0 < float(value) < seconds})
    # Uniform coverage plus frames near detected cuts; all timestamps are explicit.
    moments = {round(i * (seconds - 0.1) / (max(2, budget // 2) - 1), 3) for i in range(max(2, budget // 2))}
    for moment in cuts:
        if len(moments) + 2 > budget:
            break
        moments.update((max(0, moment - 0.1), min(seconds - 0.05, moment + 0.1)))
    for i in range(budget):
        if len(moments) >= budget:
            break
        moments.add(round(i * (seconds - 0.1) / (budget - 1), 3))
    frames = []
    for moment in sorted(moments):
        result = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-ss', str(moment),
            '-i', str(path), '-frames:v', '1', '-vf', "scale='min(512,iw)':-2", '-q:v', '5',
            '-f', 'image2pipe', '-vcodec', 'mjpeg', '-'], capture_output=True, timeout=25, check=False)
        if result.returncode or not result.stdout.startswith(b'\xff\xd8') or len(result.stdout) > 2_000_000:
            raise ValueError('Не удалось извлечь кадр исходного видео')
        frames.append({'video_number':number, 'seconds':moment,
                       'jpeg_base64':base64.b64encode(result.stdout).decode('ascii')})
    return frames, cuts


def prepare_evidence(store, payload) -> dict:
    body = payload.model_dump()
    body['source_frames'] = []
    body['source_cuts'] = {}
    videos = [ref for ref in payload.references if ref.kind == 'video' and ref.role != 'Продолжение']
    if not videos:
        return body
    if len(videos) > 3:
        raise ValueError('План поддерживает до трёх видеореференсов')
    for index, ref in enumerate(videos):
        asset = store.get_asset(ref.asset_id) if ref.asset_id else None
        if not asset or asset['kind'] != 'video':
            raise ValueError('Видеореференс плана не найден')
        frames, cuts = video_evidence(Path(asset['path']), payload.target_seconds, 24 // len(videos), ref.number or index + 1)
        body['source_frames'].extend(frames)
        body['source_cuts'][str(ref.number or index + 1)] = cuts
    return body
