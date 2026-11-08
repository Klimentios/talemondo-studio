"""A still drawn by Codex from Studio pictures (its image_gen on the user's ChatGPT subscription, no GPU).

Replaces the GPU «Ключевой кадр» (Qwen-Image-Edit) for the canvas tools that need one picture, such as the
fantasy world of a scene. Tasks live in memory: a Studio restart reports the drawing as interrupted."""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import scene_frames
from .store import StudioStore

SIZE = r"^\d{3,4}x\d{3,4}$"


class FrameInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    asset_ids: list[str] = Field(min_length=1, max_length=8)
    roles: list[str] | None = Field(default=None, max_length=8)
    size: str = Field(default="768x1344", pattern=SIZE)
    role: str = Field(default="Кадр сцены", max_length=40)


def make_router(store: StudioStore) -> APIRouter:
    router = APIRouter(prefix="/api/codex-frames", tags=["codex-frames"])
    tasks: dict[str, dict] = {}

    def save(data: bytes, role: str) -> dict:
        sha256 = hashlib.sha256(data).hexdigest()
        folder = store.assets_dir / sha256[:2]
        folder.mkdir(exist_ok=True)
        destination = folder / sha256
        if not destination.exists():
            temporary = folder / f".codex-frame-{sha256[:8]}.part"
            temporary.write_bytes(data)
            os.replace(temporary, destination)
        return store.add_asset(kind="image", role=role, original_name="codex-frame.png", mime="image/png",
                               size=len(data), sha256=sha256, path=destination)

    async def draw(key: str, payload: FrameInput) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="h3-codex-frame-") as tmp:
                pictures = []
                for at, asset_id in enumerate(payload.asset_ids, 1):
                    asset = store.get_asset(asset_id)
                    if not asset or asset["kind"] != "image":
                        raise ValueError(f"картинка {asset_id} не найдена в библиотеке")
                    path = Path(tmp) / f"image-{at:02d}{Path(asset['path']).suffix or '.png'}"
                    path.write_bytes(Path(asset["path"]).read_bytes())
                    pictures.append(path)
                roles = payload.roles or [store.get_asset(i)["role"] for i in payload.asset_ids]
                data = await scene_frames.draw(payload.model_dump(), [{"role": r} for r in roles], pictures)
            asset = await asyncio.to_thread(save, data, payload.role)
            tasks[key] = {"frame_id": key, "state": "completed", "asset_id": asset["asset_id"], "error": ""}
        except Exception as exc:  # the canvas shows the reason; nothing is retried by itself
            tasks[key] = {"frame_id": key, "state": "failed", "asset_id": None, "error": str(exc)[:500]}

    @router.post("", status_code=202)
    async def create(payload: FrameInput) -> dict:
        key = f"codex-{secrets.token_hex(6)}"
        tasks[key] = {"frame_id": key, "state": "running", "asset_id": None, "error": ""}
        asyncio.get_running_loop().create_task(draw(key, payload))
        return tasks[key]

    @router.get("/{frame_id}")
    async def get(frame_id: str) -> dict:
        task = tasks.get(frame_id)
        if not task:
            return {"frame_id": frame_id, "state": "failed", "asset_id": None,
                    "error": "рисование прервано перезапуском Studio"}
        return task

    return router
