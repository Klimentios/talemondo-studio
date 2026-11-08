"""Server-side post-production jobs, isolated from H3 and SeedVR2 graphs."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .audio_mix import extract_original_audio, has_audio, mix_project, probe
from .audio_models import capabilities, generate_music, synchronize_lips, synthesize_line
from .qwen_lifecycle import queue_active, render_submission


MAX_VIDEO = 500 * 1024 * 1024
MAX_AUDIO = 100 * 1024 * 1024
MAX_TOTAL = 800 * 1024 * 1024
ACTIVE = {"accepted", "running", "needs_reconciliation"}


def validate_render(project: dict, available: dict) -> None:
    if not isinstance(project, dict) or project.get("version") != 1 or project.get("mode") not in {"video", "audio"}:
        raise ValueError("Неверный проект звука")
    if not available["audio_mix"]:
        raise ValueError("FFmpeg недоступен на GPU-сервере")
    lines = project.get("lines")
    mix = project.get("mix")
    if not isinstance(lines, list) or len(lines) > 40 or not isinstance(mix, dict):
        raise ValueError("Неверные реплики или звуковые слои")
    try:
        duration = float(project.get("duration"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Неверная длительность") from exc
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError("Длительность вне диапазона")
    if project["mode"] == "audio" and project.get("output_format") not in {"wav", "mp3"}:
        raise ValueError("Неверный формат аудио")
    for key in ("source_level", "speech_level", "music_level", "effects_level"):
        try:
            level = float(mix[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Неверный уровень {key}") from exc
        if not math.isfinite(level) or not 0 <= level <= 2:
            raise ValueError(f"Уровень {key} вне диапазона")
    if not isinstance(mix.get("ducking"), bool):
        raise ValueError("Неверная настройка приглушения музыки")
    for index, line in enumerate(lines, 1):
        if not isinstance(line, dict) or len(str(line.get("text", ""))) > 1200 or len(str(line.get("direction", ""))) > 160 or len(str(line.get("voice_description", ""))) > 200:
            raise ValueError(f"Реплика {index} повреждена или слишком длинная")
        if line.get("voice_mode") not in {"preset", "reference"}:
            raise ValueError(f"Реплика {index}: неверный источник голоса")
        try:
            start = float(line.get("start"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Реплика {index}: неверное время") from exc
        if not math.isfinite(start) or not 0 <= start < duration:
            raise ValueError(f"Реплика {index} выходит за пределы проекта")
        if line["voice_mode"] == "reference" and not line.get("voice_asset_id"):
            raise ValueError(f"Реплика {index}: прикрепи образец голоса")
    source = project.get("source")
    if not isinstance(source, dict) or source.get("kind") not in {"none", "job", "asset"}:
        raise ValueError("Неверный источник видео")
    if project["mode"] == "video" and source.get("kind") == "none":
        raise ValueError("Выбери исходное видео")
    if project["mode"] == "audio" and project.get("lip_sync"):
        raise ValueError("Синхронизация губ требует видео")
    if project.get("lip_sync") and not available["lip_sync"]:
        raise ValueError("Модель синхронизации губ не установлена")
    if project.get("lip_sync") and not str(project.get("lip_sync_target", "")).strip():
        raise ValueError("Укажи говорящего в кадре")
    if project.get("lip_sync") and not any(isinstance(line, dict) and str(line.get("text", "")).strip() for line in lines):
        raise ValueError("Для синхронизации губ нужна реплика")
    if any(str(line.get("text", "")).strip() for line in lines) and not available["tts"]:
        raise ValueError("Модель синтеза речи не установлена")
    if mix.get("music_prompt") and not available["music_generation"]:
        raise ValueError("Генерация музыки по описанию ещё не установлена")
    custom_music = (mix.get("music_bpm") not in (None, "") or bool(mix.get("music_keyscale"))
                    or bool(mix.get("music_timesignature")) or mix.get("music_seed", -1) != -1)
    if mix.get("music_prompt") and custom_music and not available.get("music_controls"):
        raise ValueError("Настройки музыки ещё не установлены на GPU")
    if mix.get("music_prompt") and duration < 10:
        raise ValueError("Для создания музыки нужна длительность от 10 секунд")
    if mix.get("music_prompt") and duration > 600:
        raise ValueError("Одна музыкальная дорожка может длиться до 600 секунд")
    if mix.get("music_prompt") and mix.get("music_asset_id"):
        raise ValueError("Выбери готовую музыку или создание по описанию")
    if len(str(mix.get("music_prompt", ""))) > 500:
        raise ValueError("Описание музыки слишком длинное")
    if len(str(mix.get("music_lyrics", ""))) > 4096:
        raise ValueError("Текст песни слишком длинный")
    if str(mix.get("music_lyrics", "")).strip() and not str(mix.get("music_prompt", "")).strip():
        raise ValueError("Опиши стиль музыки для текста песни")
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
    if mix.get("effects_prompt") and not available["effects_generation"]:
        raise ValueError("Генерация эффектов по описанию ещё не установлена")
    if not any((any(str(line.get("text", "")).strip() for line in lines),
                mix.get("music_asset_id"), mix.get("music_prompt"), mix.get("effects_asset_id"),
                project["mode"] == "video" and float(mix.get("source_level", 0)) > 0)):
        raise ValueError("Добавь речь, музыку, эффект или исходный звук")


def _render(project: dict, media: dict[str, Path], folder: Path) -> Path:
    video = media.get("source") if project["mode"] == "video" else None
    if project["mode"] == "video" and not video:
        raise ValueError("Исходное видео не загружено")
    if video and not any(s.get("codec_type") == "video" for s in probe(video).get("streams", [])):
        raise ValueError("Исходный видеофайл не читается")
    duration = float(project["duration"])
    if not 0 < duration <= 3600:
        raise ValueError("Длительность вне диапазона")
    if video:
        source_duration = float(probe(video).get("format", {}).get("duration") or 0)
        if not 0 < source_duration or duration > source_duration + 1:
            raise ValueError("Длительность проекта превышает длительность исходного ролика")
        duration = min(duration, source_duration)
    original = extract_original_audio(video, folder / "original-audio.wav") if video else None
    speech: list[tuple[Path, float]] = []
    for index, line in enumerate(project["lines"]):
        text = str(line.get("text", "")).strip()
        if not text:
            continue
        if len(text) > 1200 or len(str(line.get("direction", ""))) > 160:
            raise ValueError("Реплика слишком длинная")
        reference = media.get(f"voice:{line.get('voice_asset_id')}") if line.get("voice_mode") == "reference" else None
        if line.get("voice_mode") == "reference" and not reference:
            raise ValueError("Голосовой референс не загружен")
        voice_description = str(line.get("voice_description", "")).strip()
        delivery = str(line.get("direction", "")).strip()
        instruction = ". ".join(part for part in (voice_description, delivery) if part)
        output = synthesize_line(text=text, direction=instruction,
                                 reference=reference, output=folder / f"speech-{index:02d}.wav")
        speech.append((output, float(line["start"])))
    mix = project["mix"]
    music = media.get("music") if mix.get("music_asset_id") else None
    if mix.get("music_prompt"):
        music = generate_music(prompt=str(mix["music_prompt"]).strip(), duration=duration,
                               lyrics=str(mix.get("music_lyrics", "")).strip(),
                               bpm=mix.get("music_bpm"), keyscale=str(mix.get("music_keyscale") or ""),
                               timesignature=str(mix.get("music_timesignature") or ""),
                               seed=mix.get("music_seed", -1),
                               output=folder / "generated-music.wav")
    effects = media.get("effects") if mix.get("effects_asset_id") else None
    if mix.get("music_asset_id") and not music or mix.get("effects_asset_id") and not effects:
        raise ValueError("Один из звуковых слоёв не загружен")
    if project.get("lip_sync"):
        voice_track = mix_project(destination=folder / "speech-for-lips.wav", duration=duration,
                                  output_format="wav", speech=speech, speech_level=1)
        video = synchronize_lips(video=video, audio=voice_track, output=folder / "lip-synced.mp4")
    output_format = "mp4" if project["mode"] == "video" else project.get("output_format", "wav")
    return mix_project(destination=folder / f"result.{output_format}", duration=duration,
                       output_format=output_format, video=video, original_audio=original,
                       speech=speech, music=music, effects=effects,
                       source_level=mix["source_level"], speech_level=mix["speech_level"],
                       music_level=mix["music_level"], effects_level=mix["effects_level"],
                       ducking=mix["ducking"])


def _render_on_gpu(project: dict, media: dict[str, Path], folder: Path, comfy) -> Path:
    """Wait for queued video work, then own the GPU until audio models exit."""
    with render_submission():
        deadline = time.monotonic() + 6 * 3600
        while queue_active():
            if time.monotonic() >= deadline:
                raise TimeoutError("Ожидание видеоочереди превысило 6 часов")
            time.sleep(5)
        asyncio.run(comfy.release_models_if_idle())
        return _render(project, media, folder)


class AudioJobs:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "audio-jobs.sqlite3"
        self.lock = asyncio.Lock()
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, state TEXT NOT NULL,
                output_path TEXT, error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("UPDATE jobs SET state='needs_reconciliation', error='Сервис перезапущен во время выполнения' WHERE state IN ('accepted','running')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, uid: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (uid,)).fetchone()
            return dict(row) if row else None

    def accept(self, uid: str, fingerprint: str) -> tuple[bool, dict]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (uid,)).fetchone()
            if row:
                if row["request_hash"] != fingerprint:
                    raise ValueError("client_job_id уже использован с другим проектом")
                return False, dict(row)
            db.execute("INSERT INTO jobs(id,request_hash,state) VALUES (?,?,'accepted')", (uid, fingerprint))
        return True, self.get(uid)

    def update(self, uid: str, state: str, *, output_path: str | None = None, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute("UPDATE jobs SET state=?, output_path=COALESCE(?,output_path), error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (state, output_path, error, uid))

    async def execute(self, uid: str, project: dict, media: dict[str, Path], comfy) -> None:
        async with self.lock:
            self.update(uid, "running")
            folder = self.root / uid
            try:
                needs_model = bool(any(str(line.get("text", "")).strip() for line in project["lines"]) or project.get("lip_sync")
                                   or project["mix"].get("music_prompt"))
                if needs_model:
                    output = await asyncio.to_thread(_render_on_gpu, project, media, folder, comfy)
                else:
                    output = await asyncio.to_thread(_render, project, media, folder)
                self.update(uid, "success", output_path=str(output), error="")
            except Exception as exc:
                self.update(uid, "error", error=str(exc)[:700])


def make_router(comfy) -> APIRouter:
    router = APIRouter(prefix="/api/audio", tags=["audio"])
    jobs: AudioJobs | None = None

    def service() -> AudioJobs:
        nonlocal jobs
        if jobs is None:
            jobs = AudioJobs(Path(os.environ.get("H3_AUDIO_JOBS_DIR", str(Path.home() / ".local/state/h3-studio/audio"))))
        return jobs

    @router.get("/capabilities")
    async def get_capabilities() -> dict:
        return capabilities()

    @router.post("/jobs", status_code=202)
    async def submit(background: BackgroundTasks, client_job_id: str = Form(...),
                     project_json: str = Form(...), media_ids_json: str = Form("[]"),
                     media: list[UploadFile] = File(default=[])) -> dict:
        try:
            uuid.UUID(client_job_id)
            project = json.loads(project_json)
            media_ids = json.loads(media_ids_json)
            validate_render(project, capabilities())
            if not isinstance(media_ids, list) or len(media_ids) != len(media) or len(media_ids) > 45:
                raise ValueError("Неверный список медиафайлов")
            if len(set(media_ids)) != len(media_ids) or any(not isinstance(i, str) or not (i in {"source", "music", "effects"} or i.startswith("voice:")) for i in media_ids):
                raise ValueError("Неверные идентификаторы медиафайлов")
            expected = set()
            if project["mode"] == "video":
                expected.add("source")
            if project["mix"].get("music_asset_id"):
                expected.add("music")
            if project["mix"].get("effects_asset_id"):
                expected.add("effects")
            expected.update(f"voice:{line['voice_asset_id']}" for line in project["lines"]
                            if line["voice_mode"] == "reference")
            if set(media_ids) != expected:
                raise ValueError("Файлы не соответствуют звуковому проекту")
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        folder = service().root / client_job_id
        if service().get(client_job_id):
            raise HTTPException(409, "Задание уже принято; проверь /by-client вместо повторной отправки")
        folder.mkdir(mode=0o700, exist_ok=True)
        media_paths: dict[str, Path] = {}
        digest = hashlib.sha256(project_json.encode())
        total = 0
        for index, (key, upload) in enumerate(zip(media_ids, media)):
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in {".mp4", ".mov", ".webm", ".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}:
                raise HTTPException(422, "Неподдерживаемый формат медиафайла")
            limit = MAX_VIDEO if key == "source" else MAX_AUDIO
            target = folder / f"input-{index}{suffix}"
            size = 0
            with target.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > limit or total > MAX_TOTAL:
                        raise HTTPException(413, "Медиафайлы превышают лимит")
                    digest.update(key.encode() + chunk)
                    output.write(chunk)
            if not size:
                raise HTTPException(422, "Пустой медиафайл")
            try:
                streams = probe(target).get("streams", [])
                if key == "source" and not any(s.get("codec_type") == "video" for s in streams):
                    raise ValueError("В исходном файле нет видео")
                if key != "source" and not has_audio(target):
                    raise ValueError("В аудиофайле нет звуковой дорожки")
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            media_paths[key] = target
        try:
            fresh, receipt = service().accept(client_job_id, digest.hexdigest())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if fresh:
            background.add_task(service().execute, client_job_id, project, media_paths, comfy)
        return {"job_id": receipt["id"], "status": receipt["state"]}

    @router.get("/by-client/{client_job_id}")
    async def by_client(client_job_id: str) -> dict:
        job = service().get(client_job_id)
        if not job:
            raise HTTPException(404, "Задание звука не найдено")
        return {"job_id": job["id"], "status": job["state"]}

    @router.get("/jobs/{uid}")
    async def status(uid: str) -> dict:
        job = service().get(uid)
        if not job:
            raise HTTPException(404, "Задание звука не найдено")
        return {"job_id": job["id"], "status": job["state"], "error": job["error"]}

    @router.get("/jobs/{uid}/file")
    async def result(uid: str) -> FileResponse:
        job = service().get(uid)
        if not job or job["state"] != "success" or not job["output_path"]:
            raise HTTPException(404, "Результат ещё не готов")
        path = Path(job["output_path"])
        if not path.is_file() or not path.resolve().is_relative_to((service().root / uid).resolve()):
            raise HTTPException(404, "Результат отсутствует")
        return FileResponse(path, filename=f"studio-audio-{uid}{path.suffix}")

    @router.get("/jobs/{uid}/original-audio")
    async def original_audio(uid: str) -> FileResponse:
        job = service().get(uid)
        if not job or job["state"] != "success":
            raise HTTPException(404, "Исходный звук ещё не готов")
        path = service().root / uid / "original-audio.wav"
        if not path.is_file():
            raise HTTPException(404, "У исходного ролика нет звуковой дорожки")
        return FileResponse(path, media_type="audio/wav", filename=f"original-audio-{uid}.wav")

    @router.get("/jobs/{uid}/generated-music")
    async def generated_music(uid: str) -> FileResponse:
        job = service().get(uid)
        if not job or job["state"] != "success":
            raise HTTPException(404, "Музыка ещё не готова")
        path = service().root / uid / "generated-music.wav"
        if not path.is_file():
            raise HTTPException(404, "В задаче не создавалась музыка")
        return FileResponse(path, media_type="audio/wav", filename=f"generated-music-{uid}.wav")

    return router
