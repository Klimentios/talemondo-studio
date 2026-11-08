"""Local Qwen Director contract and deterministic MiniMax H3 plan compiler."""
from __future__ import annotations
from .motion_reference_policy import ROLE_INSTRUCTION as MOTION_CAMERA_ROLE_INSTRUCTION, MOTION_ONLY_INSTRUCTION

import base64
import copy
import logging
import time
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .camera_guide_vendor.camera import focal_length_to_fov
from .object_info import input_options


PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "references", "style", "additional_direction",
                 "control", "camera_state", "guide_frames", "quality_intent", "audio_intent"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "references": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["asset_id", "role", "fidelity", "visual_evidence"],
            "properties": {
                "asset_id": {"type": "string"},
                "role": {"type": "string", "enum": ["identity", "wardrobe", "prop", "style", "lighting", "color", "scene", "keyframe", "motion", "camera", "motion_camera", "structure", "voice", "music", "sfx", "ambience", "source"]},
                "fidelity": {"type": "string", "enum": ["fully_preserved", "partly_preserved", "attribute_transfer", "weak_inspiration"]},
                "visual_evidence": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 180}},
            },
        }},
        "style": {"type": "string", "enum": ["realism", "animation_2d", "animation_3d", "illustration"]},
        "additional_direction": {"type": "string", "maxLength": 3000},
        # Structural ControlNet was removed; the field stays in the plan contract (always "none")
        # so the Qwen JSON format and its prompt are unchanged.
        "control": {"type": "object", "additionalProperties": False,
            "required": ["type", "source_asset_id", "people", "strength"],
            "properties": {
                "type": {"type": "string", "enum": ["none"]},
                "source_asset_id": {"type": "null"},
                "people": {"type": "integer", "minimum": 1, "maximum": 4},
                "strength": {"type": "number", "minimum": 0.0, "maximum": 2.0},
            }},
        "camera_state": {"anyOf": [
            {"type": "null"},
            {"type": "object", "additionalProperties": False,
             "required": ["version", "keys"], "properties": {
                "version": {"type": "integer", "enum": [1, 2]},
                "keys": {"type": "array", "minItems": 1, "maxItems": 12, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["t", "orbit", "elevation", "distance", "target", "fov"],
                    "properties": {
                        "t": {"type": "number", "minimum": 0, "maximum": 15},
                        "orbit": {"type": "number", "minimum": -1440, "maximum": 1440},
                        "elevation": {"type": "number", "minimum": -85, "maximum": 85},
                        "distance": {"type": "number", "minimum": 0.2, "maximum": 50},
                        "target": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                        "fov": {"type": "number", "minimum": 10, "maximum": 120},
                        "focal_length_mm": {"type": "number", "minimum": 12, "maximum": 135},
                        "aperture_f": {"type": "number", "minimum": 1.2, "maximum": 22},
                        "focus_distance_m": {"type": "number", "minimum": 0.3, "maximum": 50},
                    },
                }},
            }},
        ]},
        "guide_frames": {"type": "array", "maxItems": 9, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["asset_id", "frame_idx"],
            "properties": {"asset_id": {"type": "string"}, "frame_idx": {"type": "integer", "minimum": 0}},
        }},
        "quality_intent": {"type": "string", "enum": ["draft", "final"]},
        "audio_intent": {"type": "string", "enum": ["native_stereo_effects_music", "preserve_reference_audio", "silent"]},
    },
}

# Upper cap on the Director's output. A valid plan never needs it: the request schema bounds
# every free-text field (see DIRECTOR_FULL_LIMITS), so max_tokens is computed from those bounds
# (director_output_budget). Hitting the cap meant degenerate output — 2026-09-30 jobs 39c92c6b and
# 73033654 stopped at finish_reason=length with 19 888 and 257 502 characters, i.e. runs of
# whitespace tokens, while a valid plan in the Studio DB is 1.6–4.5 kB of JSON.
DIRECTOR_MAX_OUTPUT_TOKENS = 8192

# vLLM serves Qwen3-VL-8B with --max-model-len 32768 (deploy/h3-qwen-director.conf). Prompt text,
# every attached frame and the reserved output (max_tokens) must fit together, otherwise vLLM
# rejects the request with HTTP 400 before generating anything.
DIRECTOR_MAX_MODEL_LEN = int(os.environ.get("H3_DIRECTOR_MAX_MODEL_LEN")
                             or os.environ.get("H3_QWEN_MAX_MODEL_LEN") or 32768)
DIRECTOR_INPUT_MARGIN_TOKENS = 1024  # chat template, vision placeholders and estimate error

# What Qwen may write. additional_direction is followed by CINEMATOGRAPHY CONTROL (at most
# 622 characters) inside the 3000-character plan contract, so anything past ~2300 characters
# was cut by apply_cinematography_policy anyway.
DIRECTOR_FULL_LIMITS = {"direction_chars": 2300, "evidence_items": 6, "evidence_chars": 180}
# The single retry after a failed full attempt: fewer frames, shorter fields, smaller output.
DIRECTOR_COMPACT_LIMITS = {"direction_chars": 800, "evidence_items": 3, "evidence_chars": 120}


def director_output_budget(asset_count: int, *, camera_locked: bool, limits: dict | None = None) -> int:
    """max_tokens for a plan whose free-text fields are bounded by `limits`.

    Counted at 2.5 characters per token (English JSON is ~4; Cyrillic ~2.5) with 25 % headroom,
    so a valid plan always fits while a runaway stops early instead of burning 8k tokens.
    """
    limits = {**DIRECTOR_FULL_LIMITS, **(limits or {})}
    chars = 400 + limits["direction_chars"] + 9 * 50  # fixed fields + direction + guide_frames
    chars += asset_count * (120 + limits["evidence_items"] * (limits["evidence_chars"] + 4))
    if not camera_locked:
        chars += 12 * 260  # camera_state: up to 12 keys
    return max(1024, min(DIRECTOR_MAX_OUTPUT_TOKENS, int(chars / 2.5 * 1.25) + 256))

ROLE_INSTRUCTION = {
    "identity": ("image", "locks the same visible face, head shape, hair/fur, age, skin/fur colors, body proportions, silhouette, hands, costume and carried accessories; preserve recognizable design details"),
    "wardrobe": ("image", "locks costume cut, garment layers, material, colors, accessories and carried props for the assigned character only; do not copy the pictured face, body identity, background, lighting or framing"),
    "prop": ("image", "defines the pictured object and its visible geometry, material and scale only; do not copy the background, people, lighting or framing"),
    "style": ("image", "locks medium, linework/edge quality, shape language, palette, shading, texture and rendering across the shot; do not copy its characters or composition"),
    "lighting": ("image", "defines lighting only: key and fill direction, softness, contrast, color temperature and shadow character; do not copy its subject, location or composition"),
    "color": ("image", "defines color grade and palette only: hue balance, saturation and tonal contrast; do not copy its subject, location or composition"),
    "scene": ("image", "defines location and set dressing only"),
    "keyframe": ("image", "anchors scene composition at a timeline frame"),
    # «Композиция»: a loose layout guide (community ReferenceFacial workflow: "Let <Picture N> be a guide for the
    # general composition of the video only"). Weak on purpose; a timeline-exact frame is «Кадр сцены».
    "composition": ("image", "is a blurred grey layout sketch: it guides only where the figures stand, the shot size and the camera angle; every person keeps the face, hair and clothing of their own picture"),
    "motion": ("video", MOTION_ONLY_INSTRUCTION),
    "camera": ("video", "provides shot size, framing, camera path, perspective, spatial depth, focus changes and timing only; do not copy cast, setting, on-screen text, captions, logos, watermarks or title cards"),
    "motion_camera": ("video", MOTION_CAMERA_ROLE_INSTRUCTION),
    "structure": ("video", "provides spatial structure only"),
    "source": ("video", "provides source-scene context only"),
    "voice": ("audio", "provides voice characteristics only"),
    "music": ("audio", "provides music style and rhythm only"),
    "sfx": ("audio", "provides sound-effect character only"),
    "ambience": ("audio", "provides ambience only"),
}
USER_ONLY_ROLES = ("composition",)  # never proposed by Qwen on its own, see openai_director_payload

IDENTITY_WITH_WARDROBE_INSTRUCTION = (
    "locks the same visible face, head shape, hair/fur, age, skin/fur colors and body proportions; "
    "preserve recognizable design details, while costume and carried accessories come only from their "
    "separately assigned wardrobe reference or the written brief"
)

DIRECTOR_ROLE_MAP = {
    "Авто — определить по содержимому": None,
    "Лицо героя": "identity", "Персонаж целиком": "identity", "Костюм": "wardrobe",
    "Стиль": "style", "Свет": "lighting", "Цвет": "color", "Окружение": "scene", "Предмет": "prop", "Кадр сцены": "keyframe",
    "Композиция": "composition",
    "Исходное видео": "source", "Движение": "motion", "Актёрская игра": "motion", "Камера": "camera",
    "Движение и камера": "motion_camera",
    "Ритм сцены": "structure", "Голос": "voice", "Музыка": "music",
    "Звуковой эффект": "sfx", "Атмосфера": "ambience",
    # Not a Qwen/Ref2VA role: app/main.py removes this video from the references before planning and
    # anchors its last 22 frames at frame 0 (app/continuation.py).
    "Продолжение": "continuation",
}


def resolved_image_only_role(role_override: str | None) -> str:
    """Generic image upload means identity; scene/style uploads stay explicit."""
    return role_override or "identity"


_SHOT_MARKER = re.compile(r"(?im)^\s*\[?shot\s+\d+\]?|^\s*\[?кадр\s+\d+\]?")
_CUT_MARKER = re.compile(r"(?i)\b(?:hard\s+cut|smash\s+cut|match\s+cut|cut\s+to|склейка|резкая\s+склейка)\b")


def cinematography_direction(prompt: str) -> str:
    """Add optical continuity that H3CameraGuide cannot encode physically.

    H3CameraGuide controls a synthetic RGB trajectory (position, target and
    field of view). Lens choice, focus pulls and subject tracking remain H3
    prompt instructions, so keep those rules explicit for every plan.
    """
    multiple_shots = len(_SHOT_MARKER.findall(prompt)) > 1 or bool(_CUT_MARKER.search(prompt))
    shot_rule = (
        "Treat every written shot as a separate edit: reset composition only at the specified cut and keep "
        "screen direction, set geometry and character scale continuous across the edit."
        if multiple_shots else
        "Treat this as one continuous shot unless the user explicitly requests a cut."
    )
    return (
        "CINEMATOGRAPHY CONTROL: " + shot_rule + " "
        "Keep one coherent lens within each shot and never morph the lens: about 35 mm wide, 50 mm medium or 85 mm close-up. "
        "Choose the focus target shot by shot from the named character whose face, reaction or action carries that beat; "
        "do not default to Subject 1 or the nearest foreground person. Transfer focus deliberately when the story "
        "attention changes, and keep the currently attended hero's eyes and face crisp while inactive background "
        "people and distant scenery fall softly out of focus when framing permits. Use a closer shot when a readable "
        "facial reaction is the point of the beat; preserve a requested wide composition when it is not. Keep focus "
        "stable within each beat with no pumping; rack focus only when explicitly requested and land on the named target. "
        "Preserve horizon, headroom, eyeline, subject scale "
        "and set geometry. Ease camera starts and stops; avoid warping, snap zooms and unmotivated reframing."
    )


def camera_state_from_prompt(prompt: str, frame_count: int) -> dict | None:
    """Build a conservative guide for one explicit continuous camera move.

    A single synthetic trajectory cannot represent edits. Multi-shot prompts
    therefore stay prompt-directed rather than receiving a misleading guide.
    """
    if len(_SHOT_MARKER.findall(prompt)) > 1 or _CUT_MARKER.search(prompt):
        return None
    text = prompt.lower()
    duration = max(0.0, (frame_count - 1) / 24.0)
    if duration <= 0:
        return None
    start = {"t": 0.0, "orbit": 0.0, "elevation": 0.0, "distance": 4.5,
             "target": [0.0, 1.0, 0.0], "fov": 45.0}
    end = dict(start)
    end["target"] = list(start["target"])
    end["t"] = round(duration, 4)
    matched = False
    lens = 85.0 if re.search(r"\bclose[ -]?up\b|\bкрупн(?:ый|ом)\s+план", text) else 35.0 if re.search(
        r"\bwide\b|\bestablishing\b|\bобщ(?:ий|ем)\s+план", text) else 50.0
    aperture = 2.8
    fov = focal_length_to_fov(lens)
    start.update({"fov": fov, "focal_length_mm": lens, "aperture_f": aperture,
                  "focus_distance_m": start["distance"]})
    end.update({"fov": fov, "focal_length_mm": lens, "aperture_f": aperture,
                "focus_distance_m": end["distance"]})
    if re.search(r"\b(?:doll(?:y|ies)|push(?:es)?)[ -]?in\b|\bslow(?:ly)?\s+in\b|\bнаезд\b|\bприближа(?:ется|й|ться)\b", text):
        start["distance"], end["distance"] = 5.5, 3.2
        start["focus_distance_m"], end["focus_distance_m"] = 5.5, 3.2
        matched = True
    elif re.search(r"\b(?:doll(?:y|ies)|pull(?:s)?)[ -]?(?:out|back)\b|\bpullback\b|\bотъезд\b|\bотдаля(?:ется|й|ться)\b", text):
        start["distance"], end["distance"] = 3.2, 5.5
        start["focus_distance_m"], end["focus_distance_m"] = 3.2, 5.5
        matched = True
    if re.search(r"\borbit(?:s|ing)?\s+(?:right|clockwise)\b|\borbit\s+clockwise\b|\bобл[её]т\s+вправо\b", text):
        end["orbit"] = 30.0
        matched = True
    elif re.search(r"\borbit(?:s|ing)?\s+(?:left|counterclockwise)\b|\bобл[её]т\s+влево\b", text):
        end["orbit"] = -30.0
        matched = True
    elif re.search(r"\borbit(?:s|ing)?\b|\bобл[её]т\b", text):
        end["orbit"] = 25.0
        matched = True
    if re.search(r"\bcrane\s+up\b|\bboom\s+up\b|\bкамера\s+поднимается\b", text):
        end["elevation"] = 15.0
        matched = True
    elif re.search(r"\bcrane\s+down\b|\bboom\s+down\b|\bкамера\s+опускается\b", text):
        end["elevation"] = -15.0
        matched = True
    if re.search(r"\brack\s+focus\b|\bfocus\s+(?:pull|transfer)\b|\bперевод\s+фокус", text):
        start["focus_distance_m"], end["focus_distance_m"] = 2.0, 6.0
        matched = True
    return {"version": 2, "keys": [start, end]} if matched else None


def apply_cinematography_policy(plan: dict, prompt: str, frame_count: int) -> dict:
    """Apply deterministic safeguards after either Qwen planning path."""
    plan = dict(plan)
    direction = plan.get("additional_direction", "").strip()
    policy = cinematography_direction(prompt)
    available = 3000 - len(policy) - 2
    if len(direction) > available:
        direction = direction[:available].rsplit(".", 1)[0].strip() or direction[:available].strip()
    plan["additional_direction"] = "\n\n".join(item for item in (direction, policy) if item)
    if (len(_SHOT_MARKER.findall(prompt)) > 1 or _CUT_MARKER.search(prompt)
            or ("<Video " in prompt and re.search(r"(?i)source camera|camera reference|исходн(?:ая|ую) камер", prompt))):
        plan["camera_state"] = None
    elif plan.get("camera_state") is None:
        plan["camera_state"] = camera_state_from_prompt(prompt, frame_count)
    scene_ids = {row.get("asset_id") for row in plan.get("references", [])
                 if isinstance(row, dict) and row.get("role") in {"scene", "keyframe"}}
    if isinstance(plan.get("guide_frames"), list):
        plan["guide_frames"] = [guide for guide in plan["guide_frames"]
                                if isinstance(guide, dict)
                                and guide.get("asset_id") in scene_ids
                                and type(guide.get("frame_idx")) is int
                                # MiniMaxH3AddGuide: a negative index counts from the end (-1 = last frame).
                                and -frame_count <= guide["frame_idx"] < frame_count]
    return plan


def validate_camera_or_keep_prompt(plan: dict, asset_kinds: dict[str, str], frame_count: int,
                                   explicit_roles: dict[str, str]) -> dict:
    """Discard malformed Qwen camera keys while preserving source camera cues."""
    try:
        return validate_director_plan(plan, asset_kinds, frame_count, explicit_roles)
    except ValueError as exc:
        if plan.get("camera_state") is None or not any(term in str(exc) for term in (
            "ключ камеры", "ключевые кадры камеры", "Ключ камеры", "Оптика или фокус камеры",
        )):
            raise
        plan = dict(plan, camera_state=None)
        return validate_director_plan(plan, asset_kinds, frame_count, explicit_roles)


def required_nodes_for_plan(has_camera: bool, has_guides: bool,
                            style_adapter: str, has_camera_optics: bool = False) -> set[str]:
    required = set()
    if has_camera:
        required.add("H3OperatorCamera" if has_camera_optics else "H3CameraGuide")
    if has_guides:
        required.add("MiniMaxH3AddGuide")
    if style_adapter != "none":
        required.add("LoraLoaderModelOnly")
    return required


def preserve_explicit_camera_state(plan: dict, camera_state: dict | None,
                                   manifest: dict, asset_kinds: dict[str, str],
                                   frame_count: int) -> dict:
    """Keep the Studio trajectory when Qwen supplies the other shot directions.

    A separately selected source-camera video would compete for the same
    camera role. Motion references may coexist because they describe the
    actor rather than another camera path.
    """
    # "Движение и камера" / "Камера": the video drives the camera. Neither the Studio cards nor a trajectory
    # Qwen derived from the card text («dolly-in», «rack focus») may compete with it (audit 2026-10-05).
    video_drives_camera = any(item.get("role") in {"Движение и камера", "Камера"}
                              for item in manifest.get("videos", [])) or any(
        item.get("role") in {"motion_camera", "camera"} for item in plan.get("references", []))
    if camera_state is None:
        return {**plan, "camera_state": None} if video_drives_camera else plan
    if any(item.get("role") == "Движение и камера" for item in manifest.get("videos", [])) or any(
            item.get("role") == "motion_camera" for item in plan.get("references", [])):
        return {**plan, "camera_state": None}
    camera_assets = {item.get("asset_id") for item in manifest.get("videos", [])
                     if item.get("role") == "Камера"}
    camera_assets.update(item.get("asset_id") for item in plan.get("references", [])
                         if item.get("role") == "camera")
    if camera_assets:
        raise ValueError("Явная траектория камеры конфликтует с видео-референсом камеры; выбери один источник движения камеры")
    selected = {**plan, "camera_state": camera_state}
    # This applies the existing schema, keyframe-time and numeric validation
    # before required-node discovery or any upload to ComfyUI.
    validate_director_plan(selected, asset_kinds, frame_count)
    return selected


def missing_model_files(style_adapter: str, node_info: dict) -> list[str]:
    required = []
    adapter_files = {
        "style3d": "3dsrx_minimax_h3_rank16-step00001250.safetensors",
        "cartoon2d": "minimax-h3-rough-2d-cartoon-illustration-1200.safetensors",
        # A/B only (app/ab_variant.py): fal/MiniMax-H3-Realism-People-LoRA.
        "realism_people": "h3-realism-people-t2v-i2v-r2v.safetensors",
        "character_swap": "h3_character_swap_pro4500_1000.safetensors",
        "character_swap_v15": "h3_character_swap_v1.5.safetensors",
        "combat": "wushu_action_h3_lora_v5_3000_pruned.safetensors",
    }
    if style_adapter in adapter_files:
        required.append(("LoraLoaderModelOnly", "lora_name", adapter_files[style_adapter]))
    if style_adapter == "combat":
        required.append(("LoraLoaderModelOnly", "lora_name",
                         "wushu_spatial_physics_clean_3000_pruned.safetensors"))
    missing = []
    for node_type, input_name, filename in required:
        if filename not in input_options(node_info, node_type, input_name):
            missing.append(filename)
    return missing

STYLE_DIRECTION = {
    "realism": ("VISUAL MODE: Photoreal live-action. Keep skin, hair, fabric and environments physically "
                "credible, with controlled highlights, stable identity and no animation shading."),
    "animation_2d": ("VISUAL MODE: Contemporary 2D animated feature. Use deliberate drawn shapes, clean "
                     "consistent linework, designed color regions, readable silhouettes, expressive but "
                     "temporally stable facial acting, and no 1930s rubber-hose look or photoreal textures."),
    "animation_3d": ("VISUAL MODE: Polished 3D animated feature film. Render every character, prop and "
                     "environment in one coherent designed 3D language, with appealing shapes, readable "
                     "silhouettes, expressive stable facial acting, authored materials, cinematic lighting "
                     "and consistent proportions. Do not mix photoreal characters with stylized sets."),
    "illustration": ("VISUAL MODE: Cohesive illustrated animation with intentional shape language, "
                     "controlled palette and consistent surface treatment across all subjects and scenes."),
}


def validate_director_plan(plan: dict, asset_kinds: dict[str, str], frame_count: int,
                           explicit_roles: dict[str, str] | None = None) -> dict:
    explicit_roles = explicit_roles or {}
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("Qwen Director вернул неподдерживаемый план")
    rows = plan.get("references")
    if not isinstance(rows, list):
        raise ValueError("В плане Qwen отсутствуют роли референсов")
    returned: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Некорректное назначение роли референса")
        asset_id, role = row.get("asset_id"), row.get("role")
        if asset_id not in asset_kinds:
            raise ValueError(f"Референс {asset_id!r} не относится к заданию")
        if asset_id in returned:
            raise ValueError(f"Qwen повторно назначил роль референсу {asset_id}")
        role_contract = ROLE_INSTRUCTION.get(role)
        if not role_contract or role_contract[0] != asset_kinds[asset_id]:
            raise ValueError(f"Роль {role!r} не подходит для файла {asset_id}")
        if asset_id in explicit_roles and role != explicit_roles[asset_id]:
            raise ValueError(f"Режиссёр изменил явно выбранную роль файла {asset_id}")
        if row.get("fidelity") not in {"fully_preserved", "partly_preserved", "attribute_transfer", "weak_inspiration"}:
            raise ValueError("Некорректный fidelity референса")
        returned[asset_id] = role
        evidence = row.get("visual_evidence")
        if (not isinstance(evidence, list) or len(evidence) > 6
                or any(not isinstance(item, str) or not item.strip() or len(item) > 180 for item in evidence)):
            raise ValueError("Визуальные детали референса должны быть коротким списком проверяемых наблюдений")
        if asset_kinds[asset_id] == "audio" and evidence:
            raise ValueError("Аудио не должно получать придуманные визуальные признаки")
    if set(returned) != set(asset_kinds):
        raise ValueError("План Qwen должен назначить роль каждому загруженному референсу")

    style = plan.get("style")
    if style not in {"realism", "animation_2d", "animation_3d", "illustration"}:
        raise ValueError("Неизвестный визуальный режим в плане Qwen")
    control = plan.get("control")
    if not isinstance(control, dict) or control.get("type") != "none":
        raise ValueError("Структурный контроль (ControlNet) удалён; план Qwen должен иметь control.type none")
    if control.get("source_asset_id") is not None:
        # Qwen often names the motion video here while leaving control off; with no control
        # node the source is meaningless, so drop it instead of failing the whole job.
        plan = {**plan, "control": {**control, "source_asset_id": None}}

    camera = plan.get("camera_state")
    if camera is not None:
        if (not isinstance(camera, dict) or camera.get("version") not in {1, 2}
                or not isinstance(camera.get("keys"), list) or not 1 <= len(camera["keys"]) <= 12):
            raise ValueError("Некорректные ключевые кадры камеры")
        previous_t = -1.0
        for key in camera["keys"]:
            if not isinstance(key, dict):
                raise ValueError("Некорректный ключ камеры")
            required = {"t", "orbit", "elevation", "distance", "target", "fov"}
            if camera["version"] == 2:
                required.update({"focal_length_mm", "aperture_f", "focus_distance_m"})
            if not required.issubset(key):
                raise ValueError("В ключе камеры отсутствуют параметры")
            values = [key[name] for name in ("t", "orbit", "elevation", "distance", "fov")]
            if any(type(value) not in (int, float) for value in values):
                raise ValueError("Числовые параметры камеры должны быть числами")
            t, orbit, elevation, distance, fov = values
            if (not 0 <= t <= (frame_count - 1) / 24 or t <= previous_t
                    or not -1440 <= orbit <= 1440 or not -85 <= elevation <= 85
                    or not 0.2 <= distance <= 50 or not 10 <= fov <= 120
                    or not isinstance(key["target"], list) or len(key["target"]) != 3
                    or any(type(value) not in (int, float) for value in key["target"])):
                raise ValueError("Ключ камеры вне допустимого диапазона или времени")
            if camera["version"] == 2:
                optics = [key[name] for name in ("focal_length_mm", "aperture_f", "focus_distance_m")]
                if (any(type(value) not in (int, float) for value in optics)
                        or not 12 <= optics[0] <= 135 or not 1.2 <= optics[1] <= 22
                        or not 0.3 <= optics[2] <= 50):
                    raise ValueError("Оптика или фокус камеры вне допустимого диапазона")
            previous_t = t
        if any(key.get("t", frame_count) > (frame_count - 1) / 24 for key in camera["keys"]):
            raise ValueError("Ключ камеры находится за концом ролика")
    image_ids = {asset_id for asset_id, kind in asset_kinds.items() if kind == "image"}
    guides = plan.get("guide_frames")
    if not isinstance(guides, list):
        raise ValueError("guide_frames должен быть списком")
    for guide in guides:
        if not isinstance(guide, dict):
            raise ValueError("Некорректный guide frame")
        if (guide.get("asset_id") not in image_ids or type(guide.get("frame_idx")) is not int
                or not -frame_count <= guide["frame_idx"] < frame_count):
            raise ValueError("Guide frame должен ссылаться на фото и кадр внутри ролика")
        if returned[guide["asset_id"]] not in {"scene", "keyframe"}:
            raise ValueError("Guide frame должен использовать референс сцены/ключевого кадра")
    if plan.get("quality_intent") not in {"draft", "final"}:
        raise ValueError("Неизвестный quality intent Qwen")
    if plan.get("audio_intent") not in {"native_stereo_effects_music", "preserve_reference_audio", "silent"}:
        raise ValueError("Неизвестный аудио-режим Qwen")
    if not isinstance(plan.get("additional_direction"), str) or len(plan["additional_direction"]) > 3000:
        raise ValueError("Режиссёрская директива пуста или слишком длинная")
    return plan


# Only an explicit request for a soundless clip counts: "muted bass", "no music yet" or a mute
# character are sound design, not silence.
_SILENCE_REQUEST = re.compile(r"\b(?:silent (?:video|film|movie|scene|clip|shot)|no (?:sound|audio) at all"
                              r"|(?:no|without) (?:any )?(?:sound|audio)(?: track)?\s*(?:[.;,]|$)|mute the (?:audio|sound))"
                              r"|без звука|беззвуч|немое (?:видео|кино)|немой (?:фильм|ролик)", re.IGNORECASE | re.MULTILINE)
_SOUND_REQUEST = re.compile(r"\b(dialogue|says?|said|voice|speaks?|sound|audio|music|score|ambience|sfx)\b"
                            r"|диалог|говорит|голос|звук|музык|реплик", re.IGNORECASE)
_GENERATED_AUDIO_LINE = re.compile(r"^AUDIO DIRECTION:.*$", re.MULTILINE)


def prompt_requests_silence(prompt: str) -> bool:
    return bool(_SILENCE_REQUEST.search(_GENERATED_AUDIO_LINE.sub("", prompt)))


def prompt_requests_sound(prompt: str) -> bool:
    text = _GENERATED_AUDIO_LINE.sub("", prompt)
    return bool(_SOUND_REQUEST.search(text)) and not _SILENCE_REQUEST.search(text)


def compile_director_plan(prompt: str, plan: dict, asset_kinds: dict[str, str], *,
                          duration: int, frame_count: int, requested_steps: int | None = None,
                          requested_cache: str = "safe", available_adapters: set[str] | None = None,
                          video_audio_count: int = 0,
                          identity_with_wardrobe: set[str] | None = None) -> dict:
    plan = validate_director_plan(plan, asset_kinds, frame_count)
    from .cache_policy import CACHE_MODES
    if requested_cache not in CACHE_MODES:
        raise ValueError("Неизвестный режим H3 cache")
    references = plan["references"]
    identity_with_wardrobe = identity_with_wardrobe or set()
    # H3 numbers the soundtracks of reference videos first, then standalone audio
    # (contract.reference_prompt and the ComfyUI node); <Audio 1> may be a video's track.
    order = {"image": 0, "video": 0, "audio": video_audio_count}
    labels = {}
    for asset_id, kind in asset_kinds.items():
        order[kind] += 1
        labels[asset_id] = f"<{ {'image': 'Picture', 'video': 'Video', 'audio': 'Audio'}[kind] } {order[kind]}>"
    # Qwen sometimes copies one description onto several different pictures (two heroes in
    # different costumes both became "brown tabby, white chest", no costume). A copied
    # description is wrong for at least one picture and overrides what H3 sees, so drop it for all.
    evidence_counts: dict[tuple[str, ...], int] = {}
    for entry in references:
        if entry["visual_evidence"] and asset_kinds[entry["asset_id"]] == "image":
            key = tuple(entry["visual_evidence"])
            evidence_counts[key] = evidence_counts.get(key, 0) + 1
    references = [{**entry, "visual_evidence": []}
                  if asset_kinds[entry["asset_id"]] == "image"
                  and evidence_counts.get(tuple(entry["visual_evidence"]), 0) > 1 else entry
                  for entry in references]
    directives = []
    roles: dict[str, str] = {}
    guide_frames = []
    for entry in references:
        asset_id = entry["asset_id"]
        kind = asset_kinds[asset_id]
        tag = labels[asset_id]
        roles[asset_id] = entry["role"]
        role_instruction = ROLE_INSTRUCTION[entry["role"]][1]
        if entry["role"] == "identity" and asset_id in identity_with_wardrobe:
            role_instruction = IDENTITY_WITH_WARDROBE_INSTRUCTION
        directives.append(f"{tag} {role_instruction}; fidelity={entry['fidelity'].replace('_', ' ')}.")
        if entry["visual_evidence"] and entry["role"] not in {"motion", "motion_camera"}:
            directives.append(f"Observed details in {tag} to retain: " + "; ".join(entry["visual_evidence"]) + ".")
    for guide in plan["guide_frames"]:
        tag = labels[guide["asset_id"]]
        number = int(tag.split()[1][:-1])
        guide_frames.append((number, guide["frame_idx"]))
    style_adapter = {"animation_3d": "style3d", "animation_2d": "cartoon2d"}.get(plan["style"], "none")
    if style_adapter not in (available_adapters or set()):
        style_adapter = "none"
    # app/main.py always passes the job's steps; the old "final -> 25" fallback never fired. 24 = contract DEFAULTS.
    steps = requested_steps if requested_steps is not None else 24
    if not 20 <= steps <= 25:
        raise ValueError("Шаги H3 должны быть от 20 до 25")
    cache_mode = requested_cache
    if plan["audio_intent"] == "silent" and not prompt_requests_silence(prompt):
        # User rule (2026-10-08): every clip has sound — voices, effects, ambience, music. Qwen labelled
        # scenes without sound words "silent"; only an explicit «без звука» / "silent video" keeps silence.
        plan = {**plan, "audio_intent": "native_stereo_effects_music"}
    if plan["audio_intent"] == "silent":
        # Studio's own sound blocks ("generate voices", voice casting) contradicted the silent direction.
        prompt = re.sub(r"(?m)^(?:AUDIO DIRECTION|VOICE PERFORMANCE DIRECTION):[^\n]*(?:\n(?!\n)[^\n]*)*\n*", "", prompt)
    direction = plan["additional_direction"].strip()
    style_direction = STYLE_DIRECTION[plan["style"]]
    compiled_prompt = "\n".join([
        style_direction, "REFERENCE ROLE CONTRACT: " + " ".join(directives), direction, prompt.strip(),
    ]).strip()
    if plan["audio_intent"] == "silent":
        compiled_prompt += "\n\nAUDIO DIRECTION: Silent video; no speech, music, ambience or sound effects."
    elif plan["audio_intent"] == "preserve_reference_audio":
        compiled_prompt += "\n\nAUDIO DIRECTION: Preserve the explicitly selected reference audio as requested."
    else:
        compiled_prompt += ("\n\nAUDIO DIRECTION: Generate polished native synchronized stereo sound: clear dialogue only "
                            "when the scene calls for it, matched physical effects, appropriate ambience and "
                            "supporting music. Keep timing and perspective consistent with visible action and camera distance.")
    return {
        "prompt": compiled_prompt, "style": plan["style"], "style_adapter": style_adapter,
        "camera_state": plan["camera_state"],
        "guide_frames": guide_frames, "quality_intent": plan["quality_intent"],
        "audio_intent": plan["audio_intent"], "steps": steps, "cache_mode": cache_mode,
        "roles": roles,
        "visual_evidence": {labels[item["asset_id"]]: item["visual_evidence"]
                             for item in references if item["visual_evidence"]},
        "duration": duration,
    }


def _image_jpeg(data: bytes, filename: str) -> bytes:
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-frames:v", "1", "-vf", "scale=1024:1024:force_original_aspect_ratio=decrease", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False,
    )
    if process.returncode or not process.stdout:
        raise ValueError(f"Не удалось подготовить изображение {filename} для локального Qwen")
    return process.stdout


def _shrink_jpeg(data: bytes, edge: int) -> bytes:
    """Re-encode a Director preview frame with a smaller long edge; keep the original on failure."""
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-frames:v", "1", "-vf",
         f"scale={edge}:{edge}:force_original_aspect_ratio=decrease", "-q:v", "4",
         "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False,
    )
    return process.stdout if process.returncode == 0 and process.stdout[:2] == b"\xff\xd8" else data


_PREVIEW_SCALE = "scale=1024:1024:force_original_aspect_ratio=decrease"
# iPhone HDR (HLG / Dolby Vision 8.4) and HDR10 are PQ/HLG BT.2020: tone-map to SDR BT.709 so
# Qwen sees real colours instead of a washed-out frame. zscale is optional in ffmpeg builds,
# so a plain 8-bit conversion follows as the next attempt.
_HDR_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv,format=yuv420p," + _PREVIEW_SCALE)
_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def _probe_video(source: str) -> tuple[float | None, bool]:
    """Duration (format, else stream) and whether the first video stream is HDR."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "format=duration:stream=duration,color_transfer", "-of", "json", source],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False,
    )
    try:
        info = json.loads(probe.stdout.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        info = {}
    stream = (info.get("streams") or [{}])[0]
    duration = None
    for value in ((info.get("format") or {}).get("duration"), stream.get("duration")):
        try:
            if value is not None and float(value) > 0:
                duration = float(value)
                break
        except (TypeError, ValueError):
            continue
    return duration, stream.get("color_transfer") in _HDR_TRANSFERS


def _video_sample_jpegs(data: bytes, filename: str, count: int = 3) -> list[bytes]:
    """Up to `count` JPEG preview frames for the Director; raises ValueError only if none decode.

    Read from a seekable temp file, not a pipe: phone and editor exports often store the MP4
    index (moov) after the frames, and through a pipe ffmpeg reads the duration but then
    cannot seek back to the frames ("partial file"; IMG_8513.MP4 on 2026-10-04). Each sample
    tries a fast input seek first, then an exact output seek (HEVC open-GOP / sparse keyframes),
    and HDR sources are tone-mapped first. A frame that cannot be read is skipped; when no
    timestamp works the very first frame is tried.
    """
    log = logging.getLogger(__name__)
    with tempfile.TemporaryDirectory(prefix="h3-director-video-") as directory:
        source = os.path.join(directory, "source")
        with open(source, "wb") as handle:
            handle.write(data)
        duration, hdr = _probe_video(source)
        filters = ([_HDR_TONEMAP] if hdr else []) + [_PREVIEW_SCALE, _PREVIEW_SCALE + ",format=yuvj420p"]
        tail = ["-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]

        def grab(seek: list[str], after: list[str]) -> tuple[bytes | None, str]:
            detail = ""
            for vf in filters:
                process = subprocess.run(
                    ["ffmpeg", "-v", "error", *seek, "-i", source, *after, "-frames:v", "1", "-vf", vf, *tail],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40, check=False,
                )
                if process.returncode == 0 and process.stdout[:2] == b"\xff\xd8":
                    return process.stdout, ""
                detail = (process.stderr.decode(errors="replace").strip().splitlines()[-1:] or [""])[0][:160]
            return None, detail

        fractions = (0.2, 0.5, 0.8) if count >= 3 else (0.5,) if count == 1 else (0.25, 0.75)
        samples: list[bytes] = []
        detail = "длительность не определена" if duration is None else ""
        if duration is not None:
            for fraction in fractions:
                stamp = f"{min(max(duration - 0.05, 0.0), duration * fraction):.3f}"
                frame, detail = grab(["-ss", stamp], [])
                if frame is None:
                    frame, detail = grab([], ["-ss", stamp])
                if frame is not None:
                    samples.append(frame)
        if not samples:
            frame, first_detail = grab([], [])
            if frame is not None:
                samples.append(frame)
            detail = first_detail or detail
        if not samples:
            raise ValueError(f"Не удалось прочитать кадры видео {filename} для локального Qwen: {detail}")
        if len(samples) < len(fractions):
            log.warning("Director video %s: %d of %d preview frames decoded (hdr=%s): %s",
                        filename, len(samples), len(fractions), hdr, detail)
    return samples


def openai_director_payload(prompt: str, references: list[dict], *, model: str, frame_count: int,
                            requested_style: str = "auto",
                            emotional_direction: bool = False, camera_locked: bool = False,
                            limits: dict | None = None) -> dict:
    limits = {**DIRECTOR_FULL_LIMITS, **(limits or {})}
    performance_brief = (
        "Translate the emotional intent already present in the user's brief into visible, timed performance beats. "
        "Describe a readable progression when the story contains a change: baseline, trigger, anticipation/build, "
        "peak reaction, hold, and release. Name concrete cues suited to the subject and shot: for people, gaze, "
        "eyelids, brows, cheeks, jaw, mouth, breath and shoulders; for animals, eyes/pupils, ears, whiskers, "
        "muzzle, head, posture, weight and visible tail; for stylized 2D/3D characters, use their drawn or rigged "
        "shape language. For actor replacement, keep the mapped identity while directing the replacement's acting. "
        "Keep each visible character's emotion and reaction separate. Do not invent story beats or transfer emotion "
        "between subjects. Keep supplied timecodes; when none exist, describe relative beat order without inventing "
        "new events or dialogue. If the brief has no emotional change, preserve its baseline and use only restrained "
        "natural micro-expression, blinking and breathing. Keep expressions stable between beats; no random grimacing or jitter. "
        "Put these concrete acting beats in additional_direction."
        if emotional_direction else
        "Keep additional_direction concise, actionable and consistent with the user's brief."
    )
    # The ControlNet sentences below are frozen on purpose: they are part of the Qwen request that
    # produced the approved renders (always with selection "none"); tests/golden pins the payload.
    content = [{"type": "text", "text": (
        "Read the brief and inspect every attached reference image/frame closely. Assign exactly one role to each asset_id "
        "and preserve the user's explicit role override when present. Infer style, camera, motion, audio intent and "
        "scene keyframes. Never invent a reference ID. Choose ControlNet only when a selected source video and the "
        "brief justify structural control. Do not invent lighting/camera numeric values when the brief has no camera "
        "move; use camera_state null. For every visual asset, list concise concrete details that are actually visible "
        "in visual_evidence: for identity/character sheets include face and feature shapes, eyes, brows, nose/muzzle, "
        "hair/fur, head/body proportions, skin/fur colors, costume/accessories, hands and silhouette where visible; "
        "for style references include animation medium, linework, shape design, palette, shading, texture and light; "
        "for motion video include gesture, timing, anticipation, follow-through and secondary motion; for camera video "
        "include framing, movement, perspective and focus only when visually evidenced. Omit details not visible in "
        "the sampled frames. Do not transfer a style reference's characters/background, or an identity reference's "
        "background/composition, unless separately assigned those roles. Audio assets must use an empty visual_evidence list. "
        f"{performance_brief}\n"
        f"Frame count: {frame_count}. Studio style selection: {requested_style} (honor this explicit selection; use prompt inference only when auto). "
        "Studio structural-control selection: none (honor pose/canny/none exactly; infer only when auto). "
        "If multiple visible performers appear in one source video, preserve their separate screen positions, choreography and camera timing. "
        "Map each supplied identity picture to a source performer only when the user explicitly names that pairing; never swap identities or invent a pairing. "
        "Audio file bytes are not supplied for listening: do not claim to recognize their contents; honor the selected audio role and the user's text only. "
        f"User prompt (retain its meaning):\n{prompt}\nReferences follow with stable IDs and kinds."
    )}]
    for item in references:
        asset_id, kind = item["asset_id"], item["kind"]
        role_override = item.get("role_override")
        owner = item.get("owner")
        content.append({"type": "text", "text": f"Asset {asset_id}; kind={kind}; visible tag={item['tag']}; user role override={role_override or 'none'}"
                        + (f"; belongs to character {owner}" if owner else "") + "."})
        if kind != "audio" and not item.get("frames"):
            content.append({"type": "text", "text": f"No visual sample of {item['tag']} is attached; use an empty visual_evidence list for asset {asset_id}."})
        for index, image in enumerate(item.get("frames", []), 1):
            content.append({"type": "text", "text": f"Visual sample {index} of {item['tag']} (asset {asset_id})."})
            encoded = base64.b64encode(image).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
    schema = copy.deepcopy(PLAN_SCHEMA)
    evidence = schema["properties"]["references"]["items"]["properties"]["visual_evidence"]
    evidence["maxItems"] = limits["evidence_items"]
    evidence["items"]["maxLength"] = limits["evidence_chars"]
    schema["properties"]["additional_direction"]["maxLength"] = limits["direction_chars"]
    schema["properties"]["references"]["minItems"] = len(references)
    schema["properties"]["references"]["maxItems"] = len(references)
    if references:
        schema["properties"]["references"]["items"]["properties"]["asset_id"]["enum"] = [row["asset_id"] for row in references]
    # Roles that only exist as an explicit user choice enter the schema only for the jobs that use them, so
    # every other Qwen request (and the picture-only golden plan) stays byte-identical.
    for role in USER_ONLY_ROLES:
        if any(row.get("role_override") == role for row in references):
            schema["properties"]["references"]["items"]["properties"]["role"]["enum"].append(role)
    # The Studio already carries the full script and selected optics. Do not
    # serialize them again in the plan. Camera remains on the deterministic
    # compiler path and explicit Studio state takes precedence in main.py.
    if camera_locked:
        schema["properties"]["camera_state"] = {"type": "null"}
    content[0]["text"] += (
        "\nReturn compact JSON without indentation. "
        # The first (full) attempt keeps the exact wording that produced the picture-only golden plan;
        # explicit limits are spelled out only on the compact retry (the schema enforces them anyway).
        + ((f"Limits: additional_direction at most {limits['direction_chars']} characters; "
            + (f"at most {limits['evidence_items']} visual_evidence items per asset, each at most {limits['evidence_chars']} characters. "
               if limits["evidence_items"] else "no reference pictures are attached, so every visual_evidence list must be empty. "))
           if limits != DIRECTOR_FULL_LIMITS else "")
        + ("camera_state must be null; Studio already supplies the exact camera geometry. " if camera_locked else "Keep camera_state null unless the brief requires supported camera geometry. ")
        + "Do not rewrite or quote the screenplay, dialogue, timeline, reference bindings or preset settings. "
        "The original brief is passed to H3 unchanged. Return only concise observed reference evidence and additional "
        "acting cues that are missing from that brief, preserving all its emotional beats. "
        "Do not fill additional_direction with a repetition of the user's text."
    )
    return {
        "model": model,
        "temperature": 0,
        "max_tokens": director_output_budget(len(references), camera_locked=camera_locked, limits=limits),
        "messages": [
            {"role": "system", "content": "You are a MiniMax H3 film director and reference continuity supervisor. Extract visible design evidence precisely; never claim pixel-exact reproduction is guaranteed. Return only a valid plan matching the supplied strict JSON schema. Use only supplied asset IDs."},
            {"role": "user", "content": content},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "h3_director_plan", "strict": True, "schema": schema,
        }},
    }


def extract_director_json(raw: str, _depth: int = 0) -> dict:
    """Parse the Director plan even when the model output is slightly off-JSON.

    Accepts: JSON in ```json fences or with prose around it, raw newlines/tabs inside
    strings (strict=False), trailing commas, and a plan double-encoded as one JSON string.
    Only a complete JSON object is accepted — never an array, a number or an object
    picked out of an array; the plan schema is validated afterwards as before. On failure
    the error names the exact position so the cause is visible in Studio.
    """
    if not isinstance(raw, str):
        raise ValueError(f"ответ Qwen — {type(raw).__name__}, а не текст с JSON-объектом")
    text = raw.strip()
    try:
        whole = json.loads(text, strict=False)
    except ValueError:
        pass
    else:
        if isinstance(whole, dict):
            return whole
        if isinstance(whole, str) and _depth == 0:
            return extract_director_json(whole, 1)
        raise ValueError(f"ответ Qwen — JSON {type(whole).__name__}, а не объект плана")
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("в ответе нет JSON-объекта")
    if text[:start].rstrip().endswith("["):
        raise ValueError("ответ Qwen — JSON-массив, а не объект плана")
    text = text[start:]
    decoder = json.JSONDecoder(strict=False)
    try:
        plan, _ = decoder.raw_decode(text)
        return plan
    except json.JSONDecodeError as first:
        repaired = re.sub(r",(\s*[}\]])", r"\1", text)  # trailing commas
        try:
            plan, _ = decoder.raw_decode(repaired)
            return plan
        except json.JSONDecodeError:
            around = text[max(0, first.pos - 60):first.pos + 60].replace("\n", "⏎")
            raise ValueError(f"{first.msg} на символе {first.pos}: …{around}…") from first


def _save_director_raw(raw) -> None:
    """Keep the last rejected Director answer for diagnosis (no secrets are in it)."""
    try:
        path = os.environ.get("H3_DIRECTOR_RAW_LOG", "/workspace/h3/api-data/director-last-raw.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str))
    except Exception:
        pass


def should_use_image_reference_plan(references: list[dict], requested_style: str,
                                    *, emotional_direction: bool) -> bool:
    """Keep the lightweight image-description shortcut away from story direction."""
    image_roles = {"identity", "wardrobe", "prop", "style", "lighting", "color", "scene", "keyframe", "composition"}
    return bool(
        not emotional_direction and references and requested_style != "auto"
        and all(item["kind"] == "image" and
                (item.get("role_override") is None or item.get("role_override") in image_roles)
                for item in references)
    )


def emotional_performance_contract() -> str:
    return (
        "EMOTIONAL PERFORMANCE DIRECTION: Follow the scene's stated feelings and reactions as visible acting, "
        "not as a static label. Preserve the written emotional baseline; where the story gives a change, make its "
        "existing trigger, anticipation, reaction peak, readable hold and release distinct in that order, using "
        "the supplied timing. Use anatomy-appropriate cues: human gaze, eyelids, brows, cheeks, jaw, mouth and breath; "
        "animal eyes/pupils, ears, whiskers, muzzle, head, posture, weight and visible tail. For 2D use clear drawn "
        "poses and mouth shapes; for 3D use stable facial shapes and rig-like motion; for actor replacement preserve "
        "the named identity while transferring only the requested acting. Keep characters' emotions separate and "
        "identity, scale, costume and scene roles stable. No invented story beats, random grimaces, expression jitter "
        "or continuous exaggerated motion."
    )


def remove_unbacked_reference_data(plan: dict, asset_kinds: dict[str, str]) -> dict:
    """A text-only brief cannot acquire model-invented reference IDs or guides."""
    if not asset_kinds:
        plan["references"] = []
        plan["guide_frames"] = []
    return plan


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG SOF header, or None when the bytes are not a JPEG."""
    if data[:2] != b"\xff\xd8":
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker == 0xFF or marker == 0x01 or 0xD0 <= marker <= 0xD8:
            index += 1 if marker == 0xFF else 2
            continue
        length = int.from_bytes(data[index + 2:index + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return (width, height) if width and height else None
        index += 2 + length
    return None


def estimate_image_tokens(data: bytes) -> int:
    """Qwen3-VL vision tokens: 16 px patches merged 2x2, i.e. one token per 32x32 px."""
    width, height = _jpeg_dimensions(data) or (1024, 1024)
    return max(1, round(width / 32)) * max(1, round(height / 32)) + 2


def estimate_text_tokens(text: str) -> int:
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    return int(ascii_chars / 3.2 + (len(text) - ascii_chars) / 2.0) + 1


def estimate_director_input(payload: dict) -> dict:
    """Conservative input-token estimate of a chat request: text, image count and image tokens."""
    text_tokens, images, image_tokens = 0, 0, 0
    for message in payload.get("messages", []):
        parts = message.get("content")
        parts = [{"type": "text", "text": parts}] if isinstance(parts, str) else parts or []
        text_tokens += 8
        for part in parts:
            if part.get("type") == "text":
                text_tokens += estimate_text_tokens(part.get("text", "")) + 4
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                try:
                    data = base64.b64decode(url.split(",", 1)[1]) if "," in url else b""
                except ValueError:
                    data = b""
                images += 1
                image_tokens += estimate_image_tokens(data)
    return {"text": text_tokens, "images": images, "image_tokens": image_tokens,
            "total": text_tokens + image_tokens}


def director_request_fits(payload: dict, max_model_len: int | None = None) -> tuple[bool, dict]:
    estimate = estimate_director_input(payload)
    limit = (max_model_len or DIRECTOR_MAX_MODEL_LEN) - DIRECTOR_INPUT_MARGIN_TOKENS
    return estimate["total"] + payload["max_tokens"] <= limit, estimate


def _with_frames(references: list[dict], transform) -> list[dict]:
    return [{**item, "frames": transform(item)} for item in references]


def _one_frame(item: dict) -> list[bytes]:
    frames = list(item.get("frames") or [])
    return frames[len(frames) // 2:len(frames) // 2 + 1] if item.get("kind") == "video" else frames[:1]


def fit_director_references(references: list[dict], build, *, max_model_len: int | None = None,
                            shrink=None) -> tuple[list[dict], str, dict]:
    """Smallest change to the attached frames that keeps the request inside max_model_len.

    Ladder: as sent -> one frame per video -> frames shrunk to 640 px -> no frames at all.
    `build(references)` returns the payload. Raises ValueError when even the text alone does
    not fit, so the job renders from the Studio prompt with that precise reason.
    """
    shrink = shrink or _shrink_jpeg
    steps = (
        ("all_frames", lambda refs: refs),
        ("one_frame_per_video", lambda refs: _with_frames(refs, _one_frame)),
        ("frames_640px", lambda refs: _with_frames(refs, lambda item: [shrink(frame, 640) for frame in _one_frame(item)])),
        ("no_frames", lambda refs: _with_frames(refs, lambda item: [])),
    )
    estimate: dict = {}
    for name, step in steps:
        candidate = step(references)
        fits, estimate = director_request_fits(build(candidate), max_model_len)
        if fits:
            return candidate, name, estimate
    raise ValueError(f"бриф не помещается в контекст Qwen даже без кадров: ~{estimate.get('total')} "
                     f"входных токенов при max_model_len {max_model_len or DIRECTOR_MAX_MODEL_LEN}")


_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def sanitize_director_plan(plan: dict, prompt: str, references: list[dict], limits: dict | None = None) -> dict:
    """Clamp Qwen's free text to the request limits before the strict plan check.

    * visual_evidence: strings only, trimmed, de-duplicated, at most `evidence_items` of at most
      `evidence_chars`; emptied for assets Qwen received no picture of (it could only invent them);
    * additional_direction: sentences copied verbatim from the brief are dropped — the brief is
      passed to H3 unchanged, so an echo would only double it.
    """
    limits = {**DIRECTOR_FULL_LIMITS, **(limits or {})}
    seen_frames = {item["asset_id"] for item in references if item.get("frames")}
    rows = plan.get("references")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            evidence = row.get("visual_evidence")
            if row.get("asset_id") not in seen_frames or not isinstance(evidence, list):
                row["visual_evidence"] = []
                continue
            kept: list[str] = []
            for item in evidence:
                if not isinstance(item, str) or not item.strip():
                    continue
                item = " ".join(item.split())
                if len(item) > limits["evidence_chars"]:
                    item = item[:limits["evidence_chars"]].rsplit(" ", 1)[0].rstrip(",;: ")
                # Qwen occasionally returns a short but unfinished fragment (e.g. "lips in calm,").
                # Do not turn that fragment into a false/garbled visual claim in the H3 prompt.
                if re.search(
                    r"(?:[,;:]|\b(?:and|or|with|of|in|at|for|to|a|an|the))\s*$", item, re.I):
                    continue
                if item and item not in kept:
                    kept.append(item)
            row["visual_evidence"] = kept[:min(limits["evidence_items"] or 0, 6)]
    direction = plan.get("additional_direction")
    if not isinstance(direction, str):
        direction = ""
    brief = " ".join(prompt.split())
    sentences = [match.group(0).strip() for match in _SENTENCE.finditer(direction)]
    echoed = [sentence for sentence in sentences if len(sentence) >= 40 and " ".join(sentence.split()) in brief]
    if echoed:
        direction = " ".join(sentence for sentence in sentences if sentence and sentence not in echoed)
    plan["additional_direction"] = direction.strip()
    return plan


def _usage_tokens(result: dict) -> tuple[object, object]:
    usage = result.get("usage") if isinstance(result, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


_CONTEXT_ERROR = re.compile(r"(?i)context length|max_model_len|maximum (?:model|context)|too (?:long|many tokens)"
                            r"|prompt.*tokens|image|multimodal|mm_|vision|pixel")


DIRECTOR_MISSING = ("Qwen Director не установлен на этом GPU-сервере (режим --qwen disabled): "
                    "сценарий, планы и проверки пишет агент Studio")


def director_installed() -> bool:
    """False when nothing listens on the Director port and nothing will start it (h3_new_server.sh --qwen disabled).

    Checked before ComfyUI frees the H3 weights for Qwen: without the Director that unload only costs the next
    render a full weight reload."""
    from .qwen_lifecycle import enabled
    if enabled():  # guard mode: qwen_lifecycle starts the stopped service on demand
        return True
    parsed = urlparse(os.environ.get("H3_DIRECTOR_URL", "http://127.0.0.1:8000/v1"))
    try:
        with urllib.request.urlopen(f"{parsed.scheme}://{parsed.netloc}/health", timeout=3):
            return True
    except urllib.error.HTTPError:
        return True  # something answers there: let the request report what it is
    except (urllib.error.URLError, OSError):
        return False


class LocalQwenDirector:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("H3_DIRECTOR_URL", "http://127.0.0.1:8000/v1")).rstrip("/")
        from .scenarist_profile import active_profile
        profile = active_profile()  # H3_SCENARIST_PROFILE; "default" adds nothing to any request
        self.profile = profile["name"]
        self.model = model or os.environ.get("H3_DIRECTOR_MODEL") or profile["model"]
        self.request_extras = profile["director_extras"]
        self.scenarist_extras = profile["scenarist_extras"]
        self.exclusive_gpu = bool(profile["exclusive_gpu"])
        from .scenarist_profile import sleep_level
        sleep_level()  # an invalid H3_QWEN_SLEEP_LEVEL fails before Qwen wakes, not in its finally
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Director должен работать локально на этом GPU-сервере")
        self.runtime_root = f"{parsed.scheme}://{parsed.netloc}"

    def _runtime_control(self, endpoint: str) -> None:
        from .scenarist_profile import sleep_level
        level = sleep_level()
        if endpoint.startswith("sleep"):
            endpoint = f"sleep?level={level}"  # callers say level=1; H3_QWEN_SLEEP_LEVEL may say 2
        elif endpoint == "wake_up":
            if self.exclusive_gpu:
                # One model on the GPU at a time: re-check under the planning lock that ComfyUI is idle
                # and free its weights right before the wake (raises if a render got in first).
                from .qwen_lifecycle import _free_comfy_models
                _free_comfy_models()
            if level == 2:
                # Level 2 discarded the weights: restore the pool, reload in place, then KV cache.
                self._runtime_request("wake_up?tags=weights")
                self._runtime_request("collective_rpc", {"method": "reload_weights"})
                endpoint = "wake_up?tags=kv_cache"
        self._runtime_request(endpoint)

    def _runtime_request(self, endpoint: str, payload: dict | None = None) -> None:
        data = json.dumps(payload).encode() if payload is not None else b"{}"
        request = urllib.request.Request(f"{self.runtime_root}/{endpoint}", data=data,
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            # reload_weights (sleep level 2) reads ~31 GB from disk; plain sleep/wake take < 30 s.
            with urllib.request.urlopen(request, timeout=300 if payload else 30) as response:
                if response.status >= 300:
                    raise RuntimeError(f"vLLM {endpoint} вернул HTTP {response.status}")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Не удалось переключить локальную Qwen для H3 ({endpoint}): {exc}") from exc

    def _is_sleeping(self) -> bool:
        from .qwen_lifecycle import ensure_started
        ensure_started()
        try:
            with urllib.request.urlopen(f"{self.runtime_root}/is_sleeping", timeout=15) as response:
                result = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Не удалось проверить состояние локальной Qwen: {exc}") from exc
        if isinstance(result, bool):
            return result
        if isinstance(result, dict) and isinstance(result.get("is_sleeping"), bool):
            return result["is_sleeping"]
        raise RuntimeError("vLLM /is_sleeping вернул неизвестный формат")

    def plan(self, prompt: str, references: list[dict], asset_kinds: dict[str, str], frame_count: int,
             requested_style: str = "auto",
             emotional_direction: bool = False, camera_locked: bool = False) -> dict:
        from .qwen_lifecycle import planning_lock
        with planning_lock():
            return self._plan_locked(prompt, references, asset_kinds, frame_count,
                                     requested_style=requested_style,
                                     emotional_direction=emotional_direction, camera_locked=camera_locked)

    def _post(self, body: dict) -> tuple[dict | None, tuple[int, str] | None, float]:
        """One chat completion. Returns (result, (http_code, detail) on HTTP error, seconds)."""
        started = time.monotonic()
        if self.request_extras:  # non-default profile only (e.g. enable_thinking false)
            body = {**body, **self.request_extras}
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.load(response), None, time.monotonic() - started
        except urllib.error.HTTPError as exc:
            try:
                detail = (exc.read() or b"").decode(errors="replace")
            except Exception:
                detail = ""
            detail = " ".join(f"HTTP {exc.code}: {detail or exc.reason or ''}".split())
            return None, (exc.code, detail), time.monotonic() - started
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Локальная Qwen Director не ответила: {exc}") from exc

    def _plan_locked(self, prompt: str, references: list[dict], asset_kinds: dict[str, str], frame_count: int,
                     requested_style: str = "auto",
                     emotional_direction: bool = False, camera_locked: bool = False) -> dict:
        if should_use_image_reference_plan(references, requested_style,
                                            emotional_direction=emotional_direction):
            return self._image_reference_plan(prompt, references, asset_kinds, frame_count, requested_style)
        log = logging.getLogger(__name__)
        explicit_roles = {item["asset_id"]: item["role_override"] for item in references
                          if item.get("role_override")}

        def build(refs: list[dict], limits: dict) -> dict:
            return openai_director_payload(prompt, refs, model=self.model,
                                           frame_count=frame_count, requested_style=requested_style,
                                           emotional_direction=emotional_direction, camera_locked=camera_locked,
                                           limits=limits)

        # Native vLLM 0.15 contract first (xgrammar, no free whitespace: the 2026-09-30/10-03
        # failures were unconstrained, indented or whitespace-flooded JSON). response_format is a
        # second *transport* only when the server rejects the structured_outputs field itself.
        def transport_body(payload: dict, transport: str) -> dict:
            if transport == "response_format":
                return payload
            schema = payload["response_format"]["json_schema"]["schema"]
            base = {key: value for key, value in payload.items() if key != "response_format"}
            return {**base, "structured_outputs": {"json": schema, "disable_any_whitespace": True}}

        def checked_plan(candidate: dict, refs: list[dict], limits: dict) -> dict:
            candidate = sanitize_director_plan(candidate, prompt, refs, limits)
            candidate = remove_unbacked_reference_data(candidate, asset_kinds)
            candidate = apply_cinematography_policy(candidate, prompt, frame_count)
            for row in candidate.get("references", []):
                if isinstance(row, dict) and row.get("asset_id") in explicit_roles:
                    row["role"] = explicit_roles[row["asset_id"]]
            candidate["control"] = {"type": "none", "source_asset_id": None,
                                    "people": 1, "strength": 1.0}
            return validate_camera_or_keep_prompt(candidate, asset_kinds, frame_count, explicit_roles)

        # Fit the request into max_model_len before waking Qwen: an oversized request is reshaped
        # (fewer / smaller frames) instead of being sent only to fail with HTTP 400.
        limits = DIRECTOR_FULL_LIMITS
        try:
            refs, frames_mode, _ = fit_director_references(references, lambda r: build(r, limits))
        except ValueError as exc:
            raise RuntimeError(f"Qwen Director без плана — {exc}") from exc

        transports = ["structured_outputs", "response_format"]
        transport = 0
        attempt = "full"
        failures: list[str] = []
        plan = None
        last_raw = None
        if self._is_sleeping():
            self._runtime_control("wake_up")
        try:
            while True:
                payload = build(refs, limits)
                _, estimate = director_request_fits(payload)
                result, http_error, seconds = self._post(transport_body(payload, transports[transport]))
                finish = completion = prompt_tokens = None
                images_failed = False
                if http_error:
                    code, detail = http_error
                    images_failed = code in (400, 413, 422) and bool(_CONTEXT_ERROR.search(detail))
                    retry_transport = code in (400, 422) and not images_failed and transport == 0
                    outcome = f"http_{code}" + ("_switch_transport" if retry_transport else "")
                    failure = f"{attempt}: {detail[:170]}"
                else:
                    choice = (result.get("choices") or [{}])[0]
                    raw = (choice.get("message") or {}).get("content")
                    last_raw = raw
                    finish = choice.get("finish_reason")
                    prompt_tokens, completion = _usage_tokens(result)
                    retry_transport = False
                    try:
                        if finish == "length":
                            raise ValueError(f"ответ обрезан лимитом max_tokens={payload['max_tokens']}"
                                             f" (выдано {completion if completion is not None else '?'} токенов,"
                                             f" {len(raw) if isinstance(raw, str) else 0} символов)")
                        candidate = extract_director_json(raw) if isinstance(raw, str) else raw
                        if not isinstance(candidate, dict):
                            raise ValueError(f"ответ Qwen — {type(candidate).__name__}, а не объект плана")
                        plan = checked_plan(candidate, refs, limits)
                        outcome = "ok"
                        failure = ""
                    except (TypeError, ValueError) as exc:
                        outcome = "rejected"
                        failure = f"{attempt}: finish_reason={finish}, {str(exc)[:170]}"
                # One compact line per call (goal: see size, budget and result of every attempt).
                log.info("Director call attempt=%s transport=%s frames=%s input~%d (text~%d, images=%d~%d)"
                         " max_tokens=%d prompt_tokens=%s output_tokens=%s finish=%s seconds=%.1f outcome=%s",
                         attempt, transports[transport], frames_mode, estimate["total"], estimate["text"],
                         estimate["images"], estimate["image_tokens"], payload["max_tokens"], prompt_tokens,
                         completion, finish, seconds, outcome)
                if plan is not None:
                    break
                if retry_transport:
                    transport = 1  # the server does not know structured_outputs; nothing was generated
                    continue
                failures.append(failure)
                if attempt != "full" or (http_error and http_error[0] not in (400, 413, 422)):
                    break
                # The single retry: a smaller, stricter request. Without any frames when the
                # pictures made the request too large; otherwise one frame per asset.
                attempt = "compact_no_frames" if images_failed or frames_mode == "no_frames" else "compact"
                limits = (DIRECTOR_COMPACT_LIMITS if attempt == "compact"
                          else {**DIRECTOR_COMPACT_LIMITS, "evidence_items": 0})
                smaller = (_with_frames(refs, lambda item: []) if attempt == "compact_no_frames"
                           else _with_frames(refs, _one_frame))
                try:
                    refs, frames_mode, _ = fit_director_references(smaller, lambda r: build(r, limits))
                except ValueError as exc:
                    failures.append(f"{attempt}: {exc}")
                    break
        finally:
            # H3 and the 8B Director share one accelerator; release Director
            # weights/KV before ComfyUI starts its render.
            try:
                self._runtime_control("sleep?level=1")
            except Exception as exc:
                raise RuntimeError("Qwen не освободила VRAM; H3 job не отправлен") from exc
        if plan is None:
            _save_director_raw(last_raw)
            raise RuntimeError("Qwen Director без плана — " + " | ".join(failures)[:900])
        return plan

    def _image_reference_plan(self, prompt: str, references: list[dict], asset_kinds: dict[str, str],
                              frame_count: int, style: str) -> dict:
        content = [{"type": "text", "text": (
            "Inspect the following reference images. Reply with one short numbered line per image, in order. "
            "Describe features relevant to each stated role. "
            "Use only facts visible in the image. Do not repeat any line. Maximum 20 words per line."
        )}]
        for index, item in enumerate(references, 1):
            content.append({"type": "text", "text":
                f"Image {index}; role={resolved_image_only_role(item.get('role_override'))}"})
            for image in item.get("frames", [])[:1]:
                content.append({"type": "image_url", "image_url": {"url":
                    "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")}})
        payload = {"model": self.model, "temperature": 0, "max_tokens": 240,
                   "messages": [{"role": "system", "content": "Describe visible reference details briefly. Plain text only."},
                                {"role": "user", "content": content}]}
        payload.update(self.request_extras)
        request = urllib.request.Request(f"{self.base_url}/chat/completions",
                                         data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        if self._is_sleeping():
            self._runtime_control("wake_up")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.load(response)
        finally:
            self._runtime_control("sleep?level=1")
        estimate = estimate_director_input(payload)
        prompt_tokens, completion = _usage_tokens(result)
        logging.getLogger(__name__).info(
            "Director call attempt=image_descriptions transport=plain_text frames=all_frames input~%d (text~%d, images=%d~%d)"
            " max_tokens=%d prompt_tokens=%s output_tokens=%s finish=%s seconds=%.1f",
            estimate["total"], estimate["text"], estimate["images"], estimate["image_tokens"], payload["max_tokens"],
            prompt_tokens, completion, ((result.get("choices") or [{}])[0]).get("finish_reason"),
            time.monotonic() - started)
        raw = result["choices"][0]["message"]["content"]
        lines = {}
        for line in raw.splitlines():
            match = re.match(r"^\s*(\d+)\s*[:.)-]\s*(.+)", line)
            if match:
                number = int(match.group(1))
                if 1 <= number <= len(references) and number not in lines:
                    lines[number] = match.group(2).strip()[:120]
        if not lines:
            raise RuntimeError("Qwen не описала ни один загруженный референс")
        plan = {
            "schema_version": 1,
            "references": [{"asset_id": item["asset_id"],
                            "role": resolved_image_only_role(item.get("role_override")),
                            "fidelity": "fully_preserved" if resolved_image_only_role(item.get("role_override")) == "identity" else "partly_preserved",
                            "visual_evidence": [lines[index]] if lines.get(index) else []}
                           for index, item in enumerate(references, 1)],
            "style": style, "additional_direction": "", "control": {"type": "none", "source_asset_id": None,
                                                                  "people": 1, "strength": 1.0},
            "camera_state": None, "guide_frames": [], "quality_intent": "draft",
            "audio_intent": "native_stereo_effects_music",
        }
        plan = apply_cinematography_policy(plan, prompt, frame_count)
        return validate_director_plan(plan, asset_kinds, frame_count,
                                      {item["asset_id"]: item["role_override"] for item in references
                                       if item.get("role_override")})
