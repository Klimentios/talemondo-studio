"""Scenarist: the local Qwen writes an official-format H3 prompt from the user's brief.

POST /api/scenarist is called by Mac Studio. It never runs beside a render: an awake Qwen
(~28 GB) next to H3 would not fit, so a busy ComfyUI answers 409 and Studio retries later.
Qwen wakes for the request and goes back to sleep (1.3 GB) right after, like the Director.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .camera_words import camera_phrase
from .director import DIRECTOR_MISSING, LocalQwenDirector, director_installed
from .h3_ir import Reference, build_skeleton, scenarist_messages, shot_starts, validate_ir
from .qwen_lifecycle import planning_lock

log = logging.getLogger(__name__)
MAX_TOKENS = 2600  # six sections, ~500-word description plus dialogue


class ScenaristReference(BaseModel):
    kind: str = Field(pattern="^(image|video|audio)$")
    number: int = Field(ge=1, le=12)
    role: str
    owner: str = ""
    image_jpeg_base64: str = ""  # optional preview frame, lets Qwen describe what it sees


class CameraMove(BaseModel):
    """One camera move in the official vocabulary (app/camera_words.py); shot: 1-based, empty = every shot."""
    movement: str = Field(min_length=1, max_length=32)
    direction: str = Field(default="", max_length=32)
    speed: str = Field(default="", max_length=16)
    amplitude: str = Field(default="", max_length=16)
    shot: int | None = Field(default=None, ge=1, le=12)


class ScenaristRequest(BaseModel):
    brief: str = Field(min_length=3, max_length=20000)
    references: list[ScenaristReference] = Field(default_factory=list, max_length=12)
    cut_durations: list[float] = Field(default_factory=lambda: [10.0], min_length=1, max_length=12)
    speaking_owners: list[str] = Field(default_factory=list)
    style_hint: str = ""
    use_video_audio: bool = False
    # Studio camera choice in official words: one move for the clip or one per shot.
    camera: CameraMove | list[CameraMove] | None = None


def camera_lines(camera: CameraMove | list[CameraMove] | None, shots: int) -> list[str]:
    """CAMERA lines for the scenarist; ValueError (-> 422) for an unknown move or a shot past the cut list."""
    moves = [camera] if isinstance(camera, CameraMove) else list(camera or [])
    lines = []
    for move in moves:
        if move.shot is not None and move.shot > shots:
            raise ValueError(f"camera.shot {move.shot}: в сцене {shots} шот(а)")
        where = f"[Shot {move.shot}] " if move.shot else ""
        lines.append(f"CAMERA: {where}{camera_phrase(move.movement, move.direction, move.speed, move.amplitude)}")
    return lines


def _chat(director: LocalQwenDirector, messages: list[dict]) -> str:
    body = {"model": director.model, "messages": messages, "temperature": 0.4, "max_tokens": MAX_TOKENS,
            **getattr(director, "scenarist_extras", {})}  # H3_SCENARIST_PROFILE; empty by default
    request = urllib.request.Request(f"{director.base_url}/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=240) as response:
        result = json.load(response)
    choice = (result.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError("Сценарист упёрся в лимит длины ответа")
    return str((choice.get("message") or {}).get("content") or "").strip()


def ir_messages(payload: ScenaristRequest) -> tuple[list[dict], object, list[str]]:
    """Chat messages of one Scenarist request (reference pictures inline) with its skeleton and shot headers."""
    refs = [Reference(r.kind, r.number, r.role, r.owner) for r in payload.references]
    skeleton = build_skeleton(refs, speaking_owners=payload.speaking_owners, use_video_audio=payload.use_video_audio)
    headers = shot_starts(payload.cut_durations)
    messages = scenarist_messages(payload.brief, skeleton, headers, payload.style_hint,
                                  camera_lines(payload.camera, len(headers)))
    images = [r for r in payload.references if r.image_jpeg_base64]
    if images:
        content = [{"type": "text", "text": messages[1]["content"] + "\n\nREFERENCE PICTURES (describe what you see):"}]
        for ref in images[:9]:
            base64.b64decode(ref.image_jpeg_base64, validate=True)
            content += [{"type": "text", "text": f"<Picture {ref.number}> ({ref.role}{', ' + ref.owner if ref.owner else ''}):"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + ref.image_jpeg_base64}}]
        messages[1] = {"role": "user", "content": content}
    return messages, skeleton, headers


def write_ir(payload: ScenaristRequest, director: LocalQwenDirector | None = None) -> dict:
    messages, skeleton, headers = ir_messages(payload)
    director = director or LocalQwenDirector()
    started = time.monotonic()
    with planning_lock():
        if director._is_sleeping():
            director._runtime_control("wake_up")
        try:
            text = _chat(director, messages)
            problems = validate_ir(text, skeleton, headers)
            if problems:
                # One correction round with the exact problems; then return whatever we have, flagged.
                text = _chat(director, messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "Fix these format problems and output the six sections again:\n- "
                     + "\n- ".join(problems)}])
                problems = validate_ir(text, skeleton, headers)
        finally:
            director._runtime_control("sleep?level=1")
    log.info("Scenarist seconds=%.1f problems=%d", time.monotonic() - started, len(problems))
    return {"prompt": text, "problems": problems, "skeleton": skeleton.subject_lines,
            "task_types": skeleton.task_types, "shot_headers": headers}


def make_router(comfy) -> APIRouter:
    router = APIRouter()

    @router.post("/api/scenarist")
    async def scenarist(payload: ScenaristRequest) -> dict:
        try:
            camera_lines(payload.camera, len(payload.cut_durations))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not await asyncio.to_thread(director_installed):
            raise HTTPException(503, DIRECTOR_MISSING)
        try:
            await comfy.release_models_if_idle()
        except RuntimeError as exc:
            raise HTTPException(409, "Идёт рендер: сценарист освободится после него") from exc
        try:
            return await asyncio.to_thread(write_ir, payload)
        except (RuntimeError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise HTTPException(503, f"Сценарист недоступен: {exc}") from exc

    return router
