"""Qwen scene planning before any costly H3 generation."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import urllib.error

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from .director import DIRECTOR_MISSING, LocalQwenDirector, director_installed
from .qwen_lifecycle import planning_lock


SHOT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "purpose", "action", "first_frame", "characters", "duration", "framing", "lens_mm", "camera_motion",
                 "start_frame", "state_before", "state_after"],
    "properties": {
        "title": {"type": "string", "maxLength": 70},
        "purpose": {"type": "string", "maxLength": 180},
        "action": {"type": "string", "maxLength": 500},
        "first_frame": {"type": "string", "maxLength": 300},
        "characters": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
        "duration": {"type": "integer", "minimum": 10, "maximum": 15},
        "framing": {"type": "string", "enum": ["wide", "medium", "close", "insert"]},
        "lens_mm": {"type": "integer", "minimum": 18, "maximum": 135},
        "camera_motion": {"type": "string", "maxLength": 120},
        "start_frame": {"type": "string", "enum": ["new_keyframe", "previous_end"]},
        "state_before": {"type": "string", "maxLength": 300},
        "state_after": {"type": "string", "maxLength": 300},
    },
}
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["dramatic_arc", "visual_rule", "shots"],
    "properties": {
        "dramatic_arc": {"type": "string", "maxLength": 700},
        "visual_rule": {"type": "string", "maxLength": 300},
        "shots": {"type": "array", "minItems": 2, "maxItems": 12, "items": SHOT_SCHEMA},
    },
}


class SourceFrame(BaseModel):
    video_number: int = Field(ge=1, le=3)
    seconds: float = Field(ge=0, le=180, allow_inf_nan=False)
    jpeg_base64: str = Field(max_length=2_700_000)


class ScenePlanReference(BaseModel):
    asset_id: str = Field(default="", max_length=100)
    number: int | None = Field(default=None, ge=1, le=24)
    kind: str = Field(pattern="^(image|video|audio)$")
    role: str = Field(default="", max_length=120)
    owner: str = Field(default="", max_length=120)
    name: str = Field(default="", max_length=120)


class ScenePlanRequest(BaseModel):
    idea: str = Field(min_length=20, max_length=20000)
    target_seconds: int = Field(default=40, ge=20, le=180)
    style: str = Field(default="", max_length=300)
    source_frames: list[SourceFrame] = Field(default_factory=list, max_length=24)
    source_cuts: dict[str, list[float]] = Field(default_factory=dict)
    references: list[ScenePlanReference] = Field(default_factory=list, max_length=12)
    # «Шотов»: exact number of shots the user asked for; None = the director decides (2–12).
    shot_count: int | None = Field(default=None, ge=2, le=12)

    @model_validator(mode="after")
    def _count_fits_duration(self):
        if self.shot_count is not None and not 10 * self.shot_count <= self.target_seconds <= 15 * self.shot_count:
            raise ValueError(f"{self.shot_count} шотов по 10–15 с не дают {self.target_seconds} с: "
                             f"нужно от {10 * self.shot_count} до {15 * self.shot_count} с")
        return self


def validate_scene_plan(candidate: dict, target_seconds: int, shot_count: int | None = None,
                        reference_owners: set[str] | None = None) -> dict:
    if not isinstance(candidate, dict) or set(candidate) != set(PLAN_SCHEMA["required"]):
        raise ValueError("Qwen вернула неполный план сцены")
    for field, limit in (("dramatic_arc", 700), ("visual_rule", 300)):
        if not isinstance(candidate[field], str) or not candidate[field].strip() or len(candidate[field]) > limit:
            raise ValueError(f"Недопустимое поле {field}")
    shots = candidate["shots"]
    if not isinstance(shots, list) or not 2 <= len(shots) <= 12:
        raise ValueError("Нужно от 2 до 12 шотов")
    if shot_count is not None and len(shots) != shot_count:
        raise ValueError(f"Qwen дала {len(shots)} шотов вместо {shot_count}")
    streak = 0
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict) or set(shot) != set(SHOT_SCHEMA["required"]):
            raise ValueError(f"Шот {index + 1}: неполная структура")
        for name, spec in SHOT_SCHEMA["properties"].items():
            value = shot[name]
            if name == "characters":
                if not isinstance(value, list) or len(value) > 8 or any(not isinstance(x, str) or not x.strip() for x in value):
                    raise ValueError(f"Шот {index + 1}: недопустимый состав героев")
                if len({x.casefold() for x in value}) != len(value):
                    raise ValueError(f"Шот {index + 1}: герой указан дважды")
                unknown = [x for x in value if reference_owners is not None and x.casefold() not in reference_owners]
                if unknown:
                    raise ValueError(f"Шот {index + 1}: герои без фото в референсах: {', '.join(unknown)}")
                continue
            if spec["type"] == "string" and (not isinstance(value, str) or not value.strip() or len(value) > spec.get("maxLength", 9999)):
                raise ValueError(f"Шот {index + 1}: недопустимое поле {name}")
            if spec["type"] == "integer" and (not isinstance(value, int) or isinstance(value, bool) or not spec["minimum"] <= value <= spec["maximum"]):
                raise ValueError(f"Шот {index + 1}: недопустимое поле {name}")
            if "enum" in spec and value not in spec["enum"]:
                raise ValueError(f"Шот {index + 1}: недопустимое значение {name}")
        if shot["state_before"].strip().casefold() == shot["state_after"].strip().casefold():
            raise ValueError(f"Шот {index + 1}: состояние до и после одинаково — в шоте нет видимого результата")
        if reference_owners:
            text = " ".join(str(shot[key]) for key in ("action", "first_frame", "purpose"))
            named = {owner for owner in reference_owners
                     if re.search(rf"(?<!\w){re.escape(owner)}(?!\w)", text, re.IGNORECASE)}
            undeclared = named - {name.casefold() for name in shot["characters"]}
            if undeclared:
                raise ValueError(f"Шот {index + 1}: герой назван в действии, но отсутствует в составе: {', '.join(sorted(undeclared))}")
        if re.search(r"[А-Яа-яЁё]", shot["action"]):
            raise ValueError(f"Шот {index + 1}: действие для H3 должно быть на английском")
        if index == 0 and shot["start_frame"] != "new_keyframe":
            raise ValueError("Первый шот должен начать новый ключевой кадр")
        streak = streak + 1 if shot["start_frame"] == "previous_end" else 0
        if streak > 3:
            raise ValueError("Более трёх продолжений подряд — нужен новый ключевой кадр")
    total = sum(shot["duration"] for shot in shots)
    if total != target_seconds:
        raise ValueError(f"План длится {total} с вместо {target_seconds} с")
    return {**candidate, "total_seconds": total}


def scene_plan_messages(payload: ScenePlanRequest) -> list[dict]:
    """Chat messages of one scene plan (source video frames inline), shared by Qwen and the Studio agent."""
    refs = [ref.model_dump() for ref in payload.references]
    messages = [
        {"role": "system", "content": (
            "You are a film scene director. Design one causal scene, not a list of generic prompts. "
            "Each shot has one observable action and a story state before/after. Establish geography when needed, "
            "vary framing for a reason, use a cut/new keyframe at changes of time/place or after at most 3 continuations. "
            "Use only the provided characters, props and locations; do not invent claims about reference images. "
            "For each shot list its visible characters by the exact owner names of the supplied character pictures. "
            "The first_frame is a still image before the shot's action, not the action itself. "
            "The first shot needs a new_keyframe. Keep the same visual rule through the scene. "
            "Return JSON only, matching the schema. Write shot.action in English as a concrete ordered performance "
            "with positions, reactions, physical contact and a visible end state; use roughly 60–80 words per shot. "
            "Write display fields in Russian. Do not put Russian text in shot.action.")},
        {"role": "user", "content": json.dumps({"idea": payload.idea, "target_seconds": payload.target_seconds,
            "style": payload.style, "references": refs,
            "requirements": (f"exactly {payload.shot_count} shots" if payload.shot_count else "2–12 shots") + ", each 10–15 s, total exactly target_seconds; preserve the supplied story and action order. For motion video references, successive shots use consecutive source segments: do not reorder choreography."}, ensure_ascii=False)},
    ]
    if payload.source_frames:
        messages[0]["content"] += (
            " The timestamped source frames are visual evidence. Reconstruct the observed event order, interactions, "
            "camera and scene changes; apply only the user's requested character/costume replacements. "
            "Do not invent events between samples or dialogue from silent frames. Prefer detected source cuts "
            "as shot boundaries when compatible with integer durations of 10–15 seconds. Consecutive shot durations "
            "define consecutive source intervals from zero; describe only events in each interval. "
            "Do not turn this into a new story. Write action in English, other display fields in Russian.")
        content = [{"type":"text", "text":messages[1]["content"]},
                   {"type":"text", "text":"Detected scene cuts (seconds): " + json.dumps(payload.source_cuts)}]
        for frame in payload.source_frames:
            raw_frame = base64.b64decode(frame.jpeg_base64, validate=True)
            if not raw_frame.startswith(b"\xff\xd8") or len(raw_frame) > 2_000_000:
                raise ValueError("Недопустимый кадр видеореференса")
            content.extend([{"type":"text", "text":f"<Video {frame.video_number}> at {frame.seconds:.3f} seconds:"},
                            {"type":"image_url", "image_url":{"url":"data:image/jpeg;base64," + frame.jpeg_base64}}])
        messages[1]["content"] = content
    return messages


def write_scene_plan(payload: ScenePlanRequest, director: LocalQwenDirector | None = None) -> dict:
    director = director or LocalQwenDirector()
    messages = scene_plan_messages(payload)
    body = {"model": director.model, "messages": messages, "temperature": 0.35, "max_tokens": 4200,
            "structured_outputs": {"json": PLAN_SCHEMA, "disable_any_whitespace": True}}
    with planning_lock():
        if director._is_sleeping():
            director._runtime_control("wake_up")
        try:
            result, error, _ = director._post(body)
            if error:
                raise RuntimeError(f"Qwen HTTP {error[0]}: {error[1]}")
            choice = (result.get("choices") or [{}])[0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("Qwen обрезала план; попробуй короче описать идею")
            raw = (choice.get("message") or {}).get("content")
            candidate = json.loads(raw) if isinstance(raw, str) else raw
            owners = {ref.owner.casefold() for ref in payload.references if ref.kind == "image" and ref.owner}
            plan = validate_scene_plan(candidate, payload.target_seconds, payload.shot_count, owners)
            if payload.source_frames:
                plan.update(source_driven=True, evidence={"sampled_frames":len(payload.source_frames),
                    "source_cuts":payload.source_cuts, "audio_analyzed":False})
            return plan
        finally:
            director._runtime_control("sleep?level=1")


def make_router(comfy) -> APIRouter:
    router = APIRouter()

    @router.post("/api/scene-plans")
    async def scene_plan(payload: ScenePlanRequest) -> dict:
        if not await asyncio.to_thread(director_installed):
            raise HTTPException(503, DIRECTOR_MISSING)
        try:
            await comfy.release_models_if_idle()
        except RuntimeError as exc:
            raise HTTPException(409, "Идёт рендер: планирование доступно после него") from exc
        try:
            return await asyncio.to_thread(write_scene_plan, payload)
        except (RuntimeError, ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise HTTPException(503, f"План сцены не готов: {exc}") from exc

    return router
