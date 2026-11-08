from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import math
from pathlib import Path
import re


# 4:3 for references shot in 4:3: the world swap renders in the reference's own format instead of cropping or
# extending it (user 06.11: «формат тогда тот же, что и референс»). Native stays 16:9 / 9:16 only.
# Any format fits H3's canvas (official: short side 768, area ≤ 768×1344, sides multiple of 32); Studio lists the
# usual ones so a reference renders in its own shape.
ASPECTS = {"16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4, "1:1": 1.0}
# Mac-side validators (franchise passport, staging, edit sheet, previs) take their pattern from here.
ASPECT_PATTERN = "^(" + "|".join(re.escape(a) for a in ASPECTS) + ")$"

_RESOLUTIONS = {
    ("16:9", "native"): (1344, 768),
    ("9:16", "native"): (768, 1344),
}
_PRESET_MP = {"compact": 0.5, "quick": 0.8, "balanced": 0.9, "max": 0.98}
_NATIVE_AREA = 1344 * 768

from .render_profiles import PROFILES as _RENDER_PROFILES
_PROFILES = frozenset(_RENDER_PROFILES)

# Version of the Mac Studio <-> GPU API job contract (form fields and their meaning).
# Bump it when a field is added/renamed or its semantics change; the GPU API reports it in
# /api/capabilities and refuses clients that are newer than itself.
API_CONTRACT = 5  # 5: allow_short_motion_reference
DIRECTOR_SCHEMA_PATH = Path(__file__).resolve().with_name("director_schema.json")


@lru_cache(maxsize=1)
def director_schema_sha256() -> str:
    """Both sides must render Director fields from the same app/director_schema.json."""
    return hashlib.sha256(DIRECTOR_SCHEMA_PATH.read_bytes()).hexdigest()


def gpu_contract_problem(capabilities: dict) -> str | None:
    """Russian refusal when the GPU API is older than this client or uses another schema."""
    remote = capabilities.get("api_contract")
    if type(remote) is not int or remote < API_CONTRACT:
        return (f"GPU API устарел: контракт {remote if type(remote) is int else 1}, "
                f"а Mac Studio требует {API_CONTRACT}. Обнови сервер: scripts/h3_new_server.sh")
    if capabilities.get("director_schema_sha256") != director_schema_sha256():
        return ("Схема Director на GPU отличается от Mac Studio (app/director_schema.json). "
                "Обнови сервер: scripts/h3_new_server.sh")
    return None

# The single source of job defaults. Values are what Studio sends today
# (preview/h3-director/model.mjs createProject + settingsSnapshot), so a request that
# omits a field renders exactly like a real Studio job. Mac store, worker and the GPU
# form read them from here.
DEFAULTS = {
    "quality": "custom",
    "megapixels": 0.8,  # user decision 2026-11-04: 0.8 MP default (9:16 672x1216), below 720p
    "mode": "replace",
    "steps": 24,  # full-quality default; turbo LoRAs set their own step count
    "cache_mode": "safe",
    "sampling_profile": "standard",
    "ref_image_size": "match",
    # False since 05.11: the form already sends false and the GPU Qwen director is off (DECISIONS 22.10);
    # a client that omits the field gets the precompiled route, never a silent Qwen rewrite.
    "emotional_direction": False,
}
# A job saved before emotional_direction existed (or a client that omits the field) keeps the
# precompiled route: the field is part of the client_job_id fingerprint of accepted jobs.
ABSENT_EMOTIONAL_DIRECTION = False


def default_megapixels(quality: str, megapixels: float | None) -> float | None:
    """Studio's custom size when the client named "custom" without a size."""
    if quality == "custom" and megapixels is None:
        return DEFAULTS["megapixels"]
    return megapixels


def resolution(aspect_ratio: str, quality: str, megapixels: float | None = None) -> tuple[int, int]:
    if aspect_ratio not in ASPECTS:
        raise ValueError("Поддерживаются только " + ", ".join(ASPECTS))
    if quality == "native":
        if megapixels is not None:
            raise ValueError("Для native нельзя указывать megapixels")
        return _RESOLUTIONS[(aspect_ratio, quality)]
    if quality == "custom":
        if megapixels is None or not math.isfinite(megapixels) or not 0.2 <= megapixels <= 0.98:
            raise ValueError("Custom megapixels должен быть от 0.2 до 0.98")
        target_mp = megapixels
    elif quality in _PRESET_MP:
        if megapixels is not None:
            raise ValueError("Для пресета нельзя указывать megapixels")
        target_mp = _PRESET_MP[quality]
    else:
        raise ValueError("Доступны compact, quick, balanced, max, custom и legacy native")
    ratio = ASPECTS[aspect_ratio]
    target_pixels = target_mp * 1024 * 1024
    width = round(math.sqrt(target_pixels * ratio) / 32) * 32
    height = round(math.sqrt(target_pixels / ratio) / 32) * 32
    if width * height > _NATIVE_AREA:
        raise ValueError("Выбранное разрешение превышает нативный лимит H3")
    return width, height


def frame_count(seconds: int) -> int:
    # Official H3 README: output duration 4–15 s. On the 17k+5 grid at 24 fps
    # 4 s snaps to 107 frames (≈4.46 s); 5–15 s keep their previous frame counts.
    if not 4 <= seconds <= 15:
        raise ValueError("Длительность должна быть от 4 до 15 секунд")
    raw = max(5, round(seconds * 24))
    return raw + (5 - raw % 17) % 17


def reference_prompt(prompt: str, video_audio_indices: tuple[int, ...] = ()) -> str:
    result = prompt
    video_audio = set(video_audio_indices)

    # Audio embedded in a reference video is numbered before standalone audio.
    # Replace the longer alias first so @video1audio is not consumed as @video1.
    for audio_index, video_index in enumerate(sorted(video_audio), start=1):
        result = re.sub(
            rf"(?<![\w@])@video{video_index}audio\b(?!\.[A-Za-z])",
            f"<Audio {audio_index}>",
            result,
            flags=re.IGNORECASE,
        )
    for index in range(1, 4):
        result = re.sub(
            rf"(?<![\w@])@video{index}\b(?!\.[A-Za-z])",
            f"<Video {index}>",
            result,
            flags=re.IGNORECASE,
        )
    audio_offset = len(video_audio)
    for index in range(1, 4):
        result = re.sub(
            rf"(?<![\w@])@audio{index}\b(?!\.[A-Za-z])",
            f"<Audio {audio_offset + index}>",
            result,
            flags=re.IGNORECASE,
        )
    for index in range(1, 10):
        result = re.sub(
            rf"(?<![\w@])@ref{index}\b(?!\.[A-Za-z])",
            f"<Picture {index}>",
            result,
            flags=re.IGNORECASE,
        )
    return result


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    duration: int
    aspect_ratio: str
    quality: str
    profile: str = "bf16"
    steps: int = DEFAULTS["steps"]
    seed: int | None = None
    megapixels: float | None = None
    ref_image_size: str = DEFAULTS["ref_image_size"]
    width: int = field(init=False)
    height: int = field(init=False)
    length: int = field(init=False)

    def __post_init__(self) -> None:
        prompt = self.prompt.strip()
        if not prompt:
            raise ValueError("Промпт не может быть пустым")
        if self.profile not in _PROFILES:
            raise ValueError("Неизвестный профиль H3: " + str(self.profile))
        # 4 steps for few-step turbo LoRAs, up to 50 for quality; Studio defaults stay at full quality.
        if not 4 <= self.steps <= 50:
            raise ValueError("Число шагов должно быть от 4 до 50")
        if self.seed is not None and not 0 <= self.seed < 2**64:
            raise ValueError("Seed должен быть от 0 до 2^64-1")
        if self.ref_image_size not in {"match", "max"}:
            raise ValueError("Reference detail должен быть match или max")
        width, height = resolution(self.aspect_ratio, self.quality, self.megapixels)
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "length", frame_count(self.duration))
