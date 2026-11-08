"""«Создать новую раскадровку» and «Подготовить правку»: the Studio agent plans the scene (local_studio/agent_tasks.py,
it replaced the Qwen director on the GPU); planning never enqueues a render."""
from __future__ import annotations

import asyncio
import subprocess

from fastapi import APIRouter, HTTPException

from . import agent_tasks
from .agent_brain import BrainError
from .scene_evidence import prepare_evidence

from app.scene_planner import ScenePlanRequest
from app.scene_edits import SceneBoardEditRequest


def make_router(gpu_url: str, store=None) -> APIRouter:
    router = APIRouter()

    @router.post("/api/scene-plans")
    async def scene_plan(payload: ScenePlanRequest) -> dict:
        try:
            # Source video frames and cuts for a scene built from a motion reference (local, ffmpeg).
            body = await asyncio.to_thread(prepare_evidence, store, payload) if store is not None else payload.model_dump()
            return await agent_tasks.write_scene_plan(ScenePlanRequest(**body))
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(422, str(exc)) from exc
        except (BrainError, TimeoutError) as exc:
            raise HTTPException(502, f"Агент не составил раскадровку: {exc or 'не уложился во время'}") from exc

    @router.post("/api/scene-board-edits")
    async def scene_board_edit(payload: SceneBoardEditRequest) -> dict:
        try:
            return await agent_tasks.write_board_edit(payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (BrainError, TimeoutError) as exc:
            raise HTTPException(502, f"Агент не подготовил правку: {exc or 'не уложился во время'}") from exc

    return router
