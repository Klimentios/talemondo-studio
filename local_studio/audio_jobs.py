"""Mac-owned FIFO receipts for post-production audio on the GPU server."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .audio_project import validate_audio_project
from .side_jobs import ACTIVE, SideQueueRules, is_not_sent, response_detail
from .store import StudioStore


class AudioJobInput(BaseModel):
    project: dict


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stream_kinds(path: Path) -> set[str]:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    return {s["codec_type"] for s in json.loads(result.stdout).get("streams", [])} if result.returncode == 0 else set()


def _source_has_audio_or_unreadable(path: Path) -> bool:
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                            "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30)
    return bool(probe.returncode or probe.stdout.strip())


class AudioQueue(SideQueueRules):
    TABLE = "audio_jobs"
    ID = "audio_id"
    NOUN = "звуковое задание"

    def __init__(self, store: StudioStore, gpu_url: str):
        self.store = store
        self.gpu_url = gpu_url
        self._init_rules()
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS audio_jobs (
                audio_id TEXT PRIMARY KEY, project_json TEXT NOT NULL, manifest_json TEXT NOT NULL,
                state TEXT NOT NULL, remote_job_id TEXT, output_path TEXT, original_audio_path TEXT, error TEXT,
                generated_music_path TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(audio_jobs)")}
            if "original_audio_path" not in columns:
                db.execute("ALTER TABLE audio_jobs ADD COLUMN original_audio_path TEXT")
            if "generated_music_path" not in columns:
                db.execute("ALTER TABLE audio_jobs ADD COLUMN generated_music_path TEXT")

    def get(self, uid: str) -> dict | None:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM audio_jobs WHERE audio_id=?", (uid,)).fetchone()
            return dict(row) if row else None

    def list(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT audio_id,state,remote_job_id,output_path,original_audio_path,generated_music_path,error,created_at,updated_at FROM audio_jobs ORDER BY created_at DESC,rowid DESC")]

    def create(self, project: dict) -> dict:
        project = validate_audio_project(project, self.store)
        manifest: dict[str, dict] = {}
        source = project["source"]
        if project["mode"] == "video":
            if source["kind"] == "job":
                job = self.store.get_job(source["id"])
                if not job or job["state"] != "completed" or not job.get("output_path"):
                    raise ValueError("Выбери готовый ролик")
                path = Path(job["output_path"])
                if not path.is_file() or not path.resolve().is_relative_to(self.store.results_dir.resolve()):
                    raise ValueError("Исходный MP4 недоступен")
                manifest["source"] = {"kind": "job", "id": source["id"], "sha256": _hash(path)}
            elif source["kind"] == "asset":
                manifest["source"] = self._asset_entry(source["id"], "video")
            else:
                raise ValueError("Выбери исходное видео")
        for key, asset_id in (("music", project["mix"]["music_asset_id"]),
                              ("effects", project["mix"]["effects_asset_id"])):
            if asset_id:
                manifest[key] = self._asset_entry(asset_id, "audio")
        for line in project["lines"]:
            if line["voice_mode"] == "reference":
                if not line.get("voice_asset_id"):
                    raise ValueError("Прикрепи образец голоса к каждой реплике с референсом")
                key = f"voice:{line['voice_asset_id']}"
                manifest[key] = self._asset_entry(line["voice_asset_id"], "audio")
        if not any((any(str(line.get("text", "")).strip() for line in project["lines"]),
                    project["mix"]["music_asset_id"], project["mix"]["music_prompt"], project["mix"]["effects_asset_id"],
                    project["mode"] == "video" and project["mix"]["source_level"] > 0)):
            raise ValueError("Добавь речь, музыку, эффект или исходный звук")
        uid = str(uuid.uuid4())
        with self.store.connect() as db:
            db.execute("INSERT INTO audio_jobs(audio_id,project_json,manifest_json,state) VALUES (?,?,?,'queued_local')",
                       (uid, json.dumps(project, ensure_ascii=False), json.dumps(manifest, ensure_ascii=False)))
        return self.get(uid)

    def _asset_entry(self, asset_id: str, kind: str) -> dict:
        asset = self.store.get_asset(asset_id)
        if not asset or asset["kind"] != kind:
            raise ValueError("Медиафайл проекта не найден")
        path = Path(asset["path"])
        if not path.is_file() or not path.resolve().is_relative_to(self.store.assets_dir.resolve()):
            raise ValueError("Медиафайл проекта недоступен")
        return {"kind": "asset", "id": asset_id, "sha256": asset["sha256"]}

    def _path(self, entry: dict) -> Path:
        if entry["kind"] == "job":
            job = self.store.get_job(entry["id"])
            path = Path(job["output_path"]) if job and job.get("output_path") and job["state"] == "completed" else None
            root = self.store.results_dir
        else:
            asset = self.store.get_asset(entry["id"])
            path = Path(asset["path"]) if asset else None
            root = self.store.assets_dir
        if not path or not path.is_file() or not path.resolve().is_relative_to(root.resolve()) or _hash(path) != entry["sha256"]:
            raise ValueError("Медиафайл изменён после постановки задачи")
        return path

    def update(self, uid: str, state: str, **fields: str) -> None:
        if state not in ACTIVE | {"completed", "failed"} or fields.keys() - {"remote_job_id", "output_path", "original_audio_path", "generated_music_path", "error"}:
            raise ValueError("Неверное состояние звуковой задачи")
        parts = ["state=?", "updated_at=CURRENT_TIMESTAMP"] + [f"{key}=?" for key in fields]
        with self.store.connect() as db:
            db.execute(f"UPDATE audio_jobs SET {', '.join(parts)} WHERE audio_id=? AND state!='cancelled'",
                       (state, *fields.values(), uid))

    async def _lookup(self, uid: str) -> dict | None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.gpu_url}/api/audio/by-client/{uid}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def run_once(self) -> bool:
        job = self.first_active()
        if not job or not self.gpu_url:
            if self.gpu_url:
                return await self.reconcile()
            return False
        uid, state = job["audio_id"], job["state"]
        if state in {"queued_local", "uploading"}:
            try:
                receipt = await self._lookup(uid)
            except Exception:
                return False
            if receipt and receipt.get("status") == "needs_reconciliation":
                self.park(uid, "GPU перезапущен во время задачи; автоповтор отключён")
                return True
            if receipt and receipt.get("job_id"):
                self.update(uid, "queued_gpu", remote_job_id=receipt["job_id"], error="")
                return True
            if state != "queued_local":
                self.park(uid, "Неясно, принял ли GPU звуковую задачу; автоповтор отключён")
                return True
            project = json.loads(job["project_json"])
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(f"{self.gpu_url}/api/audio/capabilities")
                    response.raise_for_status()
                    caps = response.json()
                if any(line.get("text", "").strip() for line in project["lines"]) and not caps.get("tts"):
                    raise ValueError("CosyVoice3 не установлена на GPU")
                if project.get("lip_sync") and not caps.get("lip_sync"):
                    raise ValueError("Синхронизация губ не установлена на GPU")
                if project["mix"].get("music_prompt") and not caps.get("music_generation"):
                    raise ValueError("Генерация музыки по описанию не установлена на GPU")
                mix = project["mix"]
                custom_music = (mix.get("music_bpm") not in (None, "") or bool(mix.get("music_keyscale"))
                                or bool(mix.get("music_timesignature")) or mix.get("music_seed", -1) != -1)
                if mix.get("music_prompt") and custom_music and not caps.get("music_controls"):
                    raise ValueError("Настройки музыки ещё не установлены на GPU")
                if project["mix"].get("effects_prompt") and not caps.get("effects_generation"):
                    raise ValueError("Генерация эффектов по описанию не установлена на GPU")
                manifest = json.loads(job["manifest_json"])
                paths = await asyncio.to_thread(lambda: {key: self._path(entry) for key, entry in manifest.items()})
            except ValueError as exc:
                self.update(uid, "failed", error=str(exc))
                return True
            except Exception:
                return False
            self.update(uid, "uploading")
            try:
                with ExitStack() as stack:
                    files = [("media", (path.name, stack.enter_context(path.open("rb")),
                                         "video/mp4" if key == "source" else "audio/wav")) for key, path in paths.items()]
                    async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=20)) as client:
                        response = await client.post(f"{self.gpu_url}/api/audio/jobs",
                            data={"client_job_id": uid, "project_json": job["project_json"],
                                  "media_ids_json": json.dumps(list(paths))}, files=files)
                        if response.status_code == 409:
                            self.park(uid, "GPU вернул конфликт; сверка по client_job_id")
                            return True
                        if response.status_code in {413, 422, 503}:
                            self.update(uid, "failed", error=response_detail(response))
                            return True
                        response.raise_for_status()
                        remote_id = response.json()["job_id"]
                self.update(uid, "queued_gpu", remote_job_id=remote_id, error="")
            except Exception as exc:
                if is_not_sent(exc):
                    self.update(uid, "queued_local", error="")
                    return False
                self.park(uid, f"Отправка требует сверки: {exc}")
            return True
        if state in {"queued_gpu", "running"}:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.get(f"{self.gpu_url}/api/audio/jobs/{job['remote_job_id']}")
                    if response.status_code == 404:
                        remote = None
                    else:
                        response.raise_for_status()
                        remote = response.json()
            except Exception:
                return False
            lost = self.lost_on_gpu(uid, remote)
            if lost:
                self.update(uid, "failed", error=lost)
            elif remote is None or remote.get("status") == "unknown":
                return False
            elif remote["status"] == "success":
                self.update(uid, "downloading")
            elif remote["status"] == "error":
                self.update(uid, "failed", error=remote.get("error", "Ошибка звуковой обработки"))
            elif remote["status"] == "running" and state != "running":
                self.update(uid, "running")
            elif remote["status"] == "needs_reconciliation":
                # The GPU audio service restarted mid-job; it never resumes such a job.
                self.update(uid, "failed", error=remote.get("error") or "GPU перезапущен во время задачи; запусти заново")
            return True
        if state == "downloading":
            return await self._download(job)
        return False

    async def _download(self, job: dict) -> bool:
        uid = job["audio_id"]
        project = json.loads(job["project_json"])
        extension = "mp4" if project["mode"] == "video" else project["output_format"]
        temporary = self.store.results_dir / f".audio-{uid}.part"
        destination = self.store.results_dir / f"audio-{uid}.{extension}"
        original_temporary = self.store.results_dir / f".audio-original-{uid}.part"
        original_destination = self.store.results_dir / f"audio-original-{uid}.wav"
        music_temporary = self.store.results_dir / f".audio-music-{uid}.part"
        music_destination = self.store.results_dir / f"audio-music-{uid}.wav"
        try:
            temporary.unlink(missing_ok=True)
            original_temporary.unlink(missing_ok=True)
            music_temporary.unlink(missing_ok=True)
            original_found = False
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=20)) as client:
                async with client.stream("GET", f"{self.gpu_url}/api/audio/jobs/{job['remote_job_id']}/file") as response:
                    response.raise_for_status()
                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            output.write(chunk)
                if project["mode"] == "video":
                    async with client.stream("GET", f"{self.gpu_url}/api/audio/jobs/{job['remote_job_id']}/original-audio") as response:
                        if response.status_code != 404:
                            response.raise_for_status()
                            original_found = True
                            with original_temporary.open("wb") as output:
                                async for chunk in response.aiter_bytes(1024 * 1024):
                                    output.write(chunk)
                if project["mix"].get("music_prompt"):
                    async with client.stream("GET", f"{self.gpu_url}/api/audio/jobs/{job['remote_job_id']}/generated-music") as response:
                        response.raise_for_status()
                        with music_temporary.open("wb") as output:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                output.write(chunk)
            if project["mode"] == "video" and not original_found:
                source_entry = json.loads(job["manifest_json"])["source"]
                source_path = await asyncio.to_thread(self._path, source_entry)
                if await asyncio.to_thread(_source_has_audio_or_unreadable, source_path):
                    raise ValueError("GPU не вернул исходный звук ролика")
            kinds = await asyncio.to_thread(_stream_kinds, temporary)
            if "audio" not in kinds or project["mode"] == "video" and "video" not in kinds:
                raise ValueError("Звуковой результат повреждён или не содержит нужных дорожек")
            original_path = None
            if original_temporary.is_file():
                if "audio" not in await asyncio.to_thread(_stream_kinds, original_temporary):
                    raise ValueError("Исходная аудиодорожка повреждена")
                os.replace(original_temporary, original_destination)
                original_path = str(original_destination)
            music_path = None
            if project["mix"].get("music_prompt"):
                if "audio" not in await asyncio.to_thread(_stream_kinds, music_temporary):
                    raise ValueError("Отдельная музыкальная дорожка повреждена")
                os.replace(music_temporary, music_destination)
                music_path = str(music_destination)
            os.replace(temporary, destination)
            self._forget(uid)
            self.update(uid, "completed", output_path=str(destination),
                        original_audio_path=original_path, generated_music_path=music_path, error="")
        except ValueError as exc:
            temporary.unlink(missing_ok=True)
            original_temporary.unlink(missing_ok=True)
            music_temporary.unlink(missing_ok=True)
            self._forget(uid)
            self.update(uid, "failed", error=str(exc))
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            original_temporary.unlink(missing_ok=True)
            music_temporary.unlink(missing_ok=True)
            error = self.download_failed(uid, exc)
            if error:
                self.update(uid, "failed", error=error)
                return True
            return False
        return True


def make_router(queue: AudioQueue) -> APIRouter:
    router = APIRouter(prefix="/api/audio", tags=["audio-jobs"])

    @router.get("/jobs")
    async def list_jobs() -> list[dict]:
        return queue.list()

    @router.post("/jobs", status_code=202)
    async def create(payload: AudioJobInput) -> dict:
        try:
            job = queue.create(payload.project)
            return {key: job[key] for key in ("audio_id", "state", "created_at")}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/jobs/{uid}/cancel")
    async def cancel(uid: str) -> dict:
        try:
            job = queue.cancel(uid)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {key: job[key] for key in ("audio_id", "state", "created_at")}

    @router.get("/jobs/{uid}/file")
    async def file(uid: str) -> FileResponse:
        job = queue.get(uid)
        if not job or job["state"] != "completed" or not job["output_path"]:
            raise HTTPException(404, "Звуковой результат ещё не готов")
        path = Path(job["output_path"])
        if not path.is_file() or not path.resolve().is_relative_to(queue.store.results_dir.resolve()):
            raise HTTPException(404, "Звуковой результат отсутствует")
        mime = {"mp4": "video/mp4", "mp3": "audio/mpeg", "wav": "audio/wav"}[path.suffix.lstrip(".")]
        return FileResponse(path, media_type=mime, filename=path.name)

    @router.get("/jobs/{uid}/original-audio")
    async def original_audio(uid: str) -> FileResponse:
        job = queue.get(uid)
        if not job or job["state"] != "completed" or not job["original_audio_path"]:
            raise HTTPException(404, "У ролика нет отдельной исходной аудиодорожки")
        path = Path(job["original_audio_path"])
        if not path.is_file() or not path.resolve().is_relative_to(queue.store.results_dir.resolve()):
            raise HTTPException(404, "Исходная аудиодорожка отсутствует")
        return FileResponse(path, media_type="audio/wav", filename=path.name)

    @router.get("/jobs/{uid}/generated-music")
    async def generated_music(uid: str) -> FileResponse:
        job = queue.get(uid)
        if not job or job["state"] != "completed" or not job["generated_music_path"]:
            raise HTTPException(404, "Отдельная музыка ещё не готова")
        path = Path(job["generated_music_path"])
        if not path.is_file() or not path.resolve().is_relative_to(queue.store.results_dir.resolve()):
            raise HTTPException(404, "Музыкальная дорожка отсутствует")
        return FileResponse(path, media_type="audio/wav", filename=path.name)

    return router
