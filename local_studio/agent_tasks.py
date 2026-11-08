"""Studio writing and planning tasks done by the user's own agent (Claude Code / Codex by subscription) instead of
the separate Qwen director on the GPU: «✍ Сценарист», «Создать новую раскадровку», «Подготовить правку».

The task is built by the same code the GPU used (app/scenarist.py, app/scene_planner.py, app/scene_edits.py) and
the answer is checked by the same validators, so the pages and their contracts do not change. Pictures that were
inline in the Qwen messages are written to a temporary folder and handed to the agent as files.
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from pathlib import Path

from .agent_brain import BrainError, BrainRequest, ask, executable
from .agent_chat import CLAUDE_DEFAULT

from app.h3_ir import validate_ir
from app.scenarist import ScenaristRequest, ir_messages
from app.scene_edits import EDIT_SCHEMA, SceneBoardEditRequest, board_edit_messages, validate_scene_board_edit
from app.scene_planner import PLAN_SCHEMA, ScenePlanRequest, scene_plan_messages, validate_scene_plan

TIMEOUT_S = 600


def provider() -> str:
    """H3_BRAIN_PROVIDER, else Claude Code when installed, else Codex."""
    chosen = os.environ.get("H3_BRAIN_PROVIDER", "").strip()
    if chosen in {"claude", "codex"}:
        return chosen
    return "claude" if executable("claude") else "codex"


def flatten(messages: list[dict], folder: Path) -> tuple[str, list[str]]:
    """OpenAI-style chat messages → one prompt; inline data: pictures become files referred to as [image N]."""
    parts, images = [], []
    for message in messages:
        content = message["content"]
        chunks = [content] if isinstance(content, str) else []
        for item in content if isinstance(content, list) else []:
            if item.get("type") == "text":
                chunks.append(item["text"])
            elif item.get("type") == "image_url":
                url = item["image_url"]["url"]
                data = base64.b64decode(url.split(",", 1)[1], validate=True)
                path = folder / f"image-{len(images) + 1:02d}.jpg"
                path.write_bytes(data)
                images.append(str(path))
                chunks.append(f"[image {len(images)}: {path}]")
        parts.append(f"{message['role'].upper()}:\n" + "\n".join(chunks))
    if images:
        parts.append("The [image N] files above are attached to this request: open and look at every one of them "
                     "before answering.")
    return "\n\n".join(parts), images


def json_answer(text: str) -> dict:
    """The JSON object of an answer, also when the agent wrapped it in prose or a code fence."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise BrainError(f"Агент ответил не JSON: {text[:300]}") from exc
    if not isinstance(value, dict):
        raise BrainError("Агент ответил не объектом JSON")
    return value


def task_options() -> dict:
    """Helpers receive complete inputs; never reread the project just to transform a payload.

    Optional settings affect new helper calls only, not the user's persistent agent chats.
    No cheaper model or reduced reasoning is selected silently.
    """
    chosen = provider()
    return {"provider": chosen,
            "model": os.environ.get("H3_BRAIN_MODEL", "").strip() or (CLAUDE_DEFAULT if chosen == "claude" else ""),
            "effort": os.environ.get("H3_BRAIN_EFFORT", "").strip(), "read_repo": False}


async def _ask(prompt: str, images: list[str]) -> str:
    result = await ask(BrainRequest(**task_options(), prompt=prompt, images=images, timeout_s=TIMEOUT_S))
    return result["text"]


async def write_ir(payload: ScenaristRequest) -> dict:
    """«✍ Сценарист»: the official six-section H3 prompt, with one correction round like the Qwen version."""
    messages, skeleton, headers = ir_messages(payload)
    with tempfile.TemporaryDirectory(prefix="h3-agent-task-") as tmp:
        prompt, images = flatten(messages, Path(tmp))
        prompt += "\n\nOutput only the six sections of the prompt, nothing before or after them."
        text = (await _ask(prompt, images)).strip()
        problems = validate_ir(text, skeleton, headers)
        if problems:
            text = (await _ask(prompt + "\n\nYOUR PREVIOUS ANSWER:\n" + text + "\n\nFix these format problems and "
                               "output the six sections again:\n- " + "\n- ".join(problems), images)).strip()
            problems = validate_ir(text, skeleton, headers)
    return {"prompt": text, "problems": problems, "skeleton": skeleton.subject_lines,
            "task_types": skeleton.task_types, "shot_headers": headers, "writer": provider()}


async def write_scene_plan(payload: ScenePlanRequest) -> dict:
    """«Создать новую раскадровку»: a causal scene plan, validated by the same rules as before."""
    with tempfile.TemporaryDirectory(prefix="h3-agent-task-") as tmp:
        prompt, images = flatten(scene_plan_messages(payload), Path(tmp))
        prompt += "\n\nReturn one JSON object only, matching this JSON Schema:\n" + json.dumps(PLAN_SCHEMA, ensure_ascii=False)
        candidate = json_answer(await _ask(prompt, images))
    owners = {ref.owner.casefold() for ref in payload.references if ref.kind == "image" and ref.owner}
    plan = validate_scene_plan(candidate, payload.target_seconds, payload.shot_count, owners)
    if payload.source_frames:
        plan.update(source_driven=True, evidence={"sampled_frames": len(payload.source_frames),
                                                  "source_cuts": payload.source_cuts, "audio_analyzed": False})
    return plan


async def write_board_edit(payload: SceneBoardEditRequest) -> dict:
    """«Подготовить правку»: precise operations on the existing board."""
    with tempfile.TemporaryDirectory(prefix="h3-agent-task-") as tmp:
        prompt, images = flatten(board_edit_messages(payload), Path(tmp))
        prompt += "\n\nReturn one JSON object only, matching this JSON Schema:\n" + json.dumps(EDIT_SCHEMA, ensure_ascii=False)
        candidate = json_answer(await _ask(prompt, images))
    return validate_scene_board_edit(candidate, {shot.id for shot in payload.shots}, payload.shots[0].id)
