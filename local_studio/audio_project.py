"""Persistent editing draft for the future audio and lip-sync pipeline.

No GPU job is submitted from this module. A later executor must validate this
contract again against the actual selected TTS and lip-sync capabilities.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .store import StudioStore


class AudioDraftInput(BaseModel):
    project: dict


def validate_audio_project(project: dict, store: StudioStore) -> dict:
    if not isinstance(project, dict) or project.get("version") != 1:
        raise ValueError("Неподдерживаемый проект звука")
    if project.get("mode") not in {"video", "audio"}:
        raise ValueError("Выбери работу с видео или только со звуком")
    try:
        duration = float(project.get("duration"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Неверная длительность проекта") from exc
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError("Длительность проекта вне диапазона")
    if project.get("output_format") not in {"wav", "mp3"}:
        raise ValueError("Неверный формат аудио")
    title = str(project.get("title", "")).strip()
    if not title or len(title) > 120:
        raise ValueError("Название проекта: от 1 до 120 символов")
    source = project.get("source") or {}
    if not isinstance(source, dict) or source.get("kind") not in {"none", "job", "asset"}:
        raise ValueError("Некорректный источник видео")
    if source.get("kind") == "job":
        job = store.get_job(str(source.get("id", "")))
        if not job or job["state"] != "completed" or not job.get("output_path") or not Path(job["output_path"]).is_file():
            raise ValueError("Исходный ролик недоступен")
    elif source.get("kind") == "asset":
        asset = store.get_asset(str(source.get("id", "")))
        if not asset or asset["kind"] != "video" or not Path(asset["path"]).is_file():
            raise ValueError("Исходный видеофайл недоступен")
    lines = project.get("lines")
    if not isinstance(lines, list) or len(lines) > 40:
        raise ValueError("В проекте может быть до 40 реплик")
    for number, line in enumerate(lines, 1):
        if not isinstance(line, dict):
            raise ValueError(f"Реплика {number} повреждена")
        if len(str(line.get("text", ""))) > 1200 or len(str(line.get("speaker", ""))) > 80:
            raise ValueError(f"Реплика {number} слишком длинная")
        if len(str(line.get("direction", ""))) > 160 or len(str(line.get("voice_description", ""))) > 200:
            raise ValueError(f"Реплика {number}: описание слишком длинное")
        if line.get("voice_mode") not in {"preset", "reference"}:
            raise ValueError(f"Реплика {number}: выбери источник голоса")
        if line.get("voice_mode") == "reference" and line.get("voice_asset_id"):
            asset = store.get_asset(str(line.get("voice_asset_id", "")))
            if not asset or asset["kind"] != "audio" or not Path(asset["path"]).is_file():
                raise ValueError(f"Реплика {number}: голосовой образец недоступен")
        try:
            start = float(line.get("start", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Реплика {number}: неверное время") from exc
        if not math.isfinite(start) or not 0 <= start <= 3600:
            raise ValueError(f"Реплика {number}: время вне диапазона")
    mix = project.get("mix")
    if not isinstance(mix, dict):
        raise ValueError("Настройки сведения отсутствуют")
    for key in ("source_level", "speech_level", "music_level", "effects_level"):
        try:
            value = float(mix.get(key))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Некорректный уровень {key}") from exc
        if not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError(f"Уровень {key} вне диапазона")
    for key in ("music_prompt", "effects_prompt"):
        if len(str(mix.get(key, ""))) > 500:
            raise ValueError(f"Описание {key} слишком длинное")
    if len(str(mix.get("music_lyrics", ""))) > 4096:
        raise ValueError("Текст песни слишком длинный")
    bpm = mix.get("music_bpm")
    if bpm not in (None, "") and (isinstance(bpm, bool) or not isinstance(bpm, int) or not 30 <= bpm <= 300):
        raise ValueError("Темп музыки должен быть от 30 до 300 BPM")
    if not re.fullmatch(r"[A-G](?:#|b)?(?: ?(?:Major|minor)|m)?", str(mix.get("music_keyscale") or "")) and mix.get("music_keyscale"):
        raise ValueError("Неверная тональность музыки")
    if str(mix.get("music_timesignature") or "") not in {"", "2", "3", "4", "6"}:
        raise ValueError("Неверный музыкальный размер")
    seed = mix.get("music_seed", -1)
    if isinstance(seed, bool) or not isinstance(seed, int) or not -1 <= seed <= 2147483647:
        raise ValueError("Seed музыки вне диапазона")
    # A draft may contain an unfinished song. The actual job validates its
    # selected layers and duration before any media reaches the GPU.
    for key in ("music_asset_id", "effects_asset_id"):
        if mix.get(key):
            asset = store.get_asset(str(mix[key]))
            if not asset or asset["kind"] != "audio" or not Path(asset["path"]).is_file():
                raise ValueError(f"Аудиофайл {key} недоступен")
    if not isinstance(mix.get("ducking"), bool) or not isinstance(project.get("lip_sync"), bool):
        raise ValueError("Некорректные переключатели звука")
    if project["mode"] == "audio" and project["lip_sync"]:
        raise ValueError("Синхронизация губ доступна только для видео")
    if len(json.dumps(project, ensure_ascii=False).encode()) > 64 * 1024:
        raise ValueError("Проект превышает 64 КБ")
    return project


def make_router(store: StudioStore, gpu_url: str = "") -> APIRouter:
    router = APIRouter(prefix="/api/audio", tags=["audio-project"])

    @router.get("/draft")
    async def get_draft() -> dict:
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS audio_draft (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            row = db.execute("SELECT payload FROM audio_draft WHERE id=1").fetchone()
        return {"project": json.loads(row["payload"]) if row else None}

    @router.put("/draft")
    async def save_draft(payload: AudioDraftInput) -> dict:
        try:
            project = validate_audio_project(payload.project, store)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS audio_draft (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            db.execute("INSERT INTO audio_draft(id,payload) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                       (json.dumps(project, ensure_ascii=False),))
        return {"saved": True}

    @router.get("/capabilities")
    async def capabilities() -> dict:
        unavailable = {"tts": False, "lip_sync": False, "audio_mix": False,
                       "music_generation": False, "music_controls": False, "effects_generation": False,
                       "reason": "GPU-сервер недоступен или аудиомодели ещё не установлены"}
        if not gpu_url:
            return unavailable
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{gpu_url}/api/audio/capabilities")
                response.raise_for_status()
                payload = response.json()
            return {key: payload.get(key) is True for key in
                    ("tts", "lip_sync", "audio_mix", "music_generation", "music_controls", "effects_generation")}
        except (httpx.HTTPError, ValueError):
            return unavailable

    return router
