"""Validated, local-Qwen edits to an existing Scene Workspace board."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .director import DIRECTOR_MISSING, LocalQwenDirector, director_installed
from .qwen_lifecycle import planning_lock
from .scene_planner import ScenePlanReference


EDIT_SHOT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "action", "duration", "framing", "lens_mm", "camera_motion", "start_frame"],
    "properties": {
        "title": {"type": "string", "maxLength": 70},
        "action": {"type": "string", "maxLength": 700},
        "duration": {"type": "integer", "minimum": 10, "maximum": 15},
        "framing": {"type": "string", "enum": ["wide", "medium", "close", "insert"]},
        "lens_mm": {"type": "integer", "minimum": 18, "maximum": 135},
        "camera_motion": {"type": "string", "enum": ["static", "dolly", "tracking", "truck", "orbit", "pan", "tilt"]},
        "start_frame": {"type": "string", "enum": ["new_keyframe", "previous_end"]},
    },
}
EDIT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["operations", "summary"],
    "properties": {
        "summary": {"type": "string", "maxLength": 500},
        "operations": {
            "type": "array", "minItems": 1, "maxItems": 20,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["op"],
                "properties": {
                    "op": {"type": "string", "enum": ["add_after", "update"]},
                    "shot_id": {"type": "string", "maxLength": 100},
                    "after_shot_id": {"type": ["string", "null"], "maxLength": 100},
                    "shot": EDIT_SHOT_SCHEMA,
                    "changes": {"type": "object", "maxProperties": 7},
                },
            },
        },
    },
}


class CurrentShot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=70)
    action: str = Field(default="", max_length=700)
    duration: int = Field(ge=4, le=15)
    framing: str = Field(default="medium", max_length=20)
    lens_mm: int = Field(default=50, ge=18, le=135)
    camera_motion: str = Field(default="", max_length=120)
    start_frame: str = Field(default="new_keyframe", pattern="^(new_keyframe|previous_end)$")


class SceneBoardEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=8, max_length=2000)
    shots: list[CurrentShot] = Field(min_length=1, max_length=50)
    selected_shot_ids: list[str] = Field(default_factory=list, max_length=50)
    style: str = Field(default="", max_length=300)
    references: list[ScenePlanReference] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_selection(self):
        ids = [shot.id for shot in self.shots]
        if len(set(ids)) != len(ids):
            raise ValueError("ID шотов должны быть уникальными")
        if len(set(self.selected_shot_ids)) != len(self.selected_shot_ids) or set(self.selected_shot_ids) - set(ids):
            raise ValueError("Выбранные шоты не совпадают с доской")
        return self


class EditedShot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=70)
    action: str = Field(min_length=1, max_length=700)
    duration: int = Field(ge=10, le=15)
    framing: str = Field(pattern="^(wide|medium|close|insert)$")
    lens_mm: int = Field(ge=18, le=135)
    camera_motion: str = Field(pattern="^(static|dolly|tracking|truck|orbit|pan|tilt)$")
    start_frame: str = Field(pattern="^(new_keyframe|previous_end)$")


def validate_scene_board_edit(candidate: dict, shot_ids: set[str], first_shot_id: str | None = None) -> dict:
    if not isinstance(candidate, dict) or set(candidate) != {"operations", "summary"}:
        raise ValueError("Qwen вернула неполную правку доски")
    summary = candidate["summary"]
    operations = candidate["operations"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
        raise ValueError("Недопустимое описание правки")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 20:
        raise ValueError("Нужно от 1 до 20 операций")
    output = []
    for index, operation in enumerate(operations, 1):
        if not isinstance(operation, dict) or operation.get("op") not in {"add_after", "update"}:
            raise ValueError(f"Операция {index}: неизвестный тип")
        if operation["op"] == "update":
            if (set(operation) != {"op", "shot_id", "changes"} or not isinstance(operation["shot_id"], str)
                    or operation["shot_id"] not in shot_ids):
                raise ValueError(f"Операция {index}: шот не найден")
            changes = operation["changes"]
            allowed = {"title", "action", "duration", "framing", "lens_mm", "camera_motion", "start_frame"}
            if not isinstance(changes, dict) or not changes or set(changes) - allowed:
                raise ValueError(f"Операция {index}: недопустимые поля правки")
            if any(key in changes and (not isinstance(changes[key], str) or not changes[key].strip())
                   for key in ("title", "action", "camera_motion")):
                raise ValueError(f"Операция {index}: текстовые поля не могут быть пустыми")
            normalized = {"title":"Шот", "action":"Action", "duration":10, "framing":"medium", "lens_mm":50,
                          "camera_motion":"static", "start_frame":"new_keyframe"}
            normalized.update(changes)
            edited = EditedShot(**normalized)
            if "action" in changes and re.search(r"[А-Яа-яЁё]", changes["action"]):
                raise ValueError(f"Операция {index}: действие для H3 должно быть на английском")
            if operation["shot_id"] == first_shot_id and changes.get("start_frame") == "previous_end":
                raise ValueError("Первый шот доски должен начинаться с ключевого кадра")
        else:
            if set(operation) != {"op", "after_shot_id", "shot"}:
                raise ValueError(f"Операция {index}: неполное добавление шота")
            anchor = operation["after_shot_id"]
            if anchor is not None and (not isinstance(anchor, str) or anchor not in shot_ids):
                raise ValueError(f"Операция {index}: место вставки не найдено")
            if not isinstance(operation["shot"], dict):
                raise ValueError(f"Операция {index}: карточка нового шота повреждена")
            shot = EditedShot(**operation["shot"])
            if re.search(r"[А-Яа-яЁё]", shot.action):
                raise ValueError(f"Операция {index}: действие для H3 должно быть на английском")
            if anchor is None and not shot_ids and shot.start_frame == "previous_end":
                raise ValueError("Первый шот доски должен начинаться с ключевого кадра")
        output.append(operation)
    return {"summary": summary.strip(), "operations": output}


def board_edit_messages(payload: SceneBoardEditRequest) -> list[dict]:
    """Chat messages of one board edit, shared by Qwen and the Studio agent."""
    shots = [shot.model_dump() for shot in payload.shots]
    references = [reference.model_dump() for reference in payload.references]
    messages = [
        {"role": "system", "content": (
            "You are STUDIO's local AI film director editing an existing storyboard. "
            "Return only precise board operations matching the JSON schema. Never rewrite the entire board. "
            "Only add_after and update are allowed. Keep every unmentioned shot exactly unchanged. "
            "Use the existing shot IDs exactly; do not invent IDs. Apply the user's stated exclusions literally. "
            "For add_after, null means append and a real shot ID means insert immediately after it. "
            "Each new shot has one action, a 10–15 second duration, supported framing and a start frame. "
            "Use only supplied characters and references. Write summary and titles in Russian, actions in English.")},
        {"role": "user", "content": json.dumps({
            "command": payload.command, "current_shots": shots,
            "selected_shot_ids": payload.selected_shot_ids, "style": payload.style,
            "references": references,
            "rules": "Do not output any operation for a shot outside the requested change. Avoid unrelated duration, action, continuity or camera edits.",
        }, ensure_ascii=False)},
    ]
    return messages


def write_scene_board_edit(payload: SceneBoardEditRequest, director: LocalQwenDirector | None = None) -> dict:
    director = director or LocalQwenDirector()
    messages = board_edit_messages(payload)
    body = {"model": director.model, "messages": messages, "temperature": 0.2, "max_tokens": 2200,
            "structured_outputs": {"json": EDIT_SCHEMA, "disable_any_whitespace": True}}
    with planning_lock():
        if director._is_sleeping():
            director._runtime_control("wake_up")
        try:
            result, error, _ = director._post(body)
            if error:
                raise RuntimeError(f"Qwen HTTP {error[0]}: {error[1]}")
            choice = (result.get("choices") or [{}])[0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("Qwen обрезала правку; сократи команду")
            raw = (choice.get("message") or {}).get("content")
            candidate = json.loads(raw) if isinstance(raw, str) else raw
            return validate_scene_board_edit(candidate, {shot.id for shot in payload.shots}, payload.shots[0].id)
        finally:
            director._runtime_control("sleep?level=1")


def make_router(comfy) -> "APIRouter":
    from fastapi import APIRouter, HTTPException
    import asyncio
    import urllib.error

    router = APIRouter()

    @router.post("/api/scene-board-edits")
    async def scene_board_edit(payload: SceneBoardEditRequest) -> dict:
        if not await asyncio.to_thread(director_installed):
            raise HTTPException(503, DIRECTOR_MISSING)
        try:
            await comfy.release_models_if_idle()
        except RuntimeError as exc:
            raise HTTPException(409, "Идёт рендер: правка доски доступна после него") from exc
        try:
            return await asyncio.to_thread(write_scene_board_edit, payload)
        except (RuntimeError, ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise HTTPException(503, f"Правка доски не готова: {exc}") from exc

    return router
