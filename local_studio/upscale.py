"""Mac queue receipts for GPU-only SeedVR2 processing."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import uuid

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .side_jobs import ACTIVE, REFUSED_BEFORE_QUEUE, SideQueueRules, is_not_sent, response_detail
from .store import StudioStore
from app.upscale_graph import MODES
from .worker import validate_mp4


def _has_audio(path: Path) -> bool:
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True, timeout=20, check=False)
    if result.returncode:
        raise ValueError("Не удалось проверить звуковую дорожку MP4")
    return bool(result.stdout.strip())


class UpscaleQueue(SideQueueRules):
    TABLE = "upscale_jobs"
    ID = "upscale_id"
    NOUN = "улучшение ролика"

    def __init__(self, store: StudioStore, gpu_url: str) -> None:
        self.store = store
        self.gpu_url = gpu_url
        self._init_rules()
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS upscale_jobs (
                upscale_id TEXT PRIMARY KEY, source_job_id TEXT NOT NULL,
                state TEXT NOT NULL, remote_job_id TEXT, output_path TEXT, error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            # Enhancement mode (app/upscale_graph.MODES); rows from before it are SeedVR2 upscales.
            columns = {row[1] for row in db.execute("PRAGMA table_info(upscale_jobs)")}
            if "mode" not in columns:
                db.execute("ALTER TABLE upscale_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'upscale'")

    def list(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM upscale_jobs ORDER BY created_at DESC, rowid DESC")]

    def create(self, source_job_id: str, mode: str = "upscale") -> dict:
        if mode not in MODES:
            raise ValueError("Неизвестный режим улучшения ролика")
        source = self.store.get_job(source_job_id)
        if not source or source["state"] != "completed" or not source.get("output_path"):
            raise ValueError("Выбери готовый ролик")
        path = Path(source["output_path"])
        if not path.is_file() or not path.resolve().is_relative_to(self.store.results_dir.resolve()):
            raise ValueError("Исходный MP4 не найден")
        with self.store.connect() as db:
            previous = db.execute("SELECT * FROM upscale_jobs WHERE source_job_id=? AND state IN "
                                  "('queued_local','uploading','needs_reconciliation','queued_gpu','running','downloading')",
                                  (source_job_id,)).fetchone()
            if previous:
                return dict(previous)
            upscale_id = str(uuid.uuid4())
            db.execute("INSERT INTO upscale_jobs(upscale_id,source_job_id,state,mode) VALUES (?,?,'queued_local',?)",
                       (upscale_id, source_job_id, mode))
            return dict(db.execute("SELECT * FROM upscale_jobs WHERE upscale_id=?", (upscale_id,)).fetchone())

    def update(self, upscale_id: str, state: str, **fields: str) -> None:
        if state not in ACTIVE | {"completed", "failed"}:
            raise ValueError("Недопустимое состояние апскейла")
        allowed = {"remote_job_id", "output_path", "error"}
        if fields.keys() - allowed:
            raise ValueError("Недопустимое поле апскейла")
        parts = ["state=?", "updated_at=CURRENT_TIMESTAMP"] + [f"{key}=?" for key in fields]
        with self.store.connect() as db:
            db.execute(f"UPDATE upscale_jobs SET {', '.join(parts)} WHERE upscale_id=? AND state!='cancelled'",
                       (state, *fields.values(), upscale_id))

    async def _lookup(self, upscale_id: str) -> dict | None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.gpu_url}/api/upscale/by-client/{upscale_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def _capability_problem(self, mode: str) -> str | None:
        """GET /api/upscale/capabilities before uploading (like audio_jobs). Raises when the GPU is unreachable."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
            response = await client.get(f"{self.gpu_url}/api/upscale/capabilities", params={"mode": mode})
        if response.status_code == 404:
            return "GPU-сервер старой версии: нет /api/upscale. Обнови сервер: scripts/h3_new_server.sh"
        if response.status_code == 422:
            return response_detail(response)
        response.raise_for_status()
        caps = response.json()
        if caps.get("available") is True:
            return None
        missing = [str(item) for item in caps.get("missing") or []]
        if missing == ["GPU недоступен"]:
            raise RuntimeError("GPU-движок недоступен")  # transient: ComfyUI restarting
        return f"{caps.get('model') or 'Улучшение ролика'} не установлено на GPU: {', '.join(missing) or 'нет узлов'}"

    async def run_once(self) -> bool:
        job = self.first_active()
        if not job or not self.gpu_url:
            if self.gpu_url:
                return await self.reconcile()
            return False
        uid, state = job["upscale_id"], job["state"]
        mode = job.get("mode") or "upscale"
        if state in {"queued_local", "uploading"}:
            try:
                receipt = await self._lookup(uid)
            except Exception:
                return False
            if receipt and receipt.get("job_id"):
                self.update(uid, "queued_gpu", remote_job_id=receipt["job_id"], error="")
                return True
            if state != "queued_local":
                self.park(uid, "Неясно, принял ли GPU задание; автоповтор отключён")
                return True
            source = self.store.get_job(job["source_job_id"])
            path = Path(source["output_path"]) if source and source.get("output_path") else None
            if not path or not path.is_file() or not path.resolve().is_relative_to(self.store.results_dir.resolve()):
                self.update(uid, "failed", error="Исходный MP4 отсутствует")
                return True
            try:
                problem = await self._capability_problem(mode)
            except Exception:
                return False  # nothing was sent; try again when the GPU answers
            if problem:
                self.update(uid, "failed", error=problem)
                return True
            self.update(uid, "uploading")
            try:
                with path.open("rb") as stream:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:
                        response = await client.post(f"{self.gpu_url}/api/upscale/jobs",
                                                     data={"client_job_id": uid, "mode": mode},
                                                     files={"video": ("source.mp4", stream, "video/mp4")})
                if response.status_code in REFUSED_BEFORE_QUEUE:
                    # Refused before the GPU wrote a receipt: nothing to reconcile.
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
                    response = await client.get(f"{self.gpu_url}/api/upscale/jobs/{job['remote_job_id']}")
                    remote = None if response.status_code == 404 else response.raise_for_status().json()
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
                self.update(uid, "failed", error=remote.get("error", "Ошибка SeedVR2"))
            elif remote["status"] == "running" and state != "running":
                self.update(uid, "running")
            return True
        if state == "downloading":
            temporary = self.store.results_dir / f".upscale-{uid}.part"
            destination = self.store.results_dir / f"upscale-{uid}.mp4"
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:
                    async with client.stream("GET", f"{self.gpu_url}/api/upscale/jobs/{job['remote_job_id']}/video") as response:
                        response.raise_for_status()
                        with temporary.open("wb") as output:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                output.write(chunk)
                await asyncio.to_thread(validate_mp4, temporary)
                source = self.store.get_job(job["source_job_id"])
                if (source and source.get("output_path")
                        and await asyncio.to_thread(_has_audio, Path(source["output_path"]))
                        and not await asyncio.to_thread(_has_audio, temporary)):
                    raise ValueError("Апскейл потерял исходный звук")
                os.replace(temporary, destination)
                self._forget(uid)
                self.update(uid, "completed", output_path=str(destination))
            except ValueError as exc:
                temporary.unlink(missing_ok=True)
                self._forget(uid)
                self.update(uid, "failed", error=str(exc))
                return True
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                error = self.download_failed(uid, exc)
                if error:
                    self.update(uid, "failed", error=error)
                    return True
                return False
            return True
        return False


def make_router(queue: UpscaleQueue) -> APIRouter:
    router = APIRouter(prefix="/api/upscale", tags=["upscale"])

    @router.get("/jobs")
    async def list_jobs() -> list[dict]:
        return queue.list()

    @router.post("/jobs/{source_job_id}", status_code=202)
    async def create(source_job_id: str, mode: str = "upscale") -> dict:
        try:
            return queue.create(source_job_id, mode)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/jobs/{upscale_id}/cancel")
    async def cancel(upscale_id: str) -> dict:
        try:
            return queue.cancel(upscale_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/jobs/{upscale_id}/video")
    async def video(upscale_id: str) -> FileResponse:
        job = next((item for item in queue.list() if item["upscale_id"] == upscale_id), None)
        if not job or job["state"] != "completed" or not job["output_path"]:
            raise HTTPException(404, "Апскейл ещё не готов")
        path = Path(job["output_path"])
        if not path.is_file() or not path.resolve().is_relative_to(queue.store.results_dir.resolve()):
            raise HTTPException(404, "Результат отсутствует")
        name = {"smooth": "rife48", "upscale_smooth": "seedvr2-rife48"}.get(job.get("mode"), "seedvr2")
        return FileResponse(path, media_type="video/mp4", filename=f"{name}-{upscale_id}.mp4")

    return router
