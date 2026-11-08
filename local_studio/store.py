from __future__ import annotations

from .character_identity import replacement_identity, validate_replacement_prompt
from . import passport_medium, token_preflight

from app.cache_policy import normalize_cache_config
from app.h3_ir import latin_name
from app.motion_reference_policy import CAMERA_RULE as MOTION_CAMERA_RULE, ROLE_INSTRUCTION as MOTION_CAMERA_ROLE_INSTRUCTION, MOTION_ONLY_INSTRUCTION, normalize_motion_retention

import json
import time
import hashlib
import shutil
import secrets
import sqlite3
import subprocess
import uuid
from .owner_aliases import resolve_owner
import os
import re
from datetime import datetime, timezone
from contextlib import contextmanager
from collections.abc import Iterable, Iterator
from pathlib import Path
from app.render_profiles import normalize_render_options, validate_render_combination
from app.ab_variant import AB_TURBO

from app.reference_manifest import compile_reference_manifest
from app.h3_ir import (Reference, build_skeleton, complete_subject_sources, is_blocking, is_ir_prompt,
                       parse_ir, rebind_subject_pictures, render_ir, validate_ir)
from app.contract import DEFAULTS, GenerationRequest, default_megapixels
from app.camera_optics import normalize_camera_optics
from app.director_config import normalize_director_config
from app.color_grade import normalize_color_grade, normalize_film_look
from app.color_match import REFERENCE_ROLES as COLOR_MATCH_REFERENCE_ROLES, normalize_color_match
from app.character_refinement import normalize_character_refinement
from app.swap_mask import normalize_swap_mask
from app.reskin import normalize_reskin
from app.graph import resolve_guide_frame
from app.camera_effects import normalize_camera_effect
from app.continuation import CLIP_FRAMES as CONTINUATION_FRAMES, ROLE as CONTINUATION_ROLE
from app.lora_routing import select_adapter
from app.sampling import sampling_inputs
from app.prompt_settings_package import prompt_settings_package, ensure_audio_direction


DRAFT_HISTORY = 30  # canvas versions kept by save_draft

class StaleTransition(RuntimeError):
    """The job changed before a conditional queue transition could commit."""


class DraftConflict(RuntimeError):
    """The saved draft changed since the client loaded it."""


VISUAL_MODES = {
    "replace": "",
    "realistic": (
        "VISUAL DIRECTION: Live-action realism. Keep supplied identities and scene roles. "
        "Develop cinematic action, blocking, reactions and camera beats from the user's story where they are unspecified; "
        "keep the stated events, cast and reference roles intact."
    ),
    "animation3d": (
        "VISUAL DIRECTION: FULLY STYLIZED 3D ANIMATED FEATURE FILM. Render every visible person, "
        "animal, prop and environment in one coherent non-photoreal 3D animation language. "
        "Reference pictures define identity, silhouette, anatomy, markings, colors, costume and "
        "scene geometry only. They do not define photographic surface rendering. Translate any "
        "photo-like skin, fur, fabric, furniture or architecture into designed animation materials: "
        "clean controlled shapes, simplified groom and texture detail, stable expressive eyes, "
        "appealing feature-animation proportions and consistent cinematic shading. Convey each "
        "character's intended emotion through readable eyes, brows, muzzle or mouth shapes, head angle, "
        "body weight, gesture timing and reaction pauses; keep that facial acting temporally stable. "
        "Preserve each character's identity while removing live-action and photographic texture. Never mix a realistic animal "
        "or realistic room with a stylized character. No photoreal skin, no photoreal fur, no hybrid "
        "live-action look, no style changes between shots."
    ),
    "animation2d": (
        "VISUAL DIRECTION: 2D animated film with drawn shapes and consistent linework. "
        "Keep the identities and scene roles from the references; do not render them as live action or 3D. "
        "Use clear pose silhouettes, expressive eyes and brows, readable mouth shapes, timed reaction "
        "holds and stable facial acting without line boil or style drift."
    ),
}

NATIVE_AUDIO_DIRECTION = (
    "AUDIO DIRECTION: Generate rich synchronized native stereo audio for the scene, including "
    "voices when requested, physical sound effects, ambience, room tone and appropriate music. "
    "Keep every audible event synchronized with the visible action and camera distance."
)

VOICE_PERFORMANCE_DIRECTION = (
    "VOICE PERFORMANCE DIRECTION: Cast each named speaking character with a distinct, stable voice "
    "suited to the character's age, species and identity. A child uses a child voice, not an adult voice. "
    "A non-speaking animal uses natural species vocalizations; if the story explicitly gives an animal "
    "human speech, retain its animal identity and assign a distinct character voice. Follow the written "
    "dialogue and dramatic beats precisely. Convey each stated emotion through pacing, emphasis, breath, "
    "pauses, pitch and intonation, while keeping the same voice identity through the shot. Do not invent "
    "dialogue or exchange voices between speakers."
)

VIDEO_PERFORMANCE_DIRECTION = (
    "VIDEO PERFORMANCE DIRECTION: Use each source video for its assigned performance and camera roles. "
    "When the user maps a picture character to a visible video performer, keep that pairing through the shot: "
    "transfer the performer's action order, pose progression, timing, screen position and camera relationship "
    "to the named character while preserving identity from the named picture. "
    "For multiple performers, maintain separate pairings; never exchange their actions or identities. "
    "Do not invent a performer-to-character pairing that the user did not specify."
)
# «Движение» lends the body only; the camera is the text's. The general block above also asked for the
# performer's "screen position and camera relationship", which fought the camera rules (audit 2026-10-05).
VIDEO_BODY_DIRECTION = (
    "VIDEO PERFORMANCE DIRECTION: The movement video lends only its performer's action order, pose "
    "progression and timing to the named character, whose identity comes from the named picture. With "
    "several performers, each named character keeps its own pairing for the whole shot. Camera framing and "
    "camera movement follow this text."
)

# Movement videos only (picture-only jobs untouched). When the performer in a «Движение»
# video walks away from the camera, H3 copied that distance: the hero shrank to a few pixels, the
# face lost the pictured features and the Studio "deep focus" card left nothing tracked (2026-10-05).
MOTION_FOCUS_DIRECTION = (
    "MOTION FOCUS AND SUBJECT SCALE: Focus tracks the principal character's face continuously like a "
    "subject-tracking autofocus: as the character moves toward or away from the camera, the focal plane "
    "follows the eyes and the face stays crisp at every distance, with no focus hunting. "
)
MOTION_SCALE_FOLLOW = (
    "The camera travels with the character to hold a near-constant screen size: when the movement carries "
    "the character away, the camera follows forward, and when the character approaches, it eases back, so "
    "the head stays large enough that the pictured face, hairstyle and features remain clearly readable."
)
MOTION_SCALE_CAMERA_VIDEO = (
    "The camera path comes from the movement video; at the widest moments the character's face still keeps "
    "the pictured features, hairstyle and proportions in clean, stable detail."
)

# Fast eyelid motion smeared when a blink or a sharp eye opening fell between sampled details (2026-10-05).
MOTION_EYES_DIRECTION = (
    " Eyes stay photographically clean through fast action: each blink and each sudden eye opening is a crisp "
    "eyelid movement over two to three frames, and the instant the eyes open the irises, pupils, eyelashes and "
    "catchlights are sharp, stable and identical to the eyes in the character's picture."
)

DEFAULT_HERO_FACE_FOCUS = (
    "HERO FACE FOCUS: In every shot, hold the focal plane on the active named hero's face and eyes through "
    "movement, turns and expression changes. Keep the active hero crisp while allowing distant background and "
    "inactive bystanders to fall softly out of focus when the composition supports it. In reaction beats, prefer "
    "a medium close-up or close-up that gives the hero's face enough image area for clear detail. Do not shift "
    "focus from the active hero to the room or background. When multiple named heroes are simultaneously active, "
    "keep both faces readable where framing permits. This is visual guidance for H3, not a physical autofocus control."
)

FULL_BODY_HERO_FACE_FOCUS = (
    "HERO FACE FOCUS: Keep the named hero's face and eyes sharp within the requested full-body framing. "
    "Preserve enough room for both hands, feet and the complete action throughout the shot. "
    "Focus follows the hero without changing camera distance or cropping the choreography into a face close-up. "
    "This is visual guidance for H3, not a physical autofocus control."
)


# Every settings key POST /api/jobs accepts (audit 42.3C №12): contract DEFAULTS, what Studio sends
# (preview/h3-director: settingsSnapshot, officialSettings, colorMatchSetting, character-refine,
# allow_short_motion_reference) and what create_job itself stores, because «Повторить» resends a saved
# settings_json as is. Anything else is a 422, not a silently ignored field.
SWAP_LORAS = {"v1": "character_swap", "v1.5": "character_swap_v15"}

SETTINGS_KEYS = frozenset(DEFAULTS) | {
    "cache_config", "render_options", "continuation_trim_frames",
    "duration", "aspect_ratio", "style_adapter", "seed", "use_video_audio",
    "camera_state", "camera_optics", "director_config", "color_match", "color_grade", "film_look",
    "guide_frames", "camera_effect", "allow_short_motion_reference", "character_refinement",
    # written by create_job into settings_json (resent by «Повторить»)
    "width", "height", "length", "profile", "motion_control", "lora_auto_reason", "prompt_format", "speed",
    # «Правка видео» → «Звук исходника без изменений»: local_studio/worker.py replace_audio after download
    "keep_source_audio", "context_ir", "ab_turbo", "seed_hunt", "agent_prepared_prompt", "no_lora_swap_trial",
    # "none": the agent's text goes to H3 without Studio's generic blocks (docs/reference/H3_ENCODER_RESEARCH.md)
    "prompt_blocks",
    "backend", "cloud_model", "cloud_resolution", "api_swap_trial", "swap_recipe",
    # Character Swap LoRA version for hero replacement: "v1" (default) or "v1.5" (A/B)
    "swap_lora",
    # Masked hero swap (app/swap_mask.py): performer boxes on the first source frame; only they are redrawn
    "swap_mask",
    "reskin",
    # The user's own words asking for an A/B swap option (require_ab_request below).
    "ab_user_request",
    # production_gates.REJECT_STREAK: the user agreed to render again after two rejected takes of one source.
    "confirm_after_rejects",
}

# Hero-swap options that are A/B experiments, not the default swap route.
AB_ADAPTERS = frozenset({"character_swap_v15"})


def require_ab_request(settings: dict, style_adapter: str) -> None:
    """Refuse an A/B swap option unless the job carries the user's request for it, in their words.

    An agent that queues v1.5 or reskin swaps nobody asked for gets takes that keep the source performer. The
    options stay, only on the user's request; the Studio
    form sends the request itself when the user picks V1.5 or draws a mask by hand."""
    used = [name for name, on in (
        ("Character Swap v1.5", style_adapter in AB_ADAPTERS or settings.get("swap_lora") == "v1.5"),
        ("рескин поверх исходника", bool(settings.get("reskin"))),
        ("маска замены", bool(settings.get("swap_mask")))) if on]
    if not used:
        settings.pop("ab_user_request", None)
        return
    words = str(settings.get("ab_user_request") or "").split()
    if len(words) < 2:
        raise ValueError(f"{', '.join(used)} — A/B-проба, а не принятый рецепт: ставь её только по просьбе пользователя "
                         "и передай его слова в ab_user_request. Для замены по видео по умолчанию — «Смена мира по "
                         "видео» или «Замена по видео» со встроенными настройками")
    settings["ab_user_request"] = " ".join(words)[:400]
# Fields of removed features that old saved jobs still carry; «Повторить» on such a job drops them.
LEGACY_SETTINGS_KEYS = frozenset({"pose_people", "voice_rights_confirmed", "control", "control_asset_id", "control_strength"})


def add_default_hero_face_focus(prompt: str, images: Iterable[dict], official_format: bool) -> str:
    """Add hero-focus guidance unless the prompt already assigns focus to a hero's face/eyes."""
    if not any(image.get("job_role", image.get("role")) in {"Лицо героя", "Персонаж целиком"}
               for image in images):
        return prompt
    hero_focus = re.search(
        r"(?:\b(?:focus|focused|focal plane|autofocus|rack focus|sharp|in focus)\b.{0,100}"
        r"\b(?:hero|subject|character|face|eyes)\b|"
        r"\b(?:hero|subject|character|face|eyes)\b.{0,100}"
        r"\b(?:focus|focused|sharp|in focus)\b|"
        r"(?:фокус|резк\w*).{0,100}(?:геро\w*|лиц\w*|глаз\w*)|"
        r"(?:геро\w*|лиц\w*|глаз\w*).{0,100}(?:фокус|резк\w*))",
        prompt, re.I | re.S,
    )
    if hero_focus:
        return prompt
    # A generic close-up preference erased hands/feet from full-body dance shots.
    # Focus should follow the face without overruling the requested framing.
    full_body = bool(re.search(r"\b(?:full[- ]body|head[- ]to[- ]toe|shot\.full)\b|"
                               r"в полный рост|общ(?:ий|его) план", prompt, re.I))
    focus_rule = FULL_BODY_HERO_FACE_FOCUS if full_body else DEFAULT_HERO_FACE_FOCUS
    if official_format:
        sections = parse_ir(prompt)
        description = sections.get("detailed_description")
        if not description:
            return prompt
        sections["detailed_description"] = description.rstrip() + "\n\n" + focus_rule
        return render_ir(sections)
    return focus_rule + "\n\n" + prompt


# Reference videos lend movement, camera and depth; their burned-in graphics are not part of the scene.
# H3 copies whatever a reference frame shows (captions, logos, intro cards), so state the clean frame positively.
CLEAN_FRAME_DIRECTION = (
    "CLEAN FRAME: The reference videos lend only movement, camera path and spatial depth. The generated picture "
    "is clean footage of this scene alone, edge to edge: only its people, place and objects are visible, the story "
    "starts on its first frame, and the whole image area is pure photographed picture, free of the lettering, "
    "captions, subtitles, logos, watermarks, stickers, title cards and interface graphics of the reference files."
)
_CLEAN_FRAME_ROLES = {"Движение", "Движение и камера", "Актёрская игра", "Камера", "Ритм сцены"}


def clean_frame_direction(videos: Iterable[dict]) -> str:
    """Clean-frame rule for jobs with a movement/camera/acting/rhythm reference video; empty otherwise."""
    roles = {video.get("job_role", video.get("role")) for video in videos}
    return CLEAN_FRAME_DIRECTION if roles & _CLEAN_FRAME_ROLES else ""


def motion_focus_direction(videos: Iterable[dict], camera_from_cards: bool = False) -> str:
    """Focus/scale rule for jobs whose video lends body motion; empty for every other job.

    camera_from_cards: the Studio movement/shot cards set the camera, so this block adds only focus and
    eyes instead of its own "follow to hold size" camera (three camera rules fought each other).
    """
    roles = {video.get("job_role", video.get("role")) for video in videos}
    if "Движение и камера" in roles:
        return MOTION_CAMERA_RULE
    if "Движение" in roles:
        return MOTION_FOCUS_DIRECTION + ("" if camera_from_cards else MOTION_SCALE_FOLLOW) + MOTION_EYES_DIRECTION
    return ""


_DIRECTOR_STATE = re.compile(
    r"(\n\nDIRECTOR STATE — apply only where compatible with the described action and reference roles:\n)(.*?)(?=\n\n|\Z)",
    re.S)
_VIDEO_CAMERA_ROLES = {"Движение и камера", "Камера"}
# English card titles since 31.10 (director-presets.mjs compileDirectorState); Russian ones in older drafts.
CAMERA_CARD_LINES = ("Lens:", "Shot:", "Movement:", "Объектив:", "Кадр:", "Движение:")


def harmonize_director_state(prompt: str, videos: Iterable[dict]) -> tuple[str, bool]:
    """One camera authority per job. Returns (prompt, camera set by Studio cards).

    A video that drives the camera («Движение и камера», «Камера») removes the card lines for lens, shot
    and camera movement, which otherwise competed with the video's camera. With «Движение» the movement /
    shot cards stay and are the camera.
    """
    roles = {video.get("job_role", video.get("role")) for video in videos}
    match = _DIRECTOR_STATE.search(prompt)
    if not match:
        return prompt, False
    lines = match.group(2).split("\n")
    if roles & _VIDEO_CAMERA_ROLES:
        kept = [line for line in lines if not line.startswith(CAMERA_CARD_LINES)]
        body = "\n".join(kept)
        new = prompt[:match.start()] + (match.group(1) + body if kept else "") + prompt[match.end():]
        return new, False
    return prompt, any(line.startswith(("Movement:", "Shot:", "Движение:", "Кадр:")) for line in lines)


REFERENCE_ROLE_PROMPTS = {
    "Лицо героя": ("image", "defines this character's face and identity only"),
    "Персонаж целиком": ("image", "defines this character's full-body design and silhouette"),
    "Костюм": ("image", "defines wardrobe and carried props for the named hero only; ignore the pictured face, body identity, background, lighting and framing"),
    "Стиль": ("image", "defines rendering style, palette and line/material treatment only"),
    "Окружение": ("image", "defines the location and set dressing only"),
    "Предмет": ("image", "defines the referenced object, its visible shape, material and scale only; ignore the background, people, lighting and camera angle"),
    "Свет": ("image", "defines lighting only: key and fill direction, softness, contrast and color temperature; do not copy its subject, location or composition"),
    "Цвет": ("image", "defines color grade and palette only; do not copy its subject, location or composition"),
    "Кадр сцены": ("image", "anchors this scene composition at its requested frame"),
    "Композиция": ("image", "is a blurred grey layout sketch: it guides only where the figures stand, the shot size and the camera angle; every person keeps the face, hair and clothing of their own picture"),
    "Исходное видео": ("video", "provides source-scene context; preserve only the requested attributes"),
    "Движение": ("video", MOTION_ONLY_INSTRUCTION),
    "Движение и камера": ("video", MOTION_CAMERA_ROLE_INSTRUCTION),
    "Актёрская игра": ("video", "provides this character's facial expression, head motion, gestures and timing only; do not copy the driving performer, voice, wardrobe or background"),
    "Камера": ("video", "provides camera framing, movement and spatial depth only"),
    "Ритм сцены": ("video", "provides edit rhythm and event timing only"),
    # Validation only: the GPU anchors this clip's last 22 frames + sound at frame 0 (app/continuation.py);
    # it is not a Ref2VA <Video N>, so no role line is written into the prompt.
    CONTINUATION_ROLE: ("video", "continues the shot from the last frames of this clip"),
    "Голос": ("audio", "provides voice characteristics only"),
    "Музыка": ("audio", "provides music style and rhythm only"),
    "Звуковой эффект": ("audio", "provides sound-effect character only"),
    "Атмосфера": ("audio", "provides ambience only"),
}


_CHARACTER_SHEET = re.compile(
    r"(?im)^\s*<Picture\s+(\d+)>\s*=\s*([A-Z][A-Z0-9 _-]{1,48}?)\s*/\s*[^\n]{1,80}?\s+ONLY\.?\s*$"
)


def validate_image_bindings(prompt: str, image_count: int, role_bound: set[int] = frozenset()) -> None:
    """Do not silently condition a multi-image scene on unnamed sheets.

    role_bound: pictures with an explicit Studio role; the store writes their role contract itself
    (e.g. «Кадр сцены» from a previous clip, which the user never names in the text).
    """
    if image_count < 2:
        return
    unsupported = re.search(r"<Environment\s+\d+>", prompt, re.IGNORECASE)
    if unsupported:
        raise ValueError(
            f"{unsupported.group(0)} не является меткой H3. Для загруженной картинки используй <Picture N>."
        )
    bound = {int(number) for number in re.findall(r"<Picture\s+(\d+)>", prompt, re.IGNORECASE)} | set(role_bound)
    missing = [f"<Picture {number}>" for number in range(1, image_count + 1) if number not in bound]
    if missing:
        raise ValueError(
            "Прикреплённые картинки не привязаны к промпту: " + ", ".join(missing) +
            ". Назови роль каждой картинки или убери её из этого задания."
        )


def character_reference_contract(prompt: str, image_count: int) -> str:
    """Bind explicitly named character sheets to distinct H3 Subjects."""
    bindings: list[tuple[int, str]] = []
    used_names: set[str] = set()
    for match in _CHARACTER_SHEET.finditer(prompt):
        number, name = int(match.group(1)), match.group(2).strip()
        if number > image_count:
            continue
        if name in used_names or any(existing == number for existing, _ in bindings):
            raise ValueError("Один персонаж или лист назначен нескольким референсам")
        used_names.add(name)
        bindings.append((number, latin_name(name)))
    if not bindings:
        return ""
    for number, name in bindings:
        absent = re.search(
            rf"(?is)\b{re.escape(name)}\s+MUST\s+NOT\s+APPEAR\s+PHYSICALLY\s+ANYWHERE\s+IN\s+THIS\s+\d+[- ]SECOND\s+CLIP",
            prompt,
        )
        if absent:
            raise ValueError(
                f"{name} запрещён в кадре на весь клип, но его <Picture {number}> прикреплён. "
                "Убери этот лист и ссылки на него из данного задания."
            )
    if re.search(r"<Subject\s+\d+>", prompt, re.IGNORECASE):
        return ""
    lines = [
        f"<Subject {index}> is {name}; only <Picture {number}> defines this character's "
        "face, body, clothing and identity. Keep this Subject separate from every other Subject."
        for index, (number, name) in enumerate(bindings, 1)
    ]
    return "CHARACTER REFERENCE BINDINGS:\n" + "\n".join(lines)


MOTION_VIDEO_ROLES = {"Движение", "Движение и камера"}

_NO_SPEECH = re.compile(
    r"\bno\s+(?:talking|dialogue|dialog|speech|lines|spoken\s+words)\b|\bwithout\s+(?:dialogue|speech|words)\b"
    r"|\b(?:nobody|no\s+one)\s+speaks\b|\bsilent\s+(?:video|scene|shot)\b"
    r"|без\s+(?:слов|реплик|диалог|речи)|никто\s+не\s+говорит|молча",
    re.IGNORECASE,
)


def prompt_forbids_speech(prompt: str) -> bool:
    return bool(_NO_SPEECH.search(prompt or ""))


def compile_character_swap_prompt(prompt: str, images: tuple[dict, ...], videos: tuple[dict, ...]) -> str:
    """Use a short, role-exact instruction for the experimental H3 character-swap adapter."""
    pairs = [(video_no, pair) for video_no, video in enumerate(videos, 1)
             for pair in video.get("job_performers", ())]
    if len(videos) != 1 or not pairs:
        raise ValueError("Character Swap требует одно видео и минимум одну явную пару «герой — исполнитель»")
    validate_replacement_prompt(prompt)
    lines = ["Replace only the mapped performers in <Video 1> with their assigned characters:"]
    for _, pair in pairs:
        owner = pair["owner"]
        parts = []
        for picture_no, image in enumerate(images, 1):
            if str(image.get("job_owner", "")).casefold() != owner.casefold():
                continue
            role = image.get("job_role", image.get("role"))
            if role == "Лицо героя":
                parts.append(f"face and identity from <Picture {picture_no}>")
            elif role == "Персонаж целиком":
                parts.append(f"identity and body from <Picture {picture_no}>")
            elif role == "Костюм":
                parts.append(f"clothing from <Picture {picture_no}>")
        if not parts:
            raise ValueError(f"Для {owner} нет назначенного изображения Character Swap")
        performer = pair.get("performer") or "the assigned performer"
        lines.append(f"- Replace {performer} with {owner}; {', '.join(parts)}.")
        lines.append(replacement_identity(owner, "<Video 1>", images))
    lines.append("Transfer the source performers' movement, pose sequence, timing and screen positions. "
                 "Preserve the source camera, framing, setting, lighting, objects and untargeted people. "
                 "Translate animal motion into natural human movement when the replacement is a person.")
    source_summary = ""
    if is_ir_prompt(prompt):
        match = re.search(r"(?ims)^summary:\s*(.*?)(?=^retention_analysis:|\Z)", prompt)
        if match:
            source_summary = " ".join(match.group(1).split())
    else:
        source_summary = " ".join(prompt.split())
    if source_summary:
        if len(source_summary) > 900:
            source_summary = source_summary[:900].rsplit(" ", 1)[0]
        lines.append(f"Scene intent: {source_summary}")
    # Audio stays a separate H3 conditioning concern; retain only explicit short audio sections.
    for name in ("overall_soundscape", "non_diegetic_music"):
        match = re.search(rf"(?ims)^{name}:\s*(.*?)(?=^[a-z_]+:|\Z)", prompt)
        if match:
            value = " ".join(match.group(1).split())
            if value and value.casefold() not in {"n/a", "na"}:
                lines.append(f"{name}: {value[:400]}")
    return "\n".join(lines)


def reference_videos(videos: Iterable[dict]) -> tuple[dict, ...]:
    """Videos that reach Ref2VA as <Video N>: every video except the «Продолжение» clip."""
    return tuple(video for video in videos if video.get("job_role", video.get("role")) != CONTINUATION_ROLE)


def validate_continuation(compiled, use_video_audio: bool) -> dict | None:
    """The «Продолжение» clip, or None. ValueError for combinations the GPU graph cannot render."""
    marked = [n for n, video in enumerate(compiled.videos, 1)
              if video.get("job_role", video.get("role")) == CONTINUATION_ROLE]
    if not marked:
        return None
    if len(marked) > 1:
        raise ValueError("«Продолжение» может быть только у одного видео задания")
    number = marked[0]
    if number != len(compiled.videos):
        raise ValueError("Видео «Продолжение» должно идти последним среди видео задания")
    if re.search(rf"<Video\s+{number}>", compiled.prompt):
        raise ValueError(f"<Video {number}> — «Продолжение»: это не референс H3, не упоминай его меткой в промпте")
    if use_video_audio:
        if compiled.audios:
            raise ValueError("«Продолжение» со звуком видео-референсов и отдельными аудио не поддерживается: "
                             "номера <Audio N> на GPU сдвинутся. Выключи звук видео или убери аудио")
        if re.search(rf"<Audio\s+{number}>", compiled.prompt):
            raise ValueError(f"<Audio {number}> — звук «Продолжения»: он уходит в продолжение дубля, не в референсы")
    return compiled.videos[number - 1]


def _check_official_prompt(compiled, effective: dict) -> str:
    """Block an official-format prompt whose structure or labels do not match this job."""
    use_audio = bool(effective.get("use_video_audio"))
    refs = [Reference("image", n, a.get("job_role", a.get("role", "")), a.get("job_owner", ""))
            for n, a in enumerate(compiled.images, 1)]
    # A «Продолжение» clip has no <Video N>/<Audio N> label on the GPU (it is a guide, not a reference).
    videos = reference_videos(compiled.videos)
    refs += [Reference("video", n, a.get("job_role", a.get("role", "")), a.get("job_owner", "")) for n, a in enumerate(videos, 1)]
    offset = len(videos) if use_audio else 0
    refs += [Reference("audio", offset + n, a.get("job_role", a.get("role", "")), a.get("job_owner", "")) for n, a in enumerate(compiled.audios, 1)]
    skeleton = build_skeleton(refs, use_video_audio=use_audio)
    skeleton.labels.update(f"<Picture {n}>" for n in range(1, len(compiled.images) + 1))
    skeleton.labels.update(f"<Video {n}>" for n in range(1, len(videos) + 1))
    skeleton.labels.update(f"<Audio {n}>" for n in range(1, offset + len(compiled.audios) + 1))
    completed = complete_subject_sources(rebind_subject_pictures(compiled.prompt, skeleton), skeleton)
    blocking = [p for p in validate_ir(completed, skeleton, []) if is_blocking(p)]
    if blocking:
        raise ValueError("Промпт в формате H3 не сходится с заданием: " + "; ".join(blocking))
    completed = source_swap_identity_contract(completed, compiled.images, videos)
    return add_motion_attribute_transfer(normalize_motion_retention(completed, videos), compiled.images, videos)


def source_swap_identity_contract(prompt: str, images: tuple[dict, ...], videos: tuple[dict, ...]) -> str:
    """Bind each mapped replacement's identity to its own pictures, not the source performer."""
    pairs = [(number, pair) for number, video in enumerate(videos, 1)
             if video.get("job_role", video.get("role")) == "Исходное видео"
             for pair in video.get("job_performers", ())]
    if not pairs:
        return prompt
    validate_replacement_prompt(prompt)
    lines = []
    for number, pair in pairs:
        owner = pair["owner"]
        tags = [f"<Picture {n}>" for n, asset in enumerate(images, 1)
                if str(asset.get("job_owner", "")).casefold() == owner.casefold()
                and asset.get("job_role", asset.get("role")) in {"Лицо героя", "Персонаж целиком", "Костюм"}]
        if not tags:
            raise ValueError(f"Для замены {owner} нужен собственный визуальный референс")
        lines.append(replacement_identity(owner, f"<Video {number}>", images))
    if not is_ir_prompt(prompt):
        return prompt.rstrip() + "\n\nCHARACTER IDENTITY FOR SOURCE REPLACEMENT:\n" + "\n".join(lines)
    sections = parse_ir(prompt)
    sections["retention_analysis"] = sections.get("retention_analysis", "").rstrip() + "\n" + "\n".join(lines)
    return render_ir(sections)


def add_motion_attribute_transfer(prompt: str, images: tuple[dict, ...], videos: tuple[dict, ...]) -> str:
    """Official-format jobs: every hero paired with a performer of a movement video gets attribute_transfer.

    The user's own retention line (0062f40a: "<Video 1>: partially_preserved - choreography ... camera
    composition") said nothing about hair, and the performers' upswept hair rode onto the heroes.
    """
    pictures: dict[str, list[str]] = {}
    for number, asset in enumerate(images, 1):
        owner = str(asset.get("job_owner", "") or "")
        if owner and asset.get("job_role", asset.get("role")) in {"Лицо героя", "Персонаж целиком"}:
            pictures.setdefault(owner.casefold(), []).append(f"<Picture {number}>")
    lines = []
    for number, video in enumerate(videos, 1):
        if video.get("job_role", video.get("role")) not in MOTION_VIDEO_ROLES:
            continue
        for pair in video.get("job_performers", ()):
            owned = pictures.get(pair["owner"].casefold())
            if owned:
                lines.append(f"<Video {number}> (motion of {pair['owner']}):"
                             + motion_transfer_sentence(pair["owner"], f"<Video {number}>", owned,
                                                        pair.get("performer", ""),
                                                        animal=hero_is_animal(prompt, pair["owner"])).replace("\nattribute_transfer:", " attribute_transfer -", 1))
    if not lines:
        return prompt
    match = re.search(r"(?ims)^retention_analysis:[ \t]*\n(.*?)(?=^[a-z_]+:[ \t]*$|\Z)", prompt)
    if not match:
        return prompt
    body = match.group(1).rstrip("\n")
    return prompt[:match.start(1)] + body + "\n\n" + "\n".join(lines) + "\n\n" + prompt[match.end(1):].lstrip("\n")


PAIR_VIDEO_ROLES = {"Движение", "Движение и камера", "Исходное видео"}
_HERO_ROLES = {"Лицо героя", "Персонаж целиком"}


# Compiler boilerplate alone gives about 8; broken swap prompts carry 12–25.
SWAP_NEGATIONS_MAX = 14
SWAP_NEGATION = re.compile(r"\b(?:no|never|not|don't|without)\b", re.IGNORECASE)
SWAP_PROMPT_MAX = 5000  # good swap prompts compile to 3–4k characters; the worst broken ones run 6–8.5k


def swap_recipe_gate(prompt: str, images, videos) -> None:
    """Pre-GPU check of a hero swap against the known failure patterns of the swap route."""
    paired = [v for v in videos if any(p.get("performer") or p.get("owner") for p in v.get("job_performers", ()))]
    if not paired:
        return
    roles = {v.get("job_role", v.get("role")) for v in paired}
    has_set = any(i.get("job_role", i.get("role")) == "Окружение" for i in images)
    if roles & MOTION_VIDEO_ROLES and has_set and "Исходное видео" not in roles:
        raise ValueError("Замена героев через «Движение и камера» с фото «Окружение» даёт чужую комнату и лишнего "
                         "исходного исполнителя. Для замены по сюжету видео нужна роль "
                         "«Исходное видео» и режим «Замена героя»")
    negations = len(SWAP_NEGATION.findall(prompt))
    if "Исходное видео" in roles and negations > SWAP_NEGATIONS_MAX:
        raise ValueError(f"В промпте замены {negations} запретов (no/never/not/without); сборка Studio даёт около 8, "
                         f"брак — 12–25 (предел {SWAP_NEGATIONS_MAX}). H3 видит названное в запрете: "
                         "опиши, что в кадре есть, вместо того, чего быть не должно")
    if "Исходное видео" in roles and len(prompt) > SWAP_PROMPT_MAX:
        raise ValueError(f"Промпт замены после сборки {len(prompt)} знаков; удачные замены собираются в 3–4 тыс. "
                         f"(предел {SWAP_PROMPT_MAX}). Сократи план шотов и убери повторяющиеся запреты")


def assign_performers(assets: list[dict], performers: dict, known_owners: set[str]) -> None:
    """Which visible performer each hero replaces, per reference video (job_performers / job_keep_others).

    One hero takes an unowned movement video automatically; with several heroes every movement video
    needs an owner or explicit pairs; «Авто» never binds motion to a hero, so it is refused once a hero
    is named (7c93e492: the performer became a second person and the dance was lost).
    """
    names = {a["job_owner"].casefold(): latin_name(a["job_owner"]) for a in assets
             if a.get("job_owner") and a.get("job_role", a.get("role")) in _HERO_ROLES}
    by_id = {a["asset_id"]: a for a in assets}
    if set(performers) - set(by_id):
        raise ValueError("Пары «кого заменяет» не соответствуют файлам задания")
    for asset in assets:
        if asset["kind"] != "video":
            continue
        role = str(asset.get("job_role", asset.get("role")) or "")
        label = asset.get("original_name") or "видео"
        spec = performers.get(asset["asset_id"])
        pairs: list[dict] = []
        keep_others = False
        if role.startswith("Авто") and known_owners:
            raise ValueError(f"Выбери роль для {label}: «Движение», «Движение и камера» или «Исходное видео» — "
                             "«Авто» не привязывает движение к герою")
        if spec is not None:
            if not isinstance(spec, dict) or set(spec) - {"pairs", "keep_others"}:
                raise ValueError("Пары исполнителей: нужны pairs и keep_others")
            if role not in PAIR_VIDEO_ROLES:
                raise ValueError("«Кого заменяет» задаётся только для видео «Движение», «Движение и камера» или «Исходное видео»")
            raw = spec.get("pairs", [])
            if not isinstance(raw, list) or not 1 <= len(raw) <= 4:
                raise ValueError("Укажи от 1 до 4 пар «герой — кого заменяет»")
            seen_owner, seen_performer = set(), set()
            for item in raw:
                if not isinstance(item, dict) or set(item) - {"owner", "performer", "relative_scale_percent"}:
                    raise ValueError("Пара: owner, performer и необязательный relative_scale_percent")
                owner, performer = item.get("owner", ""), item.get("performer", "")
                owner = resolve_owner(owner, list(names.values())) or owner
                if not isinstance(owner, str) or owner.strip().casefold() not in names:
                    raise ValueError(f"Герой {owner!r} из пары не привязан к своему лицу или полному росту")
                if not isinstance(performer, str) or len(performer.strip()) > 80 or "\n" in performer:
                    raise ValueError("Описание исполнителя — одна строка до 80 символов")
                key, who = owner.strip().casefold(), performer.strip()
                if key in seen_owner or (who and who.casefold() in seen_performer):
                    raise ValueError("Каждый герой и каждый исполнитель — только в одной паре")
                seen_owner.add(key)
                seen_performer.add(who.casefold())
                pair = {"owner": names[key], "performer": who}
                if "relative_scale_percent" in item:
                    scale = item["relative_scale_percent"]
                    if type(scale) is not int or not 25 <= scale <= 200:
                        raise ValueError("Рост героя относительно первого: целое число от 25 до 200%")
                    pair["relative_scale_percent"] = scale
                pairs.append(pair)
            keep_others = spec.get("keep_others", False)
            if not isinstance(keep_others, bool):
                raise ValueError("keep_others: да или нет")
        elif role in MOTION_VIDEO_ROLES and asset.get("job_owner"):
            pairs = [{"owner": names.get(asset["job_owner"].casefold(), asset["job_owner"]), "performer": ""}]
        elif role in MOTION_VIDEO_ROLES and len(names) == 1:
            pairs = [{"owner": next(iter(names.values())), "performer": ""}]
        elif role in MOTION_VIDEO_ROLES and len(names) > 1:
            raise ValueError(f"Героев несколько: укажи, чьё движение в {label} (владелец видео или пары «кого заменяет»)")
        asset["job_performers"] = pairs
        asset["job_keep_others"] = keep_others
        if len(pairs) == 1 and not asset.get("job_owner") and role in MOTION_VIDEO_ROLES:
            asset["job_owner"] = pairs[0]["owner"]


def hero_is_animal(prompt: str, name: str) -> bool:
    """The hero's own Subject line (else the first sentence naming them) speaks of an animal."""
    from app.animal_realism import _ANIMAL_WORDS
    line = re.search(rf"(?im)^<Subject\s+\d+>\s+is\s+{re.escape(name)}\b[^\n]*", prompt or "")
    if not line:
        line = re.search(rf"(?i)[^.\n]*\b{re.escape(name)}\b[^.\n]*", prompt or "")
    return bool(line and _ANIMAL_WORDS.search(line.group(0)))


def motion_transfer_sentence(name: str, video: str, pictures: list[str], performer: str = "",
                             with_position: bool = False, animal: bool = False) -> str:
    """Official H3 attribute_transfer for a movement video: only the performer's motion moves over.

    H3 copies whatever a reference video shows; without this, a long-haired or bearded performer
    in the movement video leaked hair and beard onto the hero (2026-10-05). Written as positives:
    H3 is guidance-distilled and has no negative prompt.
    """
    source = " and ".join(dict.fromkeys(pictures))
    who = f"{performer} in {video}" if performer else f"the separate performer in {video}"
    what = "body motion, screen position, pose progression and action timing" if with_position else \
        "body motion, pose progression and action timing"
    if animal:
        # Otherwise an animal hero gets «hairstyle, facial hair or clean-shaven face … costume reference».
        return (f"\nattribute_transfer: only the {what} of {who} are transferred onto {name}. {name}'s face, ears, "
                f"eyes, fur color and markings, tail and body proportions come only from {source} and stay exactly as "
                f"pictured in every frame, including fast turns and motion blur. Fur moves only as secondary motion "
                f"of {name}'s own pictured coat.")
    return (f"\nattribute_transfer: only the {what} of {who} are transferred onto {name}. {name}'s hairstyle, hair length, hair color, "
            f"facial hair or clean-shaven face, face and visible anatomical features come only from {source} and stay "
            f"exactly as pictured in every frame, including fast turns and motion blur. Hair moves "
            f"only as secondary motion of {name}'s own pictured hairstyle. Clothing follows its assigned costume reference.")


def owned_character_contract(prompt: str, images: tuple[dict, ...], videos: tuple[dict, ...] = ()) -> str:
    """Compile UI-selected face/body/costume groups into H3 Subjects.

    With one hero and one movement video, the motion goes inside the Subject line, the
    form the official H3 guide uses: "... whose walking motion comes from <Video 1>".
    """
    if re.search(r"<Subject\s+\d+>", prompt, re.IGNORECASE):
        return ""
    owners: dict[str, list[str]] = {}
    owner_pictures: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for number, asset in enumerate(images, 1):
        owner = asset.get("job_owner", "")
        role = asset.get("job_role", asset.get("role"))
        if not owner or role not in {"Лицо героя", "Персонаж целиком", "Костюм"}:
            continue
        key = owner.casefold()
        names.setdefault(key, owner)
        component = {"Лицо героя": "face and identity", "Персонаж целиком": "body and silhouette",
                     "Костюм": "clothing and carried props"}[role]
        owners.setdefault(key, []).append(f"{component} from <Picture {number}>")
        if role != "Костюм":
            owner_pictures.setdefault(key, []).append(f"<Picture {number}>")
    if not owners:
        return ""
    transfer = ""
    for number, video in enumerate(videos, 1):
        tag = f"<Video {number}>"
        source_scene = video.get("job_role", video.get("role")) == "Исходное видео"
        for pair in video.get("job_performers", ()):
            key = pair["owner"].casefold()
            if key in owners and owner_pictures.get(key):
                transfer += motion_transfer_sentence(names[key], tag, owner_pictures[key], pair["performer"],
                                                     with_position=source_scene,
                                                     animal=hero_is_animal(prompt, names[key]))
        if video.get("job_keep_others"):
            transfer += (f"\nEvery other person in {tag} stays exactly as in the video: the same face, hair, "
                         "clothing, position and movement, unchanged from the source.")
    return "CHARACTER REFERENCE BINDINGS:\n" + "\n".join(
        f"<Subject {index}> is {names[key]}; " + "; ".join(parts) +
        ". This is one character. Do not transfer these traits to any other Subject."
        for index, (key, parts) in enumerate(owners.items(), 1)
    ) + transfer


_GENERATED_BLOCK_PREFIXES = ("REFERENCE ROLE CONTRACT: ", "CHARACTER REFERENCE BINDINGS:")


def strip_generated_blocks(prompt: str) -> str:
    """Remove Studio-generated blocks that a reopened or pasted job prompt already carries.

    The store rebuilds them for every job; keeping old copies stacked another layer on each retry.
    """
    generated = {text.strip() for text in (*VISUAL_MODES.values(), NATIVE_AUDIO_DIRECTION, VIDEO_PERFORMANCE_DIRECTION, VOICE_PERFORMANCE_DIRECTION) if text}
    blocks = re.split(r"\n\s*\n", prompt.strip())
    kept = [block for block in blocks
            if block.strip() not in generated and not block.lstrip().startswith(_GENERATED_BLOCK_PREFIXES)]
    return "\n\n".join(kept).strip()


def _media_duration_seconds(path: str | Path) -> float | None:
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10, check=False,
        )
        duration = float(result.stdout.strip()) if result.returncode == 0 else 0.0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return duration if duration > 0 else None


def apply_visual_mode(prompt: str, mode: str) -> str:
    if mode not in VISUAL_MODES:
        raise ValueError("Неизвестный визуальный режим")
    directions = [item for item in (VISUAL_MODES[mode], NATIVE_AUDIO_DIRECTION) if item]
    prefix = "\n\n".join(directions)
    return f"{prefix}\n\n{prompt}" if prefix and not prompt.startswith(f"{prefix}\n\n") else prompt


def apply_ir_animation_mode(prompt: str, mode: str) -> str:
    """Keep the IR sections and source bindings, while honoring the selected animation mode."""
    if mode not in {"animation3d", "animation2d"}:
        return prompt
    sections = parse_ir(prompt)
    direction = VISUAL_MODES[mode]
    description = sections.get("detailed_description", "")
    if direction in description:
        return prompt
    sections["detailed_description"] = direction + "\n\n" + description
    return render_ir(sections)


def validate_visual_mode(prompt: str, mode: str) -> None:
    """Reject a selected style that directly contradicts the authored scene."""
    style_lines = re.findall(r"(?im)^\s*(?:STYLE|СТИЛЬ)\s*:\s*([^\n]+)", prompt)
    style = " ".join(style_lines).casefold()
    explicit_2d = bool(re.search(r"\b2d\b|\b2-д\b|\b2д\b|двумерн", style))
    explicit_3d = bool(re.search(r"\b3d\b|\b3-д\b|\b3д\b|тр[её]хмерн", style))
    if mode == "realistic" and explicit_2d and not explicit_3d:
        raise ValueError("Промпт требует 2D, а выбран режим «Реализм». Выбери «2D-мультфильм» перед отправкой.")
    if mode == "animation2d" and explicit_3d and not explicit_2d:
        raise ValueError("Промпт требует 3D, а выбран режим «2D-мультфильм». Проверь сценарий.")
    if mode == "animation3d" and explicit_2d and not explicit_3d:
        raise ValueError("Промпт требует 2D, а выбран режим «3D-мультфильм». Проверь сценарий.")


class StudioStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets_dir = self.root / "assets"
        self.results_dir = self.root / "results"
        self.assets_dir.mkdir(exist_ok=True)
        self.results_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "studio.sqlite3"
        if self.db_path.exists():
            with sqlite3.connect(self.db_path) as source:
                columns = {row[1] for row in source.execute("PRAGMA table_info(jobs)")}
                if columns and not {"sampling_value", "sampling_max", "hidden", "director_json"} <= columns:
                    backup_dir = self.root / "code-backups"
                    backup_dir.mkdir(exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    with sqlite3.connect(backup_dir / f"studio-pre-migrate-{stamp}.sqlite3") as backup:
                        source.backup(backup)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY, kind TEXT NOT NULL, number INTEGER NOT NULL,
                    role TEXT NOT NULL, original_name TEXT NOT NULL, mime TEXT NOT NULL,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL,
                    soundtrack_number INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(kind, number)
                );
                CREATE TABLE IF NOT EXISTS counters (kind TEXT PRIMARY KEY, last_number INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, state TEXT NOT NULL, original_prompt TEXT NOT NULL,
                    compiled_prompt TEXT NOT NULL, settings_json TEXT NOT NULL,
                    manifest_json TEXT NOT NULL, mapping_json TEXT NOT NULL,
                    remote_job_id TEXT, output_path TEXT, error TEXT,
                    director_json TEXT,
                    sampling_value INTEGER, sampling_max INTEGER,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS submission_receipts (
                    receipt_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, job_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    state TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS draft (
                    id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS draft_history (
                    revision INTEGER PRIMARY KEY, payload TEXT NOT NULL, saved_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS queue_control (
                    id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(assets)")}
            if "soundtrack_number" not in columns:
                db.execute("ALTER TABLE assets ADD COLUMN soundtrack_number INTEGER")
            job_columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "output_sha256" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN output_sha256 TEXT")
            if "production_spec_json" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN production_spec_json TEXT")
            if "hidden" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
            if "director_json" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN director_json TEXT")
            if "sampling_value" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN sampling_value INTEGER")
            if "sampling_max" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN sampling_max INTEGER")
            if "budget_json" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN budget_json TEXT")
            draft_columns = {row[1] for row in db.execute("PRAGMA table_info(draft)")}
            if "revision" not in draft_columns:
                db.execute("ALTER TABLE draft ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_asset(self, *, kind: str, role: str, original_name: str,
                  mime: str, size: int, sha256: str, path: Path) -> dict:
        if kind not in {"image", "video", "audio"} or not path.is_file():
            raise ValueError("Недопустимый тип или отсутствующий файл")
        if not path.resolve().is_relative_to(self.assets_dir.resolve()):
            raise ValueError("Путь файла должен быть в управляемом хранилище")
        asset_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT last_number FROM counters WHERE kind=?", (kind,)).fetchone()
            number = (row[0] if row else 0) + 1
            db.execute("INSERT INTO counters(kind,last_number) VALUES (?,?) ON CONFLICT(kind) DO UPDATE SET last_number=excluded.last_number",
                       (kind, number))
            soundtrack_number = None
            if kind == "video":
                audio_row = db.execute("SELECT last_number FROM counters WHERE kind='audio'").fetchone()
                soundtrack_number = (audio_row[0] if audio_row else 0) + 1
                db.execute("INSERT INTO counters(kind,last_number) VALUES ('audio',?) ON CONFLICT(kind) DO UPDATE SET last_number=excluded.last_number",
                           (soundtrack_number,))
            db.execute("""INSERT INTO assets(asset_id,kind,number,role,original_name,mime,size,sha256,path,soundtrack_number)
                          VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       (asset_id, kind, number, role, original_name, mime, size, sha256, str(path), soundtrack_number))
        return self.get_asset(asset_id)

    def get_asset(self, asset_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
            return dict(row) if row else None

    def find_asset(self, *, sha256: str, kind: str, role: str | None = None) -> dict | None:
        """Newest asset with this content (and role) whose file still exists."""
        query = "SELECT * FROM assets WHERE sha256=? AND kind=?" + (" AND role=?" if role is not None else "")
        params = (sha256, kind) + ((role,) if role is not None else ())
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(query + " ORDER BY created_at DESC, rowid DESC", params)]
        return next((row for row in rows if Path(row["path"]).is_file()), None)

    def list_assets(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM assets ORDER BY created_at, rowid")]

    def create_job(self, *, prompt: str, asset_ids: list[str], settings: dict,
                   reference_numbers: dict[str, int] | None = None,
                   reference_roles: dict[str, str] | None = None,
                   reference_owners: dict[str, str] | None = None,
                   style_adapters: Iterable[str] | None = None,
                   reference_performers: dict[str, dict] | None = None,
                   production_spec: dict | None = None,
                   receipt_key: str | None = None, receipt_hash: str | None = None) -> dict:
        """style_adapters: LoRAs the GPU reports in /api/capabilities; None (GPU unreachable) allows only 'none'.

        reference_performers: {video asset_id: {"pairs": [{"owner": hero, "performer": "the woman on the left"}],
        "keep_others": bool}} — which visible performer each hero replaces in a video with several people.
        """
        if receipt_key:
            if not receipt_hash:
                raise ValueError("Для квитанции требуется хэш запроса")
            with self.connect() as db:
                old = db.execute("SELECT request_hash,job_id FROM submission_receipts WHERE receipt_key=?", (receipt_key,)).fetchone()
            if old:
                if old["request_hash"] != receipt_hash:
                    raise ValueError("ID отправки уже использован с другим запросом")
                return self.get_job(old["job_id"])
        if not isinstance(settings, dict):
            raise ValueError("Параметры генерации должны быть объектом")
        writer_package = "```studio-settings" in prompt
        prompt, settings = prompt_settings_package(prompt, settings)
        settings = {key: value for key, value in settings.items() if key not in LEGACY_SETTINGS_KEYS}
        unknown = set(settings) - SETTINGS_KEYS
        if unknown:
            # A typo or a field this Studio does not support used to be accepted and silently ignored.
            raise ValueError(f"Неизвестные параметры генерации: {', '.join(sorted(map(str, unknown)))}")
        if settings.get("backend") == "minimax_api":
            if settings.get("cloud_resolution", "768P") != "768P":
                raise ValueError("MiniMax API в Studio: сейчас разрешено только 768P")
            if settings.get("aspect_ratio", "9:16") != "9:16":
                raise ValueError("MiniMax API в Studio: только вертикальный формат 9:16")
        prompt = strip_generated_blocks(prompt)
        if not prompt.strip():
            raise ValueError("Промпт пуст")
        from .production_gates import shot_cuts_to_at  # «[Shot 2: a–b s]» → «[Shot 2] At …» (H3 cuts late on ranges)
        prompt = shot_cuts_to_at(prompt, settings.get("duration"))
        original_prompt = prompt.strip()
        if production_spec is not None:
            from .production_compile import compile_production
            prompt, settings, reference_owners, production_spec = compile_production(
                prompt, production_spec, asset_ids, settings, reference_owners)
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("Повторный asset_id в задании")
        assets = []
        for asset_id in asset_ids:
            asset = self.get_asset(asset_id)
            if not asset:
                raise ValueError(f"Референс {asset_id} не найден")
            assets.append(asset)
        if (settings.get("keep_source_audio") is True and settings.get("use_video_audio")
                and not settings.get("character_refinement") and not any(a["kind"] == "audio" for a in assets)
                # A prompt that names the soundtrack (<Audio N>) keeps it: dropping it would leave
                # <Audio 1> pointing at nothing and refuse the job.
                and not re.search(r"<Audio\s+\d+>", prompt or "")):
            # The source track replaces H3's sound after the render, so its soundtrack only cost time as reference
            # tokens in every step: 15 s swaps 2335–2947 s with it vs 1407–1460 s without (02.11). No audio files:
            # <Audio N> numbering does not depend on it.
            settings = {**settings, "use_video_audio": False}
        reference_roles = reference_roles or {}
        reference_owners = reference_owners or {}
        if set(reference_roles) - set(asset_ids):
            raise ValueError("Роли референсов не соответствуют файлам задания")
        if set(reference_owners) - set(asset_ids):
            raise ValueError("Имена героев не соответствуют файлам задания")
        for asset in assets:
            explicit_role = asset["asset_id"] in reference_roles
            role = reference_roles.get(asset["asset_id"], asset.get("role", "Референс"))
            if role == "Авто — определить по содержимому":
                asset["job_role"] = role
                continue
            if not explicit_role and role not in REFERENCE_ROLE_PROMPTS:
                continue
            role_contract = REFERENCE_ROLE_PROMPTS.get(role)
            if not role_contract or role_contract[0] != asset["kind"]:
                raise ValueError(f"Неизвестная роль референса или неподходящий тип файла: {role}")
            asset["job_role"] = role
        for asset in assets:
            owner = reference_owners.get(asset["asset_id"], "")
            if not isinstance(owner, str) or len(owner.strip()) > 80 or "\n" in owner:
                raise ValueError("Имя героя в референсе должно быть одной строкой до 80 символов")
            role_for_owner = asset.get("job_role", asset.get("role"))
            if owner and (asset["kind"], role_for_owner) not in {
                ("image", "Лицо героя"), ("image", "Персонаж целиком"),
                ("image", "Костюм"), ("video", "Актёрская игра"),
                ("video", "Движение"), ("video", "Движение и камера"),
                ("audio", "Голос"),
            }:
                raise ValueError("Имя героя можно назначить его изображению, видео движения или актёрской игры и голосу")
            asset["job_owner"] = owner.strip()
        if is_ir_prompt(prompt):
            subjects = re.findall(r"(?m)^<Subject \d+>\s+is\s+([^,.:]+)",
                                  parse_ir(prompt).get("subject_definitions", ""))
            if len(subjects) > 1:
                unowned = [a for a in assets if a["kind"] == "image" and
                           a.get("job_role", a.get("role")) in {"Лицо героя", "Персонаж целиком", "Костюм"}
                           and not a["job_owner"]]
                if unowned:
                    raise ValueError("При нескольких героях выбери имя для каждого фото лица и костюма; порядок загрузки не определяет владельца")
        known_owners = {a["job_owner"].casefold() for a in assets if a.get("job_role", a.get("role")) in
                        {"Лицо героя", "Персонаж целиком"} and a["job_owner"]}
        if len(known_owners) == 1:
            # One hero: an unowned costume / acting video / motion video / voice can only be theirs. Left unowned, the
            # costume became its own Subject and H3 drew a second person wearing it (7c93e492, 2026-10-05).
            hero = next(a["job_owner"] for a in assets if a["job_owner"] and a.get("job_role", a.get("role")) in
                        {"Лицо героя", "Персонаж целиком"})
            for asset in assets:
                if not asset["job_owner"] and (asset["kind"], asset.get("job_role", asset.get("role"))) in {
                        ("image", "Костюм"), ("video", "Актёрская игра"),
                        ("video", "Движение"), ("video", "Движение и камера"), ("audio", "Голос")}:
                    asset["job_owner"] = hero
        if len(known_owners) > 1:
            for asset in assets:
                if not asset["job_owner"] and (asset["kind"], asset.get("job_role", asset.get("role"))) in {
                        ("image", "Костюм"), ("video", "Актёрская игра"), ("audio", "Голос")}:
                    raise ValueError("При нескольких героях назначь владельца каждому костюму, видео актёрской игры и голосу")
        assign_performers(assets, reference_performers or {}, known_owners)
        for asset in assets:
            if asset.get("job_role", asset.get("role")) in {"Костюм", "Актёрская игра", "Движение", "Движение и камера", "Голос"} and asset["job_owner"] and asset["job_owner"].casefold() not in known_owners:
                raise ValueError(f"Референс героя {asset['job_owner']} не привязан к его лицу или полному росту")
        if "⟦удалённый " in prompt or "⟦неизвестный " in prompt:
            raise ValueError("Промпт ссылается на удалённый или неизвестный референс")
        if reference_numbers is not None:
            if set(reference_numbers) != set(asset_ids):
                raise ValueError("Номера референсов не соответствуют файлам задания")
            soundtrack_offset = sum(a["kind"] == "video" for a in assets) if settings.get("use_video_audio") else 0
            expected = {"image": 1, "video": 1, "audio": soundtrack_offset + 1}
            video_index = 0
            for asset in assets:
                number = reference_numbers[asset["asset_id"]]
                if type(number) is not int or number != expected[asset["kind"]]:
                    raise ValueError(f"Номера {asset['kind']} должны идти подряд в порядке задания")
                asset["number"] = number
                expected[asset["kind"]] += 1
                if asset["kind"] == "video" and settings.get("use_video_audio"):
                    video_index += 1
                    asset["soundtrack_number"] = video_index
        compiled = compile_reference_manifest(prompt, assets,
                                               use_video_audio=bool(settings.get("use_video_audio", False)))
        validate_image_bindings(compiled.prompt, len(compiled.images), {
            number for number, image in enumerate(compiled.images, 1)
            if image.get("job_role") in REFERENCE_ROLE_PROMPTS})
        for match in re.finditer(
            r"<Picture\s+(\d+)>\s+controls[^.\n]{0,240}\b(?:facial identity|facial geometry|identity of the hero)\b",
            prompt, flags=re.IGNORECASE,
        ):
            number = int(match.group(1))
            picture = next((item for item in compiled.images if item["number"] == number), None)
            if picture and picture.get("job_role", picture.get("role")) not in {"Лицо героя"}:
                raise ValueError(
                    f"Промпт назначает <Picture {number}> лицом героя, но роль файла другая. "
                    "Выбери для него «Лицо героя» перед отправкой."
                )
        continuation = validate_continuation(compiled, bool(settings.get("use_video_audio", False)))
        if settings.get("continuation_trim_frames") is not None:
            from app.continuation import CLIP_FRAMES
            if type(settings["continuation_trim_frames"]) is not int or settings["continuation_trim_frames"] != CLIP_FRAMES or not continuation:
                raise ValueError("Удаление перекрытия возможно только для продолжения на 22 кадра")
        ref_videos = reference_videos(compiled.videos)
        if not compiled.images and not compiled.videos:
            raise ValueError("Для Ref2VA нужно фото или видео")
        if continuation and not compiled.images and not ref_videos:
            raise ValueError("Для «Продолжения» нужно ещё фото героя/окружения или видео-референс")
        effective = dict(settings)
        effective["director_config"] = normalize_director_config(effective.get("director_config"))
        target_duration = effective.get("duration", 5)
        if isinstance(target_duration, (int, float)) and not isinstance(target_duration, bool):
            for video in compiled.videos:
                role = video.get("job_role", video.get("role"))
                if role not in {"Движение", "Движение и камера"}:
                    continue
                reference_duration = _media_duration_seconds(video["path"])
                if (reference_duration is not None and reference_duration + 0.25 < target_duration
                        and effective.get("allow_short_motion_reference") is not True):
                    raise ValueError(
                        f"Видео движения длится {reference_duration:.1f} с, а ролик задан на "
                        f"{target_duration:g} с. Добавь более длинный референс или сначала подготовь "
                        "версию с явно выбранным повтором/продолжением движения либо подтверди "
                        "продолжение движения по промпту."
                    )
                if reference_duration is not None and reference_duration + 0.25 < target_duration:
                    effective["allow_short_motion_reference"] = True
        effective["color_grade"] = normalize_color_grade(effective.get("color_grade"))
        color_match = normalize_color_match(effective.get("color_match"), len(compiled.images))
        if color_match:
            role = compiled.images[color_match["picture"] - 1].get("job_role", "")
            if role not in COLOR_MATCH_REFERENCE_ROLES:
                raise ValueError("Образец цвета — последний кадр предыдущего ролика (роль «Кадр сцены» или «Окружение»)")
            effective["color_match"] = color_match
        else:
            effective.pop("color_match", None)  # absent, not null: settings of other jobs unchanged
        effective["film_look"] = normalize_film_look(effective.get("film_look"))
        camera_effect = normalize_camera_effect(effective.get("camera_effect"))
        if camera_effect:
            effective["camera_effect"] = camera_effect
        else:
            effective.pop("camera_effect", None)  # absent: settings of other jobs unchanged
        refinement = normalize_character_refinement(effective.get("character_refinement"), len(compiled.images))
        if refinement:
            if continuation:
                raise ValueError("Уточнение героев нельзя совмещать с «Продолжением» дубля")
            if len(compiled.videos) != 1 or compiled.audios:
                raise ValueError("Уточнение требует один готовый ролик; звук берётся из него")
            if effective.get("camera_state") or effective.get("color_grade") or effective.get("color_match") or effective.get("film_look") or effective.get("style_adapter", "none") != "none":
                raise ValueError("Уточнение сохраняет исходную сцену; отключи дополнительные изменения камеры и стиля")
            effective["emotional_direction"] = False
            effective["use_video_audio"] = True
        effective["character_refinement"] = refinement
        guide_frames = effective.get("guide_frames", [])
        if guide_frames is None:
            guide_frames = []
        if not isinstance(guide_frames, list):
            raise ValueError("guide_frames должен быть списком пар [номер кадра сцены, позиция кадра]")
        for guide in guide_frames:
            if (not isinstance(guide, (list, tuple)) or len(guide) != 2
                    or type(guide[0]) is not int or type(guide[1]) is not int):
                raise ValueError("Неверный формат guide-кадра")
            image_number, frame_idx = guide
            image = next((item for item in compiled.images if item["number"] == image_number), None)
            if not image or image.get("job_role", image.get("role")) != "Кадр сцены":
                raise ValueError("Для guide-кадра выбери референс с ролью «Кадр сцены»")
        role_directives = []
        for kind, label, group in (("image", "Picture", compiled.images),
                                   ("video", "Video", compiled.videos),
                                   ("audio", "Audio", compiled.audios)):
            for dense, asset in enumerate(group, 1):
                role = asset.get("job_role", asset.get("role", "Референс"))
                contract = REFERENCE_ROLE_PROMPTS.get(role)
                if role == CONTINUATION_ROLE:
                    continue  # no <Video N> on the GPU; app/graph.py writes the continuation sentence
                if contract and contract[0] == kind:
                    owner = asset.get("job_owner")
                    ownership = f" belongs to {owner} only and" if owner else ""
                    role_directives.append(f"<{label} {dense}>{ownership} {contract[1]}.")
        official_format = is_ir_prompt(compiled.prompt)
        prompt_blocks = effective.get("prompt_blocks", "studio")
        if prompt_blocks not in ("studio", "none"):
            raise ValueError("prompt_blocks: studio или none")
        # Agent-written prompt without Studio's generic blocks (AUDIO/VOICE/VISUAL/HERO FACE/VIDEO
        # PERFORMANCE/CLEAN FRAME...): the causal encoder reads everything placed before the agent's
        # text into every later token (attention probe: 172 of 374 words were Studio's). Only the factual
        # reference roles and hero bindings stay, after the agent's text. Opt-in until an A/B decides;
        # the picture-only golden route and saved jobs keep "studio".
        clean_prompt = prompt_blocks == "none" and not official_format
        if clean_prompt:
            effective["prompt_blocks"] = "none"
        else:
            effective.pop("prompt_blocks", None)
        if official_format:
            # Official H3 six-section prompt (scenarist): it already carries subjects, roles, voices
            # and camera in the trained format; Studio's legacy blocks would break it.
            compiled_prompt = _check_official_prompt(compiled, effective)
        elif clean_prompt:
            compiled_prompt = compiled.prompt.rstrip()
            if role_directives:
                compiled_prompt += "\n\nReference roles: " + " ".join(role_directives)
        elif role_directives:
            compiled_prompt = "REFERENCE ROLE CONTRACT: " + " ".join(role_directives) + "\n\n" + compiled.prompt
        else:
            compiled_prompt = compiled.prompt
        if not official_format:
            character_contract = owned_character_contract(compiled_prompt, compiled.images, ref_videos)
            if not character_contract:
                character_contract = character_reference_contract(compiled_prompt, len(compiled.images))
            if character_contract and clean_prompt:
                compiled_prompt = compiled_prompt + "\n\n" + character_contract
            elif character_contract:
                compiled_prompt = character_contract + "\n\n" + compiled_prompt
        # Picture-only jobs (golden graph in tests/golden) always keep this block; a
        # movement-video scene that says "no talking" must not be told to cast voices.
        if not official_format and not clean_prompt and not (ref_videos and prompt_forbids_speech(prompt)):
            compiled_prompt = VOICE_PERFORMANCE_DIRECTION + "\n\n" + compiled_prompt
        # Keep the picture-only golden graph byte-for-byte stable; this default is for video shots.
        if ref_videos and not clean_prompt and not any(v.get("job_role", v.get("role")) == "Движение и камера" for v in ref_videos):
            compiled_prompt = add_default_hero_face_focus(compiled_prompt, compiled.images, official_format)
        # Structural control (pose/canny ControlNet) was removed; old saved jobs carry "none".
        if effective.get("motion_control", "none") != "none":
            raise ValueError("Контроль позы/контуров (ControlNet) удалён. Для переноса движения "
                             "назначь видео роль «Движение и камера»")
        effective["motion_control"] = "none"
        mode = effective.get("mode", DEFAULTS["mode"])
        if mode not in VISUAL_MODES:
            raise ValueError("Неизвестный визуальный режим")
        if mode == "replace" and compiled.images and ref_videos and not refinement:
            if len(ref_videos) != 1:
                raise ValueError(
                    "Для замены героя добавь ровно один видео-референс"
                )
            video_role = str(ref_videos[0].get("job_role", ref_videos[0].get("role")) or "")
            if not video_role or video_role.startswith("Авто"):
                raise ValueError("Укажи роль видео: «Движение и камера» для переноса движения или «Исходное видео» для сцены")
            if video_role == "Движение":
                raise ValueError("Для замены героя в исходной сцене выбери роль видео «Исходное видео» и свяжи героев с исполнителями; "
                                 "для переноса движения в новую сцену — «Движение и камера»")
        validate_visual_mode(prompt, mode)
        style_adapter = effective.get("style_adapter", "none")
        # Character Swap edits the source frame in place: it keeps the video's setting and the performer's
        # hair (0062f40a, 2026-10-08: the dancers' upswept hair stayed on the heroes). Only «Исходное видео»
        # asks for that; a «Движение» video lends motion alone and renders as a dance from a photo:
        # plain Ref2VA + attribute_transfer, the hero's hair and clothes from their own pictures.
        source_scene = any(str(asset.get("job_role", asset.get("role")) or "") == "Исходное видео"
                           for asset in ref_videos)
        mapped_replacement = bool(compiled.images and source_scene and any(
            asset.get("job_performers") for asset in ref_videos
        ))
        replacement_job = bool(compiled.images and ref_videos and source_scene
                               and (mode == "replace" or mapped_replacement))
        motion_only = bool(compiled.images and ref_videos and not source_scene)
        if motion_only and style_adapter == "auto" and style_adapters is not None:
            people = any(a["kind"] == "image" and a.get("job_role", a.get("role")) in _HERO_ROLES for a in compiled.images)
            style_adapter, reason = select_adapter(prompt, "realistic", set(style_adapters or ()), people=people)
            effective["lora_auto_reason"] = "movement video lends motion only: " + reason
        elif replacement_job and effective.get("no_lora_swap_trial") is True:
            if style_adapter != "none":
                raise ValueError("Проба замены без LoRA требует style_adapter=none")
            effective["lora_auto_reason"] = "explicit no-LoRA comparison; identity requires visual QC"
        elif replacement_job and effective.get("backend") == "minimax_api" and style_adapter in {"none", "auto"}:
            # Cloud V2 has no ComfyUI LoRA input. Its reference images and videos
            # still carry the visual swap request; the result requires full QC.
            style_adapter = "none"
            effective["lora_auto_reason"] = "MiniMax API references; no local LoRA endpoint"
        elif replacement_job and style_adapter in {"none", "auto"}:
            swap_lora = effective.get("swap_lora", "v1")
            if swap_lora not in SWAP_LORAS:
                raise ValueError("LoRA замены: v1 или v1.5")
            style_adapter = SWAP_LORAS[swap_lora]
            if style_adapter not in set(style_adapters or ()):
                raise ValueError(f"Character Swap LoRA{' V1.5' if swap_lora == 'v1.5' else ''} не объявлена GPU API. Задание замены не отправлено, чтобы Ref2VA не оставил исходных исполнителей.")
            effective["lora_auto_reason"] = "explicit hero replacement with mapped video performer"
        elif replacement_job and style_adapter in set(SWAP_LORAS.values()) and effective.get("swap_lora") \
                and SWAP_LORAS.get(effective["swap_lora"]) != style_adapter:
            raise ValueError(f"style_adapter {style_adapter} и swap_lora {effective['swap_lora']} указывают разные "
                             "LoRA замены; оставь одно")
        elif replacement_job and style_adapter not in set(SWAP_LORAS.values()):
            raise ValueError("Для замены героев требуется character_swap; стилевые LoRA нельзя смешивать с этим экспериментальным маршрутом")
        elif style_adapter == "auto" and effective.get("backend") == "minimax_api":
            style_adapter = "none"
            effective["lora_auto_reason"] = "MiniMax API references; no local LoRA endpoint"
        elif style_adapter == "auto" and style_adapters is None:
            # GPU unreachable: its LoRAs are unknown. Keep "auto" and let worker.submit choose with the
            # GPU's real /api/capabilities, instead of freezing the job to "none".
            effective["lora_auto_reason"] = "GPU недоступен при постановке — LoRA выбирается при отправке"
        elif style_adapter == "auto":
            people = any(a["kind"] == "image" and a.get("job_role", a.get("role")) in _HERO_ROLES for a in compiled.images)
            style_adapter, reason = select_adapter(prompt, mode, set(style_adapters or ()), people=people)
            effective["lora_auto_reason"] = reason
        # Scenario intent belongs to the Director/native H3 conditioning.
        # Never silently force an unverified 2D LoRA or a replacement adapter.
        enabled_adapters = set(style_adapters or ())
        if style_adapter == "auto" and style_adapters is None:
            pass
        elif style_adapter not in {"none", "style3d", "cartoon2d", "combat", "realism_people", *SWAP_LORAS.values()} or (style_adapter != "none" and style_adapter not in enabled_adapters):
            raise ValueError("Стилевая LoRA недоступна на этом GPU-сервере")
        effective["style_adapter"] = style_adapter
        # Every route (Studio form, MCP, Context IR, a studio-settings package in the prompt) ends here with the
        # final mode and LoRA, so the passport medium is checked once, on what will really render.
        heroes = [a["asset_id"] for a in assets if a.get("job_role", a.get("role")) in passport_medium.HERO_ROLES]
        wrong_medium = passport_medium.medium_problem(self.root / "franchises.sqlite3", heroes, mode, style_adapter)
        if wrong_medium:
            raise ValueError(wrong_medium)
        if effective.get("swap_mask") is not None:
            swap_mask = normalize_swap_mask(effective["swap_mask"])
            if swap_mask:
                if not replacement_job or len(ref_videos) != 1:
                    raise ValueError("Маска замены — только для замены героев по одному исходному видео")
                if style_adapter not in set(SWAP_LORAS.values()) or effective.get("backend") == "minimax_api":
                    raise ValueError("Маска замены работает только на своём GPU с Character Swap LoRA")
                if (effective.get("ab_turbo") or effective.get("guide_frames") or effective.get("seed_hunt")
                        or any(v.get("job_role", v.get("role")) == CONTINUATION_ROLE for v in compiled.videos)):
                    raise ValueError("Маска замены: без турбо, guide-кадров, подбора seed и «Продолжения»")
                owners = {pair["owner"].casefold() for video in ref_videos for pair in video.get("job_performers", ())}
                unknown = [p["name"] for p in swap_mask["performers"] if p["name"].casefold() not in owners]
                if unknown:
                    raise ValueError("Маска замены: нет пары «герой — исполнитель» для " + ", ".join(unknown))
            effective["swap_mask"] = swap_mask
            if swap_mask is None:
                effective.pop("swap_mask")
        if effective.get("reskin") is not None:
            reskin = normalize_reskin(effective["reskin"])
            if reskin:
                # A/B: the one source video becomes the starting latent; see app/reskin.py.
                if len(ref_videos) != 1 or compiled.audios:
                    raise ValueError("Рескин: ровно одно видео-исходник, без отдельных аудио")
                if effective.get("swap_mask") or effective.get("backend") == "minimax_api":
                    raise ValueError("Рескин работает только на своём GPU и без маски замены")
                if (effective.get("ab_turbo") or effective.get("guide_frames") or effective.get("seed_hunt")
                        or any(v.get("job_role", v.get("role")) == CONTINUATION_ROLE for v in compiled.videos)):
                    raise ValueError("Рескин: без турбо, guide-кадров, подбора seed и «Продолжения»")
                effective["reskin"] = reskin
            else:
                effective.pop("reskin")
        require_ab_request(effective, style_adapter)
        render_options = normalize_render_options(effective.get("render_options"))
        validate_render_combination(render_options, style_adapter, bool(effective.get("character_refinement")))
        turbo = effective.get("ab_turbo")
        if turbo is not None:
            if turbo not in AB_TURBO:
                raise ValueError("Turbo: доступны " + ", ".join(sorted(AB_TURBO)))
            if effective.get("character_refinement") or render_options:
                raise ValueError("Turbo A/B нельзя совмещать с уточнением героев или экспериментальным профилем")
            if effective.get("profile", "bf16") != "bf16":
                raise ValueError("Turbo A/B требует BF16")
            effective["cache_mode"] = "off"  # the A/B graph bypasses FirstBlockCache
        if render_options:
            effective["render_options"] = render_options
        else:
            effective.pop("render_options", None)
        if "speed" in effective and effective["speed"] != "quality":
            raise ValueError("Доступен только полный режим качества")
        cache_mode = effective.get("cache_mode", DEFAULTS["cache_mode"])
        custom_cache = normalize_cache_config(cache_mode, effective.get("cache_config"))
        if custom_cache is not None:
            effective["cache_config"] = custom_cache
        else:
            effective.pop("cache_config", None)
        effective["cache_mode"] = cache_mode
        sampling_profile = effective.get("sampling_profile", DEFAULTS["sampling_profile"])
        sampling_inputs(sampling_profile)
        if sampling_profile == "standard":
            effective.pop("sampling_profile", None)  # preserve the golden graph and saved-job fingerprint
        else:
            effective["sampling_profile"] = sampling_profile
        effective["mode"] = mode
        effective["emotional_direction"] = effective.get("emotional_direction", DEFAULTS["emotional_direction"]) is True
        if official_format:
            generation_prompt = apply_ir_animation_mode(compiled_prompt, mode)
            # Precompiled path: no Qwen re-planning and no numeric camera text. The chosen lens, focus, light,
            # colour and style presets stay: the GPU writes them once as plain sentences inside [Shot 1]
            # (app/director_config.director_config_sentences); they used to be dropped for every IR job.
            effective.update(prompt_format="h3_ir", emotional_direction=False, camera_state=None, camera_optics=None)
        elif clean_prompt:
            generation_prompt = source_swap_identity_contract(compiled_prompt, compiled.images, ref_videos)
            generation_prompt, _cards = harmonize_director_state(generation_prompt, ref_videos)
            # the agent writes camera, lens and light in its own words: no second, numeric copy on the GPU
            effective.update(emotional_direction=False, camera_state=None, camera_optics=None, director_config=None)
        else:
            generation_prompt = apply_visual_mode(compiled_prompt, mode)
            generation_prompt = source_swap_identity_contract(generation_prompt, compiled.images, ref_videos)
            # Video performance roles are independent of the visual style. A realistic
            # render must receive the same motion pairing as a replacement render.
            video_roles = {video.get("job_role", video.get("role")) for video in ref_videos}
            if "Исходное видео" in video_roles:
                generation_prompt = VIDEO_PERFORMANCE_DIRECTION + "\n\n" + generation_prompt
            elif "Движение" in video_roles and "Движение и камера" not in video_roles:
                generation_prompt = VIDEO_BODY_DIRECTION + "\n\n" + generation_prompt
            generation_prompt, camera_cards = harmonize_director_state(generation_prompt, ref_videos)
            focus_rule = motion_focus_direction(ref_videos, camera_from_cards=camera_cards)
            if focus_rule and "Движение и камера" not in video_roles:
                generation_prompt = generation_prompt + "\n\n" + focus_rule
            clean_rule = clean_frame_direction(ref_videos)
            if clean_rule and "Движение и камера" not in video_roles:
                generation_prompt = generation_prompt + "\n\n" + clean_rule
        effective["profile"] = effective.get("profile", "bf16")
        effective["seed"] = int(effective["seed"]) if effective.get("seed") not in (None, "") else secrets.randbelow(2**53)
        quality = effective.get("quality", DEFAULTS["quality"])
        effective["quality"] = quality
        if quality == "custom" and effective.get("megapixels") in (None, ""):
            effective["megapixels"] = default_megapixels(quality, None)
        effective["steps"] = int(effective.get("steps", DEFAULTS["steps"]))
        effective["ref_image_size"] = effective.get("ref_image_size", DEFAULTS["ref_image_size"])
        if writer_package and not clean_prompt:
            generation_prompt = ensure_audio_direction(generation_prompt)
        request = GenerationRequest(
            generation_prompt, int(effective.get("duration", 5)), effective.get("aspect_ratio", "9:16"),  # every STUDIO video is vertical
            quality, profile=effective["profile"], steps=effective["steps"], seed=effective["seed"],
            megapixels=effective.get("megapixels"), ref_image_size=effective["ref_image_size"],
        )
        effective["camera_optics"] = normalize_camera_optics(
            effective.get("camera_optics"), effective.get("camera_state"),
        )
        effective.update(duration=request.duration, aspect_ratio=request.aspect_ratio,
                         width=request.width, height=request.height, length=request.length)
        # Checked here, once the real frame count is known (it defaulted to 362 before).
        # MiniMaxH3AddGuide: negative frame_idx counts from the end (-1 = last frame).
        for guide in guide_frames:
            if continuation and resolve_guide_frame(guide[1], request.length) < CONTINUATION_FRAMES:
                raise ValueError(f"Guide-кадр {guide[1]} попадает на продолжение дубля (кадры 0–{CONTINUATION_FRAMES - 1})")
            resolve_guide_frame(guide[1], request.length)
        swap_recipe_gate(generation_prompt, compiled.images, ref_videos)
        audio_labels = (len(ref_videos) if effective.get("use_video_audio") else 0) + len(compiled.audios)
        token_preflight.preflight(generation_prompt, len(compiled.images), len(ref_videos), audio_labels)
        budget = token_preflight.budget_for(generation_prompt, request.width, request.height, request.duration,
                                            compiled.images, ref_videos, effective.get("ref_image_size", "max"))
        manifest = {
            "version": 1,
            "images": [{"asset_id": a["asset_id"], "sha256": a["sha256"], "display_number": a["number"], "role": a.get("job_role", a.get("role")), "owner": a.get("job_owner", "")} for a in compiled.images],
            "videos": [{"asset_id": a["asset_id"], "sha256": a["sha256"], "display_number": a["number"], "role": a.get("job_role", a.get("role")),
                        **({"owner": a["job_owner"]} if a.get("job_owner") else {}),
                        **({"performers": a["job_performers"], "keep_others": a["job_keep_others"]}
                           if len(a.get("job_performers", ())) > 1 or a.get("job_keep_others")
                           or any(p["performer"] for p in a.get("job_performers", ())) else {})}
                       for a in compiled.videos],
            "audios": [{"asset_id": a["asset_id"], "sha256": a["sha256"], "display_number": a["number"], "role": a.get("job_role", a.get("role")),
                        **({"owner": a["job_owner"]} if a.get("job_owner") else {})} for a in compiled.audios],
        }
        from .production_gates import check as production_gates_check  # shot timeline + reject streak (29481ae3)
        with self.connect() as gate_db:
            production_gates_check(self, gate_db, prompt=original_prompt, settings=effective, manifest=manifest)
        job_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if receipt_key:
                old = db.execute("SELECT request_hash,job_id FROM submission_receipts WHERE receipt_key=?", (receipt_key,)).fetchone()
                if old:
                    if old["request_hash"] != receipt_hash:
                        raise ValueError("ID отправки уже использован с другим запросом")
                    return dict(db.execute("SELECT * FROM jobs WHERE job_id=?", (old["job_id"],)).fetchone())
                db.execute("INSERT INTO submission_receipts VALUES (?,?,?)", (receipt_key, receipt_hash, job_id))
            db.execute("""INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,mapping_json,production_spec_json,budget_json)
                          VALUES (?,'queued_local',?,?,?,?,?,?,?)""",
                       (job_id, original_prompt, generation_prompt,
                        json.dumps(effective, sort_keys=True), json.dumps(manifest, sort_keys=True),
                        json.dumps(compiled.mapping, sort_keys=True),
                        json.dumps(production_spec, sort_keys=True, ensure_ascii=False) if production_spec is not None else None,
                        json.dumps(budget, sort_keys=True) if budget else None))
            db.execute("INSERT INTO events(job_id,state) VALUES (?,'queued_local')", (job_id,))
        return self.get_job(job_id)

    def add_comfyui_edit_job(self, source: dict, job_id: str, remote_job_id: str, edit: dict,
                             director_json: str | None) -> dict:
        """A graph edited in ComfyUI, already accepted by the GPU API: same prompt and references
        as its source job, `settings.comfyui_edit` records which edit and what changed."""
        settings = {**json.loads(source["settings_json"]), "comfyui_edit": edit}
        with self.connect() as db:
            db.execute("""INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,
                          mapping_json,production_spec_json,remote_job_id,director_json)
                          VALUES (?,'queued_gpu',?,?,?,?,?,?,?,?)""",
                       (job_id, source["original_prompt"], source["compiled_prompt"],
                        json.dumps(settings, sort_keys=True, ensure_ascii=False), source["manifest_json"],
                        source["mapping_json"], source.get("production_spec_json"), remote_job_id, director_json))
            db.execute("INSERT INTO events(job_id,state) VALUES (?,'queued_gpu')", (job_id,))
        return self.get_job(job_id)

    def add_seed_hunt_jobs(self, source: dict, hunt: list[dict]) -> list[dict]:
        """Seed hunt (app/h3graph/seed_hunt.py): copies of `source` for the Mac FIFO, each with its own
        seed and `settings.seed_hunt` ({role, hunt_id, index}); prompt and references stay the same."""
        base = json.loads(source["settings_json"])
        base.pop("comfyui_edit", None)
        width, height = int(base.get("width") or 0), int(base.get("height") or 0)
        if width % 64 or height % 64:
            raise ValueError(f"Подбор seed рендерит превью вдвое меньше: {width}×{height} не делится на 64. "
                             "Для подбора выбери 0,7 Мп (640×1152) или 0,86 Мп (704×1280)")
        if base.get("swap_mask"):
            raise ValueError("Подбор seed с маской замены не поддерживается: превью меньшего размера, маска — по кадру")
        if base.get("reskin"):
            raise ValueError("Подбор seed для рескина не поддерживается")
        ids = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in hunt:
                job_id = str(uuid.uuid4())
                settings = {**base, "seed": item["seed"], "seed_hunt": item["seed_hunt"]}
                db.execute("""INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,
                              mapping_json,production_spec_json) VALUES (?,'queued_local',?,?,?,?,?,?)""",
                           (job_id, source["original_prompt"], source["compiled_prompt"],
                            json.dumps(settings, sort_keys=True, ensure_ascii=False), source["manifest_json"],
                            source["mapping_json"], source.get("production_spec_json")))
                db.execute("INSERT INTO events(job_id,state) VALUES (?,'queued_local')", (job_id,))
                ids.append(job_id)
        return [self.get_job(job_id) for job_id in ids]

    def get_job(self, job_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def list_jobs(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs WHERE hidden=0 ORDER BY created_at DESC, rowid DESC")]

    def set_sampling_progress(self, job_id: str, value: int, maximum: int) -> None:
        if maximum <= 0 or value < 0 or value > maximum:
            return
        with self.connect() as db:
            db.execute("UPDATE jobs SET sampling_value=?, sampling_max=?, updated_at=CURRENT_TIMESTAMP WHERE job_id=? AND state='running'",
                       (value, maximum, job_id))

    def save_draft(self, payload: dict, expected_revision: int | None = None) -> int:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM draft WHERE id=1").fetchone()
            current = row[0] if row else 0
            if expected_revision is not None and expected_revision != current:
                raise DraftConflict(f"Черновик изменён: текущая версия {current}")
            revision = current + 1
            # 1.11: the whole canvas was replaced by {"prompt": "first"} and only freed SQLite pages kept the scenes.
            # Every save keeps the version it replaces; the last DRAFT_HISTORY stay.
            previous = db.execute("SELECT payload FROM draft WHERE id=1").fetchone()
            if previous and previous[0]:
                db.execute("INSERT OR REPLACE INTO draft_history(revision,payload,saved_at) VALUES (?,?,?)",
                           (current, previous[0], time.time()))
                db.execute("DELETE FROM draft_history WHERE revision NOT IN "
                           "(SELECT revision FROM draft_history ORDER BY revision DESC LIMIT ?)", (DRAFT_HISTORY,))
            db.execute("INSERT INTO draft(id,payload,revision) VALUES (1,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, revision=excluded.revision",
                       (json.dumps(payload, ensure_ascii=False), revision))
            return revision

    def draft_history(self) -> list[dict]:
        """Earlier canvas versions, newest first: revision, time, scene titles (the payload by draft_version)."""
        with self.connect() as db:
            rows = db.execute("SELECT revision,payload,saved_at FROM draft_history ORDER BY revision DESC").fetchall()
        out = []
        for revision, payload, saved_at in rows:
            try:
                scenes = [str(s.get("title") or "") for s in json.loads(payload).get("scenes") or []]
            except (ValueError, AttributeError):
                scenes = []
            out.append({"revision": revision, "saved_at": saved_at, "scenes": scenes, "bytes": len(payload)})
        return out

    def draft_version(self, revision: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM draft_history WHERE revision=?", (revision,)).fetchone()
        return json.loads(row[0]) if row else None

    def load_draft_snapshot(self) -> tuple[dict | None, int]:
        with self.connect() as db:
            row = db.execute("SELECT payload,revision FROM draft WHERE id=1").fetchone()
            return (json.loads(row[0]), row[1]) if row else (None, 0)

    def load_draft(self) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM draft WHERE id=1").fetchone()
            return json.loads(row[0]) if row else None

    def queue_paused(self) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT paused FROM queue_control WHERE id=1").fetchone()
        if row and row[0]:
            return True
        # QC gate (review_gate.py, 13207232): finished takes nobody reviewed hold every dispatch path.
        from . import review_gate
        return bool(review_gate.review_hold(self))

    def set_queue_paused(self, paused: bool) -> bool:
        with self.connect() as db:
            db.execute("INSERT INTO queue_control(id,paused) VALUES (1,?) "
                       "ON CONFLICT(id) DO UPDATE SET paused=excluded.paused", (int(paused),))
        return paused

    def cancel_queued(self, job_id: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE jobs SET state='cancelled', updated_at=CURRENT_TIMESTAMP "
                                "WHERE job_id=? AND state='queued_local'", (job_id,))
            if result.rowcount != 1:
                raise ValueError("Отменить можно только ожидающее локальное задание")
            db.execute("INSERT INTO events(job_id,state,detail) VALUES (?,'cancelled',?)",
                       (job_id, "Отменено до отправки на GPU"))
        return self.get_job(job_id)

    def hide_completed_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job or job["state"] != "completed":
            raise ValueError("Удалить из ленты можно только завершённый результат")
        source = Path(job["output_path"])
        if not source.is_file() or not source.resolve().is_relative_to(self.results_dir.resolve()):
            raise ValueError("MP4 не найден")
        trash = self.results_dir / "trash"
        trash.mkdir(exist_ok=True)
        destination = trash / source.name
        if destination.exists():
            raise ValueError("В корзине Studio уже есть файл с таким именем")
        os.replace(source, destination)
        with self.connect() as db:
            db.execute("UPDATE jobs SET hidden=1, output_path=?, updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                       (str(destination), job_id))
            db.execute("INSERT INTO events(job_id,state,detail) VALUES (?,?,?)",
                       (job_id, "completed", "Перенесено в корзину Studio"))

    def hide_failed_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        # Failed and cancelled jobs have no media: hiding only removes the card, the record stays.
        if not job or job["state"] not in {"failed", "cancelled"}:
            raise ValueError("Скрыть можно только задание с ошибкой или отменённое")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE jobs SET hidden=1, updated_at=CURRENT_TIMESTAMP WHERE job_id=? AND state IN ('failed','cancelled')",
                       (job_id,))
            db.execute("INSERT INTO events(job_id,state,detail) VALUES (?,?,?)",
                       (job_id, job["state"], "Карточка скрыта из ленты; запись сохранена"))

    def clear_visible_jobs(self) -> dict:
        """Hide the visible queue/history. Remote or uncertain jobs (on GPU, stopping,
        needs reconciliation) are never touched: they stay visible and are reported as kept."""
        moved: list[tuple[Path, Path]] = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            visible = [dict(row) for row in db.execute("SELECT job_id,state,output_path FROM jobs WHERE hidden=0")]
            safe_states = {"queued_local", "completed", "failed", "cancelled", "interrupted"}
            jobs = [job for job in visible if job["state"] in safe_states]
            kept = len(visible) - len(jobs)
            trash = self.results_dir / "trash"
            for job in jobs:
                if job["state"] != "completed":
                    continue
                source = Path(job["output_path"] or "")
                if not source.is_file() or not source.resolve().is_relative_to(self.results_dir.resolve()):
                    raise ValueError("MP4 одного из результатов не найден; список не изменён")
                if (trash / source.name).exists():
                    raise ValueError("В корзине Studio уже есть файл с таким именем; список не изменён")
            try:
                trash.mkdir(exist_ok=True)
                for job in jobs:
                    if job["state"] == "completed":
                        source = Path(job["output_path"])
                        destination = trash / source.name
                        os.replace(source, destination)
                        moved.append((source, destination))
                        db.execute("UPDATE jobs SET hidden=1,output_path=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                                   (str(destination), job["job_id"]))
                    elif job["state"] == "queued_local":
                        db.execute("UPDATE jobs SET state='cancelled',hidden=1,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                                   (job["job_id"],))
                    else:
                        db.execute("UPDATE jobs SET hidden=1,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                                   (job["job_id"],))
                    db.execute("INSERT INTO events(job_id,state,detail) VALUES (?,?,?)",
                               (job["job_id"], "cancelled" if job["state"] == "queued_local" else job["state"],
                                "Скрыто командой «Удалить всё»; запись сохранена"))
            except Exception:
                for source, destination in reversed(moved):
                    os.replace(destination, source)
                raise
        return {"hidden": len(jobs), "cancelled_local": sum(job["state"] == "queued_local" for job in jobs),
                "videos_in_trash": len(moved), "kept_active": kept}

    def oldest_pending(self) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE state='queued_local' ORDER BY created_at, rowid LIMIT 1").fetchone()
            return dict(row) if row else None

    def oldest_pending_cloud(self) -> dict | None:
        """Oldest MiniMax API job waiting on the Mac: it has its own remote queue, not the GPU FIFO."""
        with self.connect() as db:
            row = db.execute("""SELECT * FROM jobs WHERE state='queued_local'
                AND json_extract(settings_json, '$.backend')='minimax_api'
                ORDER BY created_at, rowid LIMIT 1""").fetchone()
            return dict(row) if row else None

    def active_job(self) -> dict | None:
        """The H3 job that owns the FIFO. needs_reconciliation is not one: it is checked in the
        background by client ID (QueueWorker.reconcile_once) and must not hold later jobs for 2 h."""
        with self.connect() as db:
            row = db.execute("""SELECT * FROM jobs WHERE state IN
                ('uploading','queued_gpu','running','stopping','downloading')
                ORDER BY created_at, rowid LIMIT 1""").fetchone()
            return dict(row) if row else None

    def active_remote_jobs(self) -> list[dict]:
        """All accepted GPU jobs, including ones behind an older unfinished job."""
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM jobs WHERE state IN
                ('queued_gpu','running','downloading') AND remote_job_id IS NOT NULL
                ORDER BY created_at, rowid""")]

    def reconciling_jobs(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM jobs WHERE state='needs_reconciliation' ORDER BY created_at, rowid")]

    def transition(self, job_id: str, state: str, *, remote_job_id: str | None = None,
                   output_path: str | None = None, error: str | None = None,
                   director_json: str | None = None,
                   expected_states: tuple[str, ...] | None = None) -> dict:
        allowed = {"queued_local", "uploading", "queued_gpu", "running", "stopping", "downloading",
                   "completed", "failed", "needs_reconciliation", "cancelled", "interrupted"}
        if state not in allowed:
            raise ValueError("Неизвестный статус")
        output_sha256 = None
        if state == "completed" and output_path and Path(output_path).is_file():
            with Path(output_path).open("rb") as stream:
                output_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not current:
                raise ValueError("Задание не найдено")
            if expected_states is not None and current[0] not in expected_states:
                raise StaleTransition(f"Задание уже перешло в {current[0]}")
            db.execute("""UPDATE jobs SET state=?, remote_job_id=COALESCE(?,remote_job_id),
                output_path=COALESCE(?,output_path), director_json=COALESCE(?,director_json),
                output_sha256=COALESCE(output_sha256,?), error=?, updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
                       (state, remote_job_id, output_path, director_json, output_sha256, error, job_id))
            db.execute("INSERT INTO events(job_id,state,detail) VALUES (?,?,?)", (job_id, state, error))
        return self.get_job(job_id)
