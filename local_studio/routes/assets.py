"""Reference library files: upload, list, the file itself, a small thumbnail, a finished clip
turned into a video reference, and the saved Create draft."""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..media import LIMITS, detect_media
from ..worker import sha256_file
from ..store import DraftConflict
from ..agents.contracts import AgentTaskRequest
from ..core.idempotency import EventConflict, StateConflict, UnauthorizedAgent
from ..draft_shot import DraftShotError, apply_draft_shot_proposal


class DraftInput(BaseModel):
    project: dict
    expected_revision: int


class ShotProposalInput(BaseModel):
    event_id: str = Field(min_length=1, max_length=120)
    agent_role: str = Field(min_length=1, max_length=80)
    base_revision: int = Field(ge=0)
    patch: dict


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    @router.post("/api/assets", status_code=201)
    async def import_asset(file: UploadFile = File(...), role: str = "Референс") -> dict:
        store = ctx.store
        if not file.filename:
            raise HTTPException(422, "Нужно имя файла")
        fd, temporary_name = tempfile.mkstemp(prefix="upload-", dir=store.assets_dir)
        temporary = Path(temporary_name)
        size = 0
        digest = hashlib.sha256()
        header = b""
        kind = None
        mime = None
        try:
            with os.fdopen(fd, "wb") as output:
                while chunk := await file.read(1024 * 1024):
                    if not header:
                        header = chunk[:64]
                        try:
                            kind, mime = detect_media(header)
                        except ValueError as exc:
                            raise HTTPException(422, str(exc)) from exc
                    size += len(chunk)
                    if size > LIMITS[kind]:
                        raise HTTPException(413, f"Файл превышает лимит {LIMITS[kind] // 1024 // 1024} МБ")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if not size:
                raise HTTPException(422, "Пустой файл")
            sha256 = digest.hexdigest()
            folder = store.assets_dir / sha256[:2]
            folder.mkdir(exist_ok=True)
            destination = folder / sha256
            if destination.exists():
                temporary.unlink()
            else:
                os.replace(temporary, destination)
            return store.add_asset(kind=kind, role=role[:120], original_name=Path(file.filename).name,
                                   mime=mime, size=size, sha256=sha256, path=destination)
        finally:
            temporary.unlink(missing_ok=True)

    @router.get("/api/assets")
    async def list_assets() -> list[dict]:
        return ctx.store.list_assets()

    @router.get("/api/draft")
    async def get_draft() -> dict:
        project, revision = ctx.store.load_draft_snapshot()
        return {"project": project, "revision": revision}

    @router.put("/api/draft")
    async def save_draft(payload: DraftInput) -> dict:
        try:
            revision = ctx.store.save_draft(payload.project, payload.expected_revision)
        except DraftConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"saved": True, "revision": revision}

    @router.get("/api/draft/history")
    async def draft_history() -> list[dict]:
        """Earlier canvas versions (store.save_draft keeps the last DRAFT_HISTORY): revision, time, scene titles."""
        return ctx.store.draft_history()

    @router.get("/api/draft/history/{revision}")
    async def draft_version(revision: int) -> dict:
        project = ctx.store.draft_version(revision)
        if project is None:
            raise HTTPException(404, "Такой версии холста нет в истории")
        return {"revision": revision, "project": project}

    @router.post("/api/draft/scenes/{scene_id}/shots/{shot_id}/proposals")
    async def apply_shot_proposal(scene_id: str, shot_id: str, payload: ShotProposalInput) -> dict:
        request = AgentTaskRequest(event_id=payload.event_id, agent_role=payload.agent_role,
                                   project_id=scene_id, shot_id=shot_id,
                                   base_revision=payload.base_revision, patch=payload.patch)
        try:
            revision, replayed = apply_draft_shot_proposal(ctx.store, request)
        except (StateConflict, EventConflict) as exc:
            raise HTTPException(409, str(exc)) from exc
        except UnauthorizedAgent as exc:
            raise HTTPException(403, f"Роль {exc} не может менять шот") from exc
        except DraftShotError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"revision": revision, "replayed": replayed}

    def library_file(asset_id: str, kinds: set[str] | None = None) -> dict:
        # Only files inside assets/: a row edited by hand must not serve any file on the Mac.
        asset = ctx.store.get_asset(asset_id)
        if not asset or (kinds and asset["kind"] not in kinds):
            raise HTTPException(404, "Файл не найден")
        path = Path(asset["path"])
        if not path.is_file() or not path.resolve().is_relative_to(ctx.store.assets_dir.resolve()):
            raise HTTPException(404, "Файл не найден")
        return asset

    @router.get("/api/assets/{asset_id}/file")
    async def asset_file(asset_id: str) -> FileResponse:
        asset = library_file(asset_id)
        return FileResponse(asset["path"], media_type=asset["mime"])

    @router.get("/api/assets/{asset_id}/thumbnail")
    async def asset_thumbnail(asset_id: str) -> FileResponse:
        # Pictures get a small JPEG too: Studio pages used to load every full-size reference image.
        asset = library_file(asset_id, {"video", "image"})
        picture = asset["kind"] == "image"
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise HTTPException(503, "ffmpeg недоступен")
        thumb_dir = ctx.data_root / "thumbnails"
        thumb_dir.mkdir(mode=0o700, exist_ok=True)
        thumb = thumb_dir / f"{asset_id}.jpg"
        if not thumb.is_file():
            # Unique per request: the canvas and the side panel ask for the same new picture at once,
            # and a shared temporary file made one of them fail with 500.
            temporary = thumb_dir / f".{asset_id}.{uuid.uuid4().hex}.jpg"
            try:
                seek = [] if picture else ["-ss", "0.3"]
                process = await asyncio.create_subprocess_exec(
                    ffmpeg, "-hide_banner", "-loglevel", "error", *seek,
                    "-i", asset["path"], "-frames:v", "1", "-vf", "scale='min(384,iw)':-2",
                    "-q:v", "4", "-y", str(temporary),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(process.communicate(), timeout=15)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.communicate()
                    raise HTTPException(422, "Не удалось сделать миниатюру")
                if process.returncode != 0 or not temporary.is_file():
                    if thumb.is_file():
                        return FileResponse(thumb, media_type="image/jpeg")
                    raise HTTPException(422, "Не удалось сделать миниатюру")
                os.replace(temporary, thumb)
            finally:
                temporary.unlink(missing_ok=True)
        return FileResponse(thumb, media_type="image/jpeg")

    @router.post("/api/character-refinement/source/{job_id}")
    @router.post("/api/jobs/{job_id}/reference-video")
    async def refinement_source(job_id: str) -> dict:
        store = ctx.store
        job = store.get_job(job_id)
        if not job or job["state"] != "completed" or not job.get("output_path"):
            raise HTTPException(422, "Нужен готовый ролик")
        path = Path(job["output_path"])
        if not path.is_file() or not path.resolve().is_relative_to(store.results_dir.resolve()):
            raise HTTPException(404, "Готовый ролик отсутствует")
        if path.stat().st_size > LIMITS["video"]:
            raise HTTPException(413, "Ролик превышает лимит видеореференса")
        digest = await asyncio.to_thread(sha256_file, path)
        existing = store.find_asset(sha256=digest, kind="video")
        if existing:
            return existing
        destination = store.assets_dir / (str(uuid.uuid4()) + ".mp4")
        try:
            await asyncio.to_thread(shutil.copyfile, path, destination)
            return store.add_asset(kind="video", role="Движение", original_name=f"h3-{job_id}.mp4",
                                   mime="video/mp4", size=destination.stat().st_size, sha256=digest, path=destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    return router
