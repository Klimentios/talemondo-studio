"""«✍ Сценарист»: attach small reference previews and let the Studio agent write the official H3 prompt
(local_studio/agent_tasks.py; it replaced the Qwen director on the GPU)."""
from __future__ import annotations

import asyncio

import base64
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import agent_tasks
from .agent_brain import BrainError
from .store import StudioStore

from app.scenarist import ScenaristRequest


class SceneReference(BaseModel):
    asset_id: str
    kind: str = Field(pattern="^(image|video|audio)$")
    number: int = Field(ge=1, le=12)
    role: str = ""
    owner: str = ""


class SceneBrief(BaseModel):
    brief: str = Field(min_length=3, max_length=20000)
    references: list[SceneReference] = Field(default_factory=list, max_length=12)
    duration: float = Field(default=10, ge=4, le=15)
    shots: int = Field(default=1, ge=1, le=6)
    speaking_owners: list[str] = Field(default_factory=list)
    style_hint: str = ""
    use_video_audio: bool = False
    # Official camera words (app/camera_words.py), validated on the GPU: {movement, direction, speed,
    # amplitude, shot?} or a list of them. Passed through unchanged.
    camera: dict | list[dict] | None = None


def preview_jpeg(path: Path, edge: int = 768) -> str:
    """Downscaled JPEG (base64) so the agent sees the hero without a multi-MB picture."""
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale='min({edge},iw)':-2", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
        capture_output=True, timeout=30, check=False)
    return base64.b64encode(result.stdout).decode("ascii") if result.returncode == 0 and result.stdout else ""


def make_router(store: StudioStore, gpu_url: str) -> APIRouter:
    router = APIRouter()

    @router.post("/api/scenarist")
    async def scenarist(payload: SceneBrief) -> dict:
        refs = []
        for ref in payload.references:
            item = {"kind": ref.kind, "number": ref.number, "role": ref.role, "owner": ref.owner}
            asset = store.get_asset(ref.asset_id)
            if ref.kind == "image" and asset and Path(asset["path"]).is_file():
                item["image_jpeg_base64"] = await asyncio.to_thread(preview_jpeg, Path(asset["path"]))
            refs.append(item)
        # Shots split the clip evenly; the writer gets exact "[Shot N] At MM:SS.mmm" headers.
        cut = round(payload.duration / payload.shots, 3)
        body = {"brief": payload.brief, "references": refs, "cut_durations": [cut] * payload.shots,
                "speaking_owners": payload.speaking_owners, "style_hint": payload.style_hint,
                "use_video_audio": payload.use_video_audio}
        if payload.camera is not None:
            body["camera"] = payload.camera
        try:
            return await agent_tasks.write_ir(ScenaristRequest(**body))
        except ValueError as exc:  # pydantic validation and camera words
            raise HTTPException(422, str(exc)) from exc
        except (BrainError, TimeoutError) as exc:
            raise HTTPException(502, f"Агент не написал промпт: {exc or 'не уложился во время'}") from exc

    return router
