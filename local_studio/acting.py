"""Mac-owned acting preparation: existing assets in, prepared video asset out."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import tempfile

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .store import StudioStore


LOCK_WAIT_S = 120


class ActingRequest(BaseModel):
    mode: str
    source_asset_id: str
    driving_asset_id: str


def make_router(store: StudioStore, gpu_url: str, dispatch_lock: asyncio.Lock) -> APIRouter:
    router = APIRouter(prefix="/api/acting", tags=["acting"])

    @router.get("/capabilities")
    async def capabilities() -> dict:
        if not gpu_url:
            return {"human": False, "animal": False}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{gpu_url}/api/acting/capabilities")
                response.raise_for_status()
                data = response.json()
            return {"human": data.get("human") is True, "animal": data.get("animal") is True}
        except (httpx.HTTPError, ValueError):
            return {"human": False, "animal": False}

    @router.post("/prepare", status_code=201)
    async def prepare(payload: ActingRequest) -> dict:
        if payload.mode not in {"human", "animal"}:
            raise HTTPException(422, "Выбери человека или животное")
        if not gpu_url:
            raise HTTPException(503, "GPU не подключён")
        source = store.get_asset(payload.source_asset_id)
        driving = store.get_asset(payload.driving_asset_id)
        if not source or source["kind"] != "image" or not driving or driving["kind"] != "video":
            raise HTTPException(422, "Для актёрской игры нужны фото и видеообразец из медиатеки")
        for asset in (source, driving):
            path = Path(asset["path"])
            if not path.is_file() or not path.resolve().is_relative_to(store.assets_dir.resolve()):
                raise HTTPException(409, "Исходный файл отсутствует")
        # The GPU is shared with the render queue; wait for a dispatcher pass to finish, but bounded
        # (a pass may upload or download for many minutes).
        try:
            await asyncio.wait_for(dispatch_lock.acquire(), timeout=LOCK_WAIT_S)
        except asyncio.TimeoutError:
            raise HTTPException(409, "GPU сейчас занят очередью (отправка или скачивание ролика). "
                                     "Повтори через минуту") from None
        try:
            with open(source["path"], "rb") as image, open(driving["path"], "rb") as video:
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(960, connect=15)) as client:
                        async with client.stream("POST", f"{gpu_url}/api/acting/prepare",
                                                 data={"mode": payload.mode},
                                                 files={"source": ("source.jpg", image, source["mime"]),
                                                        "driving": ("driving.mp4", video, driving["mime"])}) as response:
                            if response.status_code != 200:
                                body = (await response.aread())[:1000].decode("utf-8", "replace")
                                raise HTTPException(response.status_code, body)
                            fd, name = tempfile.mkstemp(prefix="acting-", dir=store.assets_dir)
                            temporary = Path(name)
                            digest = hashlib.sha256()
                            size = 0
                            try:
                                with os.fdopen(fd, "wb") as output:
                                    async for chunk in response.aiter_bytes(1024 * 1024):
                                        size += len(chunk)
                                        if size > 500 * 1024 * 1024:
                                            raise HTTPException(413, "Подготовленный ролик превышает 500 МБ")
                                        digest.update(chunk)
                                        output.write(chunk)
                                    output.flush()
                                    os.fsync(output.fileno())
                                with temporary.open("rb") as check:
                                    header = check.read(12)
                                if size < 12 or header[4:8] != b"ftyp":
                                    raise HTTPException(502, "GPU вернул некорректный MP4")
                                sha = digest.hexdigest()
                                folder = store.assets_dir / sha[:2]
                                folder.mkdir(exist_ok=True)
                                destination = folder / sha
                                if destination.exists():
                                    temporary.unlink()
                                else:
                                    os.replace(temporary, destination)
                                return store.add_asset(kind="video", role="Актёрская игра",
                                                       original_name=f"acting-{payload.mode}-{sha[:12]}.mp4",
                                                       mime="video/mp4", size=size, sha256=sha, path=destination)
                            finally:
                                temporary.unlink(missing_ok=True)
                except httpx.HTTPError as exc:
                    raise HTTPException(502, f"GPU недоступен во время подготовки: {exc}") from exc
        finally:
            dispatch_lock.release()
    return router
