from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import os
import subprocess
from pathlib import Path
import uuid

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .camera_guide_vendor.camera import parse_state as parse_camera_state
from .comfy_client import ComfyClient
from .contract import (ASPECTS, 
    ABSENT_EMOTIONAL_DIRECTION, API_CONTRACT, DEFAULTS, GenerationRequest, default_megapixels,
    director_schema_sha256, reference_prompt,
)
from .director import (
    DIRECTOR_MISSING, DIRECTOR_ROLE_MAP, LocalQwenDirector, director_installed, _image_jpeg, _video_sample_jpegs,
    emotional_performance_contract,
    compile_director_plan, missing_model_files, preserve_explicit_camera_state,
    required_nodes_for_plan,
)
from .cache_policy import normalize_cache_config, cache_from_graph, custom_available
from .motion_reference_policy import apply_cast_and_clean_scene_contract, normalize_motion_retention
from .graph import SPEED_MODES, build_prompt, camera_guide_video_needed, effective_cache_mode
from .render_profiles import (normalize_render_options, render_capability, render_problem,
                              validate_render_combination)
from .ab_variant import (AB_CACHE_MODES, AB_COLOR_MATCH, AB_LORAS, AB_RESOLUTIONS, AB_SCHEDULERS,
                         AB_ANCHORS, AB_HIRES, AB_SAMPLERS, AB_SHIFT_RANGE, AB_SPARSE_MODES, AB_TURBO, AB_VAES, X2_MIN_HOST_RAM_GB, host_ram_gb, ABVariant, apply_ab_variant, graph_fingerprint,
                         parse_ab_variant)
from .upscale_graph import seedvr2_models_available
from .h3graph import seed_hunt
from .camera_optics import normalize_camera_optics
from .camera_words import vocabulary as camera_words_vocabulary
from .color_grade import normalize_color_grade, normalize_film_look
from .color_match import NODE as COLOR_MATCH_NODE, lab_stats_from_image, normalize_color_match
from .director_config import (director_config_sentences, place_in_first_shot,
                              normalize_director_config, director_config_direction)
from .scenarist_profile import capability as scenarist_capability
from .animal_realism import animal_realism_direction
from .h3_ir import Reference, build_skeleton, is_blocking, is_ir_prompt, validate_ir
from .job_registry import JobRegistry, recover_accepted
from .probe_graph import attach_to_job as attach_research_probes
from .qwen_lifecycle import async_render_submission
from .scenarist import make_router as make_scenarist_router
from .context_ir.router import make_router as make_context_ir_router, capability as context_ir_capability
from .scene_planner import make_router as make_scene_planner_router
from .scene_edits import make_router as make_scene_edits_router
from .reference_manifest import validate_dense_prompt
from .video_reference import (motion_reference_requires_full_duration, prepare_video_reference,
                              reference_video_short_edge)
from .audio_reference import prepare_audio_reference
from .voice_contract import normalize_voice_cast
from .character_refinement import normalize_character_refinement, build_character_refinement, validate_refinement_video
from .swap_mask import SWAP_MASK_NODES, apply_swap_mask, normalize_swap_mask
from .reskin import RESKIN_NODES, apply_reskin, normalize_reskin
from .h3graph.nodes import Node
from .acting import router as acting_router
from .upscale import make_router as make_upscale_router
from .camera_effects import EFFECTS as CAMERA_EFFECTS, embedding_name, installed_effects, normalize_camera_effect
from .continuation import NODES as CONTINUATION_NODES, prepare_continuation_video, split_continuation
from .audio_jobs import make_router as make_audio_jobs_router
from .acting_core import gpu_admission_dependency


ROOT = Path(__file__).resolve().parent
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 500 * 1024 * 1024
MAX_AUDIO_BYTES = 100 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
ALLOWED_AUDIO_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/x-m4a",
    "audio/x-wav",
}

app = FastAPI(title="MiniMax H3 Studio API", version="1.0.0")
comfy = ComfyClient()


_CAPABILITY_CACHE: dict[str, object] = {"at": 0.0, "value": None}


def capabilities_from_nodes(node_info: dict, embeddings: list[str] | None = None) -> dict:
    from .sampling import sampling_available
    enabled = {part.strip() for part in os.environ.get("H3_ENABLED_STYLE_ADAPTERS", "").split(",") if part.strip()}
    adapters = sorted(name for name in ("style3d", "cartoon2d", "combat", "realism_people", "character_swap",
                                        "character_swap_v15")
                      if name in enabled and not missing_model_files(name, node_info))
    return {
        "schema": 1, "ref2va": "MiniMaxH3ReferenceToVideo" in node_info,
        # Versioned Mac <-> GPU contract: Mac Studio refuses a GPU API that is older than itself
        # or renders Director fields from a different app/director_schema.json.
        "api_contract": API_CONTRACT, "director_schema_sha256": director_schema_sha256(),
        # Movement comes only from a native Ref2VA video reference; structural ControlNet was removed.
        "style_adapters": adapters, "motion_control": ["none"],
        "render_profiles_v1": render_capability(node_info),
        # The nodes app/graph.py builds (node 330): H3CameraGuide, or H3OperatorCamera with optics.
        "camera_guide": {"available": {"H3CameraGuide", "H3OperatorCamera"} <= set(node_info),
                         "state_versions": [1, 2, 3], "max_keys": 12},
        "camera_optics_v1": "H3OperatorCamera" in node_info,
        "scene_guides": "MiniMaxH3AddGuide" in node_info,
        # Official camera words accepted by camera_phrase (app/camera_words.py); additive key.
        "camera_words_v1": camera_words_vocabulary(),
        # «Продолжение»: the last 22 frames + sound of a previous clip anchored at frame 0 (app/continuation.py).
        "continuation_v1": CONTINUATION_NODES <= set(node_info),
        # Official prompt embeddings (H3_CAMERA_EFFECTS=1 installs them): job setting camera_effect.
        "camera_effects_v1": {"available": installed_effects(embeddings), "known": list(CAMERA_EFFECTS)},
        "speed_v1": {"modes": list(SPEED_MODES), "default": "quality"},
        "cache_modes": (["off", "safe", "fast"] + (["custom"] if custom_available(node_info) else [])) if "ApplyMiniMaxH3FirstBlockCache" in node_info else ["off"],
        "sampling_profiles": ["standard"] + [name for name in ("motion_ab", "workflow_euler", "ref_beta") if sampling_available(node_info, name)],
        "aspect_ratios": list(ASPECTS),
        "qualities": ["compact", "quick", "balanced", "max", "custom"],
        "megapixels": [0.2, 0.98], "duration_seconds": [4, 15], "steps": [4, 50],
        "ref_image_size": ["match", "max"],
        "limits": {"images": 9, "videos": 3, "audios": 3, "total": 12},
        "roles": sorted(DIRECTOR_ROLE_MAP), "director": "qwen-local",
        # Read only by Mac builds before api_contract 2; the contract version replaces it.
        "director_config_v1": True,
        "color_grade_v1": "H3ColorGrade" in node_info,
        "color_match_v1": COLOR_MATCH_NODE in node_info,
        "film_look_v1": "H3FilmLook" in node_info,
        "character_refinement_v1": {"H3CharacterTrack", "H3CharacterCrop", "H3CharacterComposite"} <= set(node_info),
        "swap_mask_v1": SWAP_MASK_NODES <= set(node_info),
        "reskin_v1": RESKIN_NODES <= set(node_info),
        # Planner model behind the Director and /api/scenarist (H3_SCENARIST_PROFILE, additive key).
        "scenarist_v1": scenarist_capability(),
        "context_ir_v1": context_ir_capability(),
        # A/B harness only (scripts/h3_ab.py); Studio never sends ab_* fields.
        "ab_v1": {
            "schedulers": list(AB_SCHEDULERS), "samplers": list(AB_SAMPLERS),
            "loras": sorted(name for name in AB_LORAS
                            if not ABVariant("caps", lora=name, lora_strength=1.0).missing_files(node_info)),
            "vae": [name for name in AB_VAES if "MiniMaxH3VAEDecodeFast" in node_info
                    and not ABVariant("caps", vae=name).missing_files(node_info)
                    and host_ram_gb() >= X2_MIN_HOST_RAM_GB],
            "sparse_attention": list(AB_SPARSE_MODES) if "BlockSparseAttention" in node_info else ["off"],
            "plan_replay": True, "vram_peak": True,
            # Two-pass render (app/h3graph/hires.py); "learned" downloads its checkpoint on first use.
            "hires": list(AB_HIRES) if {"SplitSigmasDenoise", "MiniMaxH3LatentUpscaleCombined"} <= set(node_info) else [],
            "turbo": sorted(name for name in AB_TURBO if not ABVariant("caps", turbo=name).missing_files(node_info)),
            # Seed hunt previews + finish (app/h3graph/seed_hunt.py): count range when the nodes exist.
            "seed_hunt": list(seed_hunt.COUNT_RANGE) if (seed_hunt.required_nodes({"role": "preview"})
                                                          | seed_hunt.FINISH_NODES) <= set(node_info) else [],
            # ab_shift: MiniMaxH3SigmaShift video shift range (model default 12).
            "shift": list(AB_SHIFT_RANGE) if "MiniMaxH3SigmaShift" in node_info else [],
            # ab_anchor for «Продолжение»: keyframe5 needs only core; inpaint needs h3-continuation-anchor.
            "anchor": [name for name, (mode, _) in AB_ANCHORS.items() if mode == "keyframe" or "H3ContinuationInpaint" in node_info],
            "resolutions": sorted(AB_RESOLUTIONS), "cache": list(AB_CACHE_MODES), "camera_words": True,
            "color_match": [name for name in AB_COLOR_MATCH if name != "colormatch_v2" or "ColorMatchV2" in node_info],
            # Upscale pass: /api/upscale/jobs ab_seedvr2_model (weights only with H3_SEEDVR2_7B=1).
            "seedvr2_models": seedvr2_models_available(),
        },
    }


@app.get("/api/capabilities")
async def api_capabilities() -> dict:
    import time
    now = time.monotonic()
    if _CAPABILITY_CACHE["value"] is not None and now - float(_CAPABILITY_CACHE["at"]) < 60:
        return _CAPABILITY_CACHE["value"]  # type: ignore[return-value]
    try:
        node_info = await comfy.object_info()
    except Exception as exc:
        raise HTTPException(503, f"GPU-движок недоступен: {exc}") from exc
    try:
        embeddings = await comfy.embeddings()
    except Exception:
        embeddings = []
    value = capabilities_from_nodes(node_info, embeddings)
    _CAPABILITY_CACHE.update(at=now, value=value)
    return value

async def _build_director_references(manifest: dict, groups: dict[str, list[tuple[str, bytes, str]]]) -> tuple[list[dict], dict[str, str]]:
    kinds = {"images": "image", "videos": "video", "audios": "audio"}
    records: list[dict] = []
    asset_kinds: dict[str, str] = {}
    for group, kind in kinds.items():
        for index, (entry, (filename, data, _mime)) in enumerate(zip(manifest[group], groups[group]), 1):
            asset_id = entry["asset_id"]
            asset_kinds[asset_id] = kind
            role = DIRECTOR_ROLE_MAP.get(entry.get("role"))
            frames: list[bytes] = []
            # A preview Qwen cannot decode (iPhone HEVC/HDR, broken index, exotic codec) must not
            # fail the job: the Director plans that asset without pictures and leaves its
            # visual_evidence empty. H3 itself still receives the original file.
            try:
                if kind == "image":
                    frames = [await asyncio.to_thread(_image_jpeg, data, filename)]
                elif kind == "video":
                    frames = await asyncio.to_thread(_video_sample_jpegs, data, filename)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                logging.getLogger(__name__).warning(
                    "Director preview frames unavailable for %s (%s): %s", Path(filename).name, kind, exc)
                frames = []
            records.append({"asset_id": asset_id, "kind": kind,
                            "tag": {"image": "Picture", "video": "Video", "audio": "Audio"}[kind] + f" {index}",
                            "role_override": role, "owner": entry.get("owner", ""), "frames": frames,
                            "filename": Path(filename).name})
    return records, asset_kinds


_REGISTRIES: dict[Path, JobRegistry] = {}

# Studio style LoRA -> the Director style it renders with (app/graph.py triggers).
_STYLE_ADAPTER_STYLES = {"style3d": "animation_3d", "cartoon2d": "animation_2d", "combat": "realism",
                         "realism_people": "realism", "character_swap": "realism", "character_swap_v15": "realism"}
_STYLE_NAMES_RU = {"realism": "реализм", "animation_3d": "3D-анимация", "animation_2d": "2D-анимация",
                   "illustration": "иллюстрация"}


def validated_client_camera(state: object, frame_count: int) -> dict:
    """Validate a Studio camera trajectory before compiling the GPU graph."""
    if not isinstance(state, dict):
        raise ValueError("camera_state должен быть объектом")
    try:
        parsed = parse_camera_state(state)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Некорректная траектория камеры: {exc}") from exc
    if len(parsed["keys"]) > 12:
        raise ValueError("Траектория камеры: не больше 12 ключевых кадров")
    if parsed["keys"][-1]["t"] > (frame_count - 1) / 24 + 1e-6:
        raise ValueError("Ключ камеры находится за концом ролика")
    return state


def reference_video_role(manifest: dict | None, compiled: dict, index: int) -> str | None:
    """Role of a reference video: an explicit Studio role wins, «Авто» defers to the role Qwen assigned."""
    videos = (manifest or {}).get("videos") or []
    entry = videos[index] if index < len(videos) else {}
    studio_role = (entry.get("role") or "").strip()
    if studio_role and not studio_role.startswith("Авто"):
        return studio_role
    return (compiled.get("roles") or {}).get(entry.get("asset_id"))


def _registry() -> JobRegistry:
    path = Path(os.environ.get("H3_JOB_REGISTRY_PATH", str(Path.home() / ".local/state/h3-studio/job-registry.sqlite3")))
    registry = _REGISTRIES.get(path)
    if registry is None:
        registry = _REGISTRIES[path] = JobRegistry(path)
    return registry


def _verify_manifest(manifest_json: str, groups: dict[str, list[tuple[str, bytes, str]]]) -> dict:
    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Некорректный JSON манифеста") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Неподдерживаемый манифест задания")
    seen_ids: set[str] = set()
    for kind, items in groups.items():
        entries = manifest.get(kind)
        if not isinstance(entries, list) or len(entries) != len(items):
            raise ValueError(f"Манифест {kind} не соответствует числу файлов")
        for number, (entry, (_, data, _)) in enumerate(zip(entries, items), 1):
            if not isinstance(entry, dict) or not isinstance(entry.get("asset_id"), str) or not entry["asset_id"]:
                raise ValueError(f"Некорректный asset_id в {kind}")
            if entry["asset_id"] in seen_ids:
                raise ValueError(f"Повторный asset_id: {entry['asset_id']}")
            seen_ids.add(entry["asset_id"])
            valid_numbers = {number, number + len(manifest.get("videos", []))} if kind == "audios" else {number}
            if "display_number" in entry and entry["display_number"] not in valid_numbers:
                raise ValueError(f"Номер файла {entry['asset_id']} не совпадает с порядком {kind}")
            if not isinstance(entry.get("role", ""), str) or not isinstance(entry.get("owner", ""), str):
                raise ValueError(f"Некорректная роль или владелец файла {entry['asset_id']}")
            if entry.get("sha256") != hashlib.sha256(data).hexdigest():
                raise ValueError(f"SHA-256 файла {entry['asset_id']} не совпадает с манифестом")
    return manifest


def _validate_official_manifest_prompt(prompt: str, manifest: dict, use_video_audio: bool) -> None:
    """Recheck Studio's subject/source bindings on the GPU before accepting work."""
    refs = [Reference("image", n, entry.get("role", ""), entry.get("owner", ""))
            for n, entry in enumerate(manifest["images"], 1)]
    refs += [Reference("video", n, entry.get("role", ""), entry.get("owner", ""))
             for n, entry in enumerate(manifest["videos"], 1)]
    offset = len(manifest["videos"]) if use_video_audio else 0
    refs += [Reference("audio", offset + n, entry.get("role", ""), entry.get("owner", ""))
             for n, entry in enumerate(manifest["audios"], 1)]
    skeleton = build_skeleton(refs, use_video_audio=use_video_audio)
    skeleton.labels.update(f"<Picture {n}>" for n in range(1, len(manifest["images"]) + 1))
    skeleton.labels.update(f"<Video {n}>" for n in range(1, len(manifest["videos"]) + 1))
    skeleton.labels.update(f"<Audio {n}>" for n in range(1, offset + len(manifest["audios"]) + 1))
    blocking = [p for p in validate_ir(prompt, skeleton, []) if is_blocking(p)]
    if blocking:
        raise ValueError("Промпт H3 не совпадает с файлами GPU: " + "; ".join(blocking))


async def _read_reference(
    upload: UploadFile | None,
    *,
    allowed_types: set[str],
    max_bytes: int,
    label: str,
) -> tuple[str, bytes, str] | None:
    if upload is None or not upload.filename:
        return None
    content_type = upload.content_type or "application/octet-stream"
    if content_type not in allowed_types:
        raise HTTPException(400, f"Неподдерживаемый формат: {label}")
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"Файл слишком большой: {label}")
    if not data:
        raise HTTPException(400, f"Пустой файл: {label}")
    return upload.filename, data, content_type


async def _read_many(
    uploads: list[UploadFile],
    *,
    allowed_types: set[str],
    max_bytes: int,
    label: str,
) -> list[tuple[str, bytes, str]]:
    items = [
        await _read_reference(
            upload,
            allowed_types=allowed_types,
            max_bytes=max_bytes,
            label=label,
        )
        for upload in uploads
    ]
    return [item for item in items if item is not None]


@app.get("/api/live")
async def live() -> dict:
    """Liveness of this process only (the watchdog's probe); /api/health also proxies ComfyUI."""
    return {"status": "ok"}


@app.get("/api/health")
async def health() -> dict:
    try:
        await comfy.health()
        return {"status": "ok", "engine": "ready"}
    except Exception as exc:
        raise HTTPException(503, f"GPU-движок недоступен: {exc}") from exc


@app.post("/api/jobs", status_code=202, dependencies=[Depends(gpu_admission_dependency)])
async def create_job(
    prompt: str = Form(...),
    duration: int = Form(5),
    aspect_ratio: str = Form("16:9"),
    quality: str = Form(DEFAULTS["quality"]),
    megapixels: str = Form(""),
    ref_image_size: str = Form(DEFAULTS["ref_image_size"]),
    profile: str = Form("bf16"),
    steps: int = Form(DEFAULTS["steps"]),
    seed: str = Form(""),
    client_job_id: str = Form(""),
    # Mac Studio sends its app.contract.API_CONTRACT; older clients omit it (contract 1).
    contract_version: int = Form(1),
    manifest_json: str = Form(""),
    use_video_audio: bool = Form(False),
    # Contract 5: a «Движение» video shorter than the clip is accepted (H3 sees it as it is).
    allow_short_motion_reference: bool = Form(False),
    # Legacy field: older Mac builds send "none". Structural control (pose/canny) was removed.
    control_type: str = Form("none"),
    mode: str = Form(DEFAULTS["mode"]),
    camera_state_json: str = Form(""),
    camera_optics_json: str = Form(""),
    director_config_json: str = Form(""),
    color_grade_json: str = Form(""),
    color_match_json: str = Form(""),
    film_look_json: str = Form(""),
    camera_effect: str = Form(""),
    speed: str = Form(""),
    character_refinement_json: str = Form(""),
    swap_mask_json: str = Form(""),
    reskin_json: str = Form(""),
    guide_frames_json: str = Form("[]"),
    style_adapter: str = Form("none"),
    cache_mode: str = Form(DEFAULTS["cache_mode"]),
    render_options_json: str = Form(""),
    sampling_profile: str = Form(DEFAULTS["sampling_profile"]),
    cache_config_json: str = Form(""),
    precompiled_prompt: bool = Form(False),
    emotional_direction: bool = Form(ABSENT_EMOTIONAL_DIRECTION),
    # "none": the Studio agent's prompt is final; no cast/scene, director-config or animal-realism blocks.
    prompt_blocks: str = Form("studio"),
    # A/B harness only (app/ab_variant.py, scripts/h3_ab.py). Absent = the normal Studio render.
    ab_variant: str = Form(""),
    ab_scheduler: str = Form(""),
    ab_lora: str = Form(""),
    ab_lora_strength: str = Form(""),
    ab_sparse_attention: str = Form(""),
    ab_director_plan_json: str = Form(""),
    ab_resolution: str = Form(""),
    ab_cache: str = Form(""),
    ab_camera_words: str = Form(""),
    ab_color_match: str = Form(""),
    ab_vae: str = Form(""),
    ab_sampler: str = Form(""),
    ab_steps: str = Form(""),
    ab_legacy_motion: str = Form(""),
    ab_hires: str = Form(""),
    ab_hires_tail: str = Form(""),
    ab_turbo: str = Form(""),
    ab_shift: str = Form(""),
    ab_anchor: str = Form(""),
    # Seed hunt (app/h3graph/seed_hunt.py): {"role": "preview"|"finish", "hunt_id", "index"}.
    seed_hunt_json: str = Form(""),
    images: list[UploadFile] | None = File(None),
    videos: list[UploadFile] | None = File(None),
    audios: list[UploadFile] | None = File(None),
    ref1: UploadFile | None = File(None),
    ref2: UploadFile | None = File(None),
    ref3: UploadFile | None = File(None),
) -> dict:
    if contract_version > API_CONTRACT:
        raise HTTPException(422, f"Mac Studio новее GPU API: контракт клиента {contract_version}, "
                                 f"сервера {API_CONTRACT}. Обнови сервер: scripts/h3_new_server.sh")
    try:
        from .sampling import sampling_inputs, sampling_available
        cache_config = normalize_cache_config(cache_mode, json.loads(cache_config_json) if cache_config_json.strip() else None)
        sampling_inputs(sampling_profile)
        render_options = normalize_render_options(json.loads(render_options_json) if render_options_json.strip() else None)
        parsed_seed = int(seed) if seed.strip() else None
        request = GenerationRequest(
            prompt,
            duration,
            aspect_ratio,
            quality,
            profile=profile,
            steps=steps,
            seed=parsed_seed,
            megapixels=default_megapixels(quality, float(megapixels) if megapixels.strip() else None),
            ref_image_size=ref_image_size,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if cache_mode == "custom":
        try:
            cache_info = await comfy.object_info()
        except Exception as exc:
            raise HTTPException(503, "Не удалось проверить CUSTOM кэш на GPU") from exc
        if not custom_available(cache_info):
            raise HTTPException(422, "Узел GPU не поддерживает ручной кэш с temporal_guard")
    if sampling_profile != "standard":
        try:
            sampling_node_info = await comfy.object_info()
        except Exception as exc:
            raise HTTPException(503, f"Не удалось проверить сэмплинг ComfyUI: {exc}") from exc
        if not sampling_available(sampling_node_info, sampling_profile):
            raise HTTPException(422, "ComfyUI не поддерживает выбранный сэмплер и scheduler")
    if request.profile != "bf16" or render_options:
        if character_refinement_json.strip():
            raise HTTPException(422, "Экспериментальный профиль нельзя применять к уточнению героев")
        try:
            experiment_info = await comfy.object_info()
            problem = render_problem(request.profile, render_options, experiment_info)
        except Exception as exc:
            raise HTTPException(503, f"Не удалось проверить экспериментальные веса: {exc}") from exc
        if problem:
            raise HTTPException(422, problem)
    try:
        ab, ab_plan = parse_ab_variant(ab_variant, ab_scheduler, ab_lora, ab_lora_strength,
                                       ab_sparse_attention, ab_director_plan_json, resolution=ab_resolution,
                                       cache=ab_cache, camera_words=ab_camera_words, color_match=ab_color_match,
                                       vae=ab_vae, sampler=ab_sampler,
                                       steps=ab_steps, legacy_motion=ab_legacy_motion,
                                       hires=ab_hires, hires_tail=ab_hires_tail, turbo=ab_turbo,
                                       shift=ab_shift, anchor=ab_anchor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if ab_plan is not None and precompiled_prompt:
        raise HTTPException(422, "ab_director_plan_json повторяет план Qwen; с precompiled_prompt он не нужен")
    if cache_mode == "custom" and character_refinement_json.strip():
        raise HTTPException(422, "Уточнение героев не поддерживает CUSTOM кэш; настройка не будет проигнорирована")
    if ab is not None and character_refinement_json.strip():
        raise HTTPException(422, "A/B-варианты не применяются к уточнению героев")
    try:
        hunt = seed_hunt.parse(seed_hunt_json)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if hunt is not None:
        if character_refinement_json.strip() or (ab is not None and (ab.hires or ab.turbo)):
            raise HTTPException(422, "Подбор seed сам задаёт два прохода: без уточнения героев, ab_hires и ab_turbo")
        try:
            hunt_nodes = seed_hunt.required_nodes(hunt) - set(await comfy.object_info())
        except Exception as exc:
            raise HTTPException(503, f"Не удалось проверить ноды ComfyUI: {exc}") from exc
        if hunt_nodes:
            raise HTTPException(422, "Подбор seed: на текущем ComfyUI нет узлов: " + ", ".join(sorted(hunt_nodes))
                                + " (пакет ComfyUI-MiniMaxH3-LatentUpscaler)")
    if ab is not None and ab.required_nodes():
        try:
            ab_node_info = await comfy.object_info()
        except Exception as exc:
            raise HTTPException(503, f"Не удалось проверить ноды и веса ComfyUI: {exc}") from exc
        missing_nodes = sorted(ab.required_nodes() - set(ab_node_info))
        if missing_nodes:
            raise HTTPException(422, "A/B: на текущем ComfyUI нет узлов: " + ", ".join(missing_nodes))
        missing_models = missing_model_files(ab.lora, ab_node_info) if ab.lora else []
        missing_models += [name for name in ab.missing_files(ab_node_info) if name not in missing_models]
        if missing_models:
            raise HTTPException(422, "A/B: в ComfyUI не найдены требуемые файлы моделей: "
                                + ", ".join(missing_models) + " (models/loras, models/vae)")

    image_uploads = list(images or []) + [item for item in (ref1, ref2, ref3) if item is not None]
    video_uploads = list(videos or [])
    audio_uploads = list(audios or [])
    image_items = await _read_many(
        image_uploads,
        allowed_types=ALLOWED_IMAGE_TYPES,
        max_bytes=MAX_IMAGE_BYTES,
        label="PNG/JPEG/WebP до 25 МБ",
    )
    video_items = await _read_many(
        video_uploads,
        allowed_types=ALLOWED_VIDEO_TYPES,
        max_bytes=MAX_VIDEO_BYTES,
        label="MP4/MOV/WebM до 500 МБ",
    )
    audio_items = await _read_many(
        audio_uploads,
        allowed_types=ALLOWED_AUDIO_TYPES,
        max_bytes=MAX_AUDIO_BYTES,
        label="WAV/FLAC/MP3/M4A/OGG до 100 МБ",
    )
    if control_type not in {"auto", "none"}:
        raise HTTPException(422, "Контроль позы/контуров (ControlNet) удалён из Studio. "
                                 "Для движения добавь видео-референс с ролью «Движение и камера»")

    if len(image_items) > 9 or len(video_items) > 3 or len(audio_items) > 3 or len(image_items) + len(video_items) + len(audio_items) > 12:
        raise HTTPException(422, "Превышен лимит референсов H3")
    try:
        camera_state = json.loads(camera_state_json) if camera_state_json.strip() else None
        if camera_state is not None:
            camera_state = validated_client_camera(camera_state, request.length)
        camera_optics = json.loads(camera_optics_json) if camera_optics_json.strip() else None
        director_config = normalize_director_config(
            json.loads(director_config_json) if director_config_json.strip() else None
        )
        color_grade = normalize_color_grade(json.loads(color_grade_json) if color_grade_json.strip() else None)
        film_look = normalize_film_look(json.loads(film_look_json) if film_look_json.strip() else None)
        color_match = normalize_color_match(json.loads(color_match_json) if color_match_json.strip() else None,
                                            len(image_items))
        character_refinement = normalize_character_refinement(json.loads(character_refinement_json) if character_refinement_json.strip() else None, len(image_items))
        swap_mask = normalize_swap_mask(json.loads(swap_mask_json) if swap_mask_json.strip() else None)
        if swap_mask and (len(video_items) != 1 or character_refinement):
            raise ValueError("Маска замены: одно исходное видео, без уточнения героя")
        reskin = normalize_reskin(json.loads(reskin_json) if reskin_json.strip() else None)
        if reskin and (len(video_items) != 1 or character_refinement or swap_mask):
            raise ValueError("Рескин: одно исходное видео, без маски замены и уточнения героя")
        if character_refinement:
            if len(video_items) != 1 or audio_items or not precompiled_prompt or emotional_direction or profile != "bf16":
                raise ValueError("Уточнение требует один готовый ролик, BF16 и готовый план без Qwen")
            if camera_state or color_grade or color_match or film_look or style_adapter != "none":
                raise ValueError("Уточнение сохраняет исходную сцену; дополнительные изменения отключены")
        effect = normalize_camera_effect(camera_effect.strip())
        speed_mode = (speed.strip() or "quality").lower()
        if speed_mode not in SPEED_MODES:
            raise ValueError(f"speed: только {', '.join(SPEED_MODES)}")
        if character_refinement:
            speed_mode = "quality"  # the refinement pass builds its own graph
        if effect and character_refinement:
            raise ValueError("Уточнение сохраняет исходную сцену; эффект камеры отключён")
        guide_frames_value = json.loads(guide_frames_json)
        if not isinstance(guide_frames_value, list):
            raise ValueError("guide_frames должен быть списком")
        guide_frames = [tuple(item) for item in guide_frames_value]
        validate_dense_prompt(
            reference_prompt(request.prompt, tuple(range(1, len(video_items) + 1)) if use_video_audio else ()),
            len(image_items), len(video_items), len(audio_items),
            len(video_items) if use_video_audio else 0,
        )
        manifest = _verify_manifest(manifest_json, {
            "images": image_items, "videos": video_items, "audios": audio_items,
        }) if manifest_json else None
        # «Продолжение»: that video leaves the Ref2VA references (and Qwen's view) and becomes a guide clip.
        full_manifest = manifest
        continuation_item = None
        if manifest is not None:
            manifest, video_items, continuation_item = split_continuation(manifest, video_items)
            if precompiled_prompt and is_ir_prompt(request.prompt):
                _validate_official_manifest_prompt(request.prompt, manifest, use_video_audio)
        if continuation_item is not None:
            if character_refinement:
                raise ValueError("Уточнение героев нельзя совмещать с «Продолжением» дубля")
            if not image_items and not video_items:
                raise ValueError("Для «Продолжения» нужно ещё фото героя/окружения или видео-референс: "
                                 "H3 Ref2VA не запускается без референсов")
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if effect:
        try:
            available = installed_effects(await comfy.embeddings())
        except Exception as exc:
            raise HTTPException(503, f"Не удалось проверить эмбеддинги ComfyUI: {exc}") from exc
        if effect not in available:
            raise HTTPException(422, f"Эффект камеры {effect} не установлен на GPU (models/embeddings/"
                                     f"{embedding_name(effect)}.safetensors; установщик с H3_CAMERA_EFFECTS=1)")
    registry = None
    fingerprint = None
    parsed_uuid = None
    if client_job_id:
        if not manifest:
            raise HTTPException(422, "Для client_job_id обязателен манифест")
        try:
            parsed_uuid = uuid.UUID(client_job_id)
            if str(parsed_uuid) != client_job_id:
                raise ValueError("client_job_id должен быть каноническим UUID")
            fingerprint = hashlib.sha256(json.dumps({
                "prompt": request.prompt, "duration": duration,
                "aspect_ratio": aspect_ratio, "quality": quality,
                "megapixels": request.megapixels, "profile": profile,
                "steps": steps, "seed": request.seed,
                "ref_image_size": ref_image_size,
                "use_video_audio": use_video_audio,
                # Only when set: fingerprints of jobs accepted before contract 5 stay identical.
                **({"allow_short_motion_reference": True} if allow_short_motion_reference else {}),
                # voice_rights_confirmed (removed form field), control_* and pose_people stay with
                # their old constant values so fingerprints of accepted jobs still match on resubmission.
                "voice_rights_confirmed": False,
                "control_type": control_type, "control_strength": 0.75,
                "pose_people": 1, "camera_state": camera_state,
                "camera_optics": camera_optics,
                **({"director_config": director_config} if director_config else {}),
                "guide_frames": guide_frames,
                "style_adapter": style_adapter,
                "cache_mode": cache_mode,
                **({"render_options": render_options} if render_options else {}),
                **({"cache_config": cache_config} if cache_config is not None else {}),
                # Only when not the default: earlier fingerprints stay identical.
                **({"sampling_profile": sampling_profile} if sampling_profile != DEFAULTS["sampling_profile"] else {}),
                **({"mode": mode} if mode != DEFAULTS["mode"] else {}),
                **({"character_refinement": character_refinement} if character_refinement else {}),
                # Only when present: jobs accepted without a grade/look keep their fingerprint.
                **({"color_grade": color_grade} if color_grade else {}),
                **({"color_match": color_match} if color_match else {}),
                **({"film_look": film_look} if film_look else {}),
                **({"camera_effect": effect} if effect else {}),
                "precompiled_prompt": precompiled_prompt,
                "emotional_direction": emotional_direction,
                # Only for A/B submissions: Studio fingerprints stay as they were.
                **({"ab": {**ab.describe(), "plan": ab_plan}} if ab else {}),
                "control_sha256": None,
                "manifest": full_manifest,
            }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            registry = _registry()
            existing = registry.get(client_job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if existing and existing["request_hash"] != fingerprint:
            raise HTTPException(409, "client_job_id уже использован с другим заданием")
        if existing:
            return _client_receipt(client_job_id, await recover_accepted(registry, comfy, existing))

    if manifest is None:
        raise HTTPException(422, "Для автоматической режиссуры Qwen нужен манифест файлов задания")
    if mode not in {"auto", "replace", "realistic", "animation3d", "animation2d"}:
        raise HTTPException(422, "Неизвестный стиль Studio")
    try:
        asset_kinds = {
            entry["asset_id"]: kind
            for group, kind in (("images", "image"), ("videos", "video"), ("audios", "audio"))
            for entry in manifest[group]
        }
        requested_style = {"realistic": "realism", "animation3d": "animation_3d",
                           "animation2d": "animation_2d"}.get(mode, "auto")
        enabled_adapters = {item.strip() for item in os.environ.get("H3_ENABLED_STYLE_ADAPTERS", "").split(",") if item.strip()}
        director_fallback = None
        plan = None
        if ab_plan is not None and set(ab_plan) == {"director_fallback"}:
            # A/B replay of a baseline Qwen could not plan: the same Studio-prompt fallback.
            director_fallback = str(ab_plan["director_fallback"])[:400]
        elif ab_plan is not None:
            # A/B replay: every variant renders the plan Qwen wrote for the baseline (same prompt,
            # roles, guide frames), and Qwen does not wake beside a running render.
            plan = ab_plan
        elif not precompiled_prompt and not (mode == "replace" and image_items and video_items) \
                and not await asyncio.to_thread(director_installed):
            # The same Studio-prompt fallback as a failed plan, without unloading H3 for a missing Qwen.
            director_fallback = DIRECTOR_MISSING
        elif not precompiled_prompt and not (mode == "replace" and image_items and video_items):
            await comfy.release_models_if_idle()
            director_refs, _ = await _build_director_references(manifest, {
                "images": image_items, "videos": video_items, "audios": audio_items,
            })
            try:
                director = LocalQwenDirector()
                plan = await asyncio.to_thread(
                    director.plan, normalize_voice_cast(prompt, manifest), director_refs, asset_kinds, request.length,
                    requested_style, emotional_direction=emotional_direction, camera_locked=bool(camera_state),
                )
            except (RuntimeError, ValueError) as exc:
                # Qwen is an optional planner. If it cannot produce a valid plan (broken JSON,
                # schema violation, no answer), render the Studio prompt directly — same seed,
                # size, steps, references and roles — instead of failing the job.
                # Never continue if Qwen may still hold VRAM.
                if "не освободила VRAM" in str(exc):
                    raise
                director_fallback = str(exc)[:400]
        use_precompiled = precompiled_prompt or plan is None
        qwen_plan = json.loads(json.dumps(plan)) if ab is not None and plan is not None else None
        if use_precompiled:
            # Server-side FIFO can enqueue a Studio-compiled prompt while another
            # H3 render is using the GPU. Do not wake Qwen beside the active render.
            # The original saved prompt, references, seed, size and steps stay intact.
            if requested_style == "auto":
                requested_style = {"realism": "realism", "illustration": "illustration"}.get(mode, "realism")
            if style_adapter == "none":
                style_adapter = {"animation3d": "style3d", "animation2d": "cartoon2d"}.get(mode, "none")
            if style_adapter not in {"none", "style3d", "cartoon2d", "combat", "realism_people", "character_swap",
                                     "character_swap_v15"}:
                raise ValueError("Неизвестная стилевая LoRA")
            if style_adapter != "none" and style_adapter not in enabled_adapters:
                raise ValueError("Стилевая LoRA недоступна на этом GPU-сервере")
            compiled = {
                "prompt": reference_prompt(request.prompt,
                    tuple(range(1, len(video_items) + 1)) if use_video_audio else ()),
                "style": requested_style,
                "style_adapter": style_adapter,
                # Same rule as preserve_explicit_camera_state on the Qwen path: a camera video
                # drives the camera, so the Studio trajectory must not compete with it.
                "camera_state": None if any(item.get("role") in {"Камера", "Движение и камера"}
                                            for item in manifest.get("videos", [])) else camera_state,
                "guide_frames": guide_frames, "quality_intent": "final",
                "audio_intent": "native_stereo_effects_music", "steps": steps,
                "cache_mode": cache_mode,
                "roles": {}, "visual_evidence": {},
            }
            request = GenerationRequest(
                compiled["prompt"], duration, aspect_ratio, quality, profile=profile,
                steps=steps, seed=parsed_seed, megapixels=request.megapixels,
                ref_image_size=ref_image_size,
            )
            # The camera node enters the graph only as a guide video (H3_CAMERA_GUIDE_VIDEO=1);
            # by default the trajectory reaches H3 as text and needs no node.
            required_nodes = required_nodes_for_plan(
                camera_guide_video_needed(compiled["camera_state"]), bool(guide_frames), style_adapter,
                has_camera_optics=bool(camera_optics),
            )
            if director_fallback and emotional_direction:
                compiled["prompt"] = emotional_performance_contract() + "\n\n" + compiled["prompt"]
        else:
            plan = preserve_explicit_camera_state(
                plan, camera_state, manifest, asset_kinds, request.length,
            )
            wardrobe_owners = {
                str(item.get("owner") or "").casefold()
                for item in manifest.get("images", [])
                if item.get("role") == "Костюм" and item.get("owner")
            }
            identity_with_wardrobe = {
                item["asset_id"] for item in manifest.get("images", [])
                if item.get("role") in {"Лицо героя", "Персонаж целиком"}
                and str(item.get("owner") or "").casefold() in wardrobe_owners
            }
            compiled = compile_director_plan(
                prompt, plan, asset_kinds, duration=request.duration,
                frame_count=request.length, requested_steps=steps,
                requested_cache=cache_mode, available_adapters=enabled_adapters,
                video_audio_count=len(video_items) if use_video_audio else 0,
                identity_with_wardrobe=identity_with_wardrobe,
            )
            if guide_frames:
                # Explicit Studio guide frames win over Qwen's, like the explicit camera above.
                compiled["guide_frames"] = guide_frames
            if style_adapter != "none":
                # An explicit Studio LoRA is applied or the job is refused; never silently "none".
                if style_adapter not in _STYLE_ADAPTER_STYLES:
                    raise HTTPException(422, f"Неизвестная стилевая LoRA «{style_adapter}»")
                if style_adapter not in enabled_adapters:
                    raise HTTPException(422, f"Стилевая LoRA «{style_adapter}» недоступна на этом GPU-сервере")
                needed_style = _STYLE_ADAPTER_STYLES[style_adapter]
                if compiled["style"] != needed_style or compiled["style_adapter"] not in {"none", style_adapter}:
                    raise HTTPException(422, (
                        f"Стилевая LoRA «{style_adapter}» требует стиль «{_STYLE_NAMES_RU[needed_style]}», "
                        f"а режиссёр Qwen выбрал «{_STYLE_NAMES_RU.get(compiled['style'], compiled['style'])}». "
                        "Выбери этот стиль в Studio или убери LoRA."))
                compiled["style_adapter"] = style_adapter
            if emotional_direction:
                compiled["prompt"] = emotional_performance_contract() + "\n\n" + compiled["prompt"]
            required_nodes = required_nodes_for_plan(
                camera_guide_video_needed(compiled["camera_state"]),
                bool(compiled["guide_frames"]), compiled["style_adapter"],
                has_camera_optics=bool(camera_optics),
            )
        if not use_precompiled and requested_style != "auto" and compiled["style"] != requested_style:
            raise ValueError("Qwen не применил выбранный стиль Studio; задание остановлено до GPU-графа")
        if not use_precompiled:
            compiled["prompt"] = normalize_motion_retention(compiled["prompt"], manifest.get("videos", []))
        if compiled["style_adapter"].startswith("character_swap"):
            performer_pairs = sum(len(item.get("performers") or []) for item in manifest.get("videos", []))
            if not image_items or len(video_items) != 1 or performer_pairs < 1:
                raise ValueError("Character Swap требует изображения героя, одно видео и явную пару «герой — исполнитель»")
        if not use_precompiled:
            compiled["prompt"] = reference_prompt(
                compiled["prompt"], tuple(range(1, len(video_items) + 1)) if use_video_audio else (),
            )
        if prompt_blocks not in ("studio", "none"):
            raise ValueError("prompt_blocks: studio или none")
        clean_prompt = prompt_blocks == "none"
        if not clean_prompt:
            compiled["prompt"] = apply_cast_and_clean_scene_contract(
                compiled["prompt"], [
                    {**video, "role": reference_video_role(manifest, compiled, index) or video.get("role")}
                    for index, video in enumerate(manifest.get("videos", []))
                ],
            )
        structured_direction = None if clean_prompt else director_config_direction(director_config)
        # Studio already compiled the same settings as a DIRECTOR STATE block. With a movement video a
        # second copy doubles their weight against the video (13ac71ee carried both). Picture-only jobs
        # keep both (the picture-only golden graph).
        official_format = is_ir_prompt(compiled["prompt"])  # six-section H3 prompt: send as written
        if structured_direction and official_format:
            # Plain sentences inside [Shot 1], each setting once. Framing, angle and camera move are already
            # in the shot's own opening (scene takes) or the agent's text; only lens, focus, light, colour
            # and style are added (audit 05.11: IR jobs used to lose all of these).
            compiled["prompt"] = place_in_first_shot(compiled["prompt"], director_config_sentences(
                director_config, skip=frozenset({"camera", "movement", "shot", "acting"}),
                node_grain=bool(film_look and film_look.get("grain"))))
        elif structured_direction and not (video_items and "DIRECTOR STATE —" in compiled["prompt"]):
            compiled["prompt"] = compiled["prompt"].rstrip() + "\n\n" + structured_direction
        animal_direction = animal_realism_direction(
            compiled["prompt"], compiled["style"], compiled["style_adapter"], compiled.get("visual_evidence"))
        if animal_direction and not official_format and not clean_prompt:
            compiled["prompt"] = compiled["prompt"].rstrip() + "\n\n" + animal_direction
        compiled["prompt"] = normalize_voice_cast(compiled["prompt"], manifest)
        request = GenerationRequest(
            compiled["prompt"], request.duration, request.aspect_ratio, request.quality,
            profile=request.profile, steps=compiled["steps"], seed=request.seed,
            megapixels=request.megapixels, ref_image_size=request.ref_image_size,
        )
        camera_state = compiled["camera_state"]
        # A camera video replaces the trajectory (above); its lens settings go with it instead of
        # failing the whole job with "optics need camera_state".
        camera_optics = normalize_camera_optics(camera_optics, camera_state) if camera_state else None
        guide_frames = compiled["guide_frames"]
        style_adapter = compiled["style_adapter"]
        validate_render_combination(render_options, style_adapter, bool(character_refinement))
        cache_mode = compiled["cache_mode"]
        if character_refinement:
            required_nodes = set(required_nodes) | {"H3CharacterTrack", "H3CharacterCrop", "H3CharacterComposite"}
        elif (effective_cache_mode(cache_mode, bool(video_items)) != "off"
              and not (ab is not None and ab.cache == "off")):  # ab_cache=off removes node 310
            # app/graph.py node 310; without it the job must stop here, not fail inside ComfyUI.
            required_nodes = set(required_nodes) | {"ApplyMiniMaxH3FirstBlockCache"}
        if color_grade:
            required_nodes = set(required_nodes) | {"H3ColorGrade"}
        if color_match:
            required_nodes = set(required_nodes) | {COLOR_MATCH_NODE}
        if film_look:
            required_nodes = set(required_nodes) | {"H3FilmLook"}
        if continuation_item is not None:
            required_nodes = set(required_nodes) | CONTINUATION_NODES
        if swap_mask:
            if not compiled["style_adapter"].startswith("character_swap"):
                raise ValueError("Маска замены работает только с Character Swap")
            if continuation_item is not None or guide_frames or hunt is not None or (ab is not None and (ab.hires or ab.turbo)):
                raise ValueError("Маска замены: без продолжения, guide-кадров, подбора seed, hires и турбо")
            required_nodes = set(required_nodes) | SWAP_MASK_NODES
        if reskin:
            if continuation_item is not None or guide_frames or hunt is not None or (ab is not None and (ab.hires or ab.turbo)):
                raise ValueError("Рескин: без продолжения, guide-кадров, подбора seed, hires и турбо")
            required_nodes = set(required_nodes) | RESKIN_NODES
        if required_nodes:
            try:
                node_info = await comfy.object_info()
            except Exception as exc:
                raise RuntimeError(f"Не удалось проверить ноды и веса ComfyUI: {exc}") from exc
            missing_nodes = sorted(required_nodes - set(node_info))
            if missing_nodes:
                raise ValueError("На текущем ComfyUI не установлены требуемые узлы: " + ", ".join(missing_nodes))
            missing_models = missing_model_files(style_adapter, node_info)
            if missing_models:
                raise ValueError("В ComfyUI не найдены требуемые файлы моделей: " + ", ".join(missing_models))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(503, f"Автоматическая режиссура MiniMax H3 не завершена: {exc}") from exc
    try:
        async def prepare_video_item(index: int, item: tuple[str, bytes, str]):
            if character_refinement:
                return await asyncio.to_thread(validate_refinement_video, item, request.length)
            role = reference_video_role(manifest, compiled, index)
            return await asyncio.to_thread(
                prepare_video_reference, item, request, keep_audio=use_video_audio,
                short_edge=None if ab is not None and ab.legacy_motion else reference_video_short_edge(role),
                minimum_duration_seconds=request.duration if motion_reference_requires_full_duration(role)
                and not allow_short_motion_reference else None,
            )

        prepared_videos = await asyncio.gather(*(
            prepare_video_item(index, item) for index, item in enumerate(video_items)
        ))
        continuation_clip = (await asyncio.to_thread(prepare_continuation_video, continuation_item, request)
                             if continuation_item is not None else None)
        audio_items = await asyncio.gather(*(
            asyncio.to_thread(prepare_audio_reference, item, request) for item in audio_items
        ))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    async def upload(items: list[tuple[str, bytes, str]]) -> list[str]:
        return [
            await comfy.upload_file(filename, data, content_type)
            for filename, data, content_type in items
        ]

    reference_names = await upload(image_items)
    video_names = await upload(prepared_videos)
    audio_names = await upload(audio_items)
    if color_match:
        # Scene colour continuity: Lab statistics of the reference picture, the same numbers Studio
        # computes for the «Цвет» preset; one clip-wide H3ColorGrade (app/color_match.py).
        try:
            color_match = {**color_match, **await asyncio.to_thread(
                lab_stats_from_image, image_items[color_match["picture"] - 1][1])}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    continuation = None
    if continuation_clip is not None:
        continuation = {"file": (await upload([continuation_clip["upload"]]))[0],
                        "frames": continuation_clip["frames"], "audio": continuation_clip["audio"]}
    try:
        build_args = dict(
            request=request,
            references=reference_names,
            reference_videos=video_names,
            reference_audios=audio_names,
            use_video_audio=use_video_audio,
            camera_state=camera_state,
            camera_optics=camera_optics,
            guide_frames=guide_frames,
            style_adapter=style_adapter,
            cache_mode=cache_mode,
            **({"cache_config": cache_config} if cache_config is not None else {}),
            sampling_profile=sampling_profile,
            **({"render_options": render_options} if render_options else {}),
            color_grade=color_grade,
            color_match=color_match,
            film_look=film_look,
            continuation=continuation,
            camera_effect=effect,
            speed=speed_mode,
        )
        base_graph = build_prompt(**build_args)
        if ab is not None and ab.build_overrides():
            if ab.camera_words and (not camera_state or is_ir_prompt(request.prompt)):
                raise ValueError("ab_camera_words: нужен legacy-промпт с траекторией камеры")
            if ab.color_match and not color_match:
                raise ValueError("ab_color_match: в задании нет color_match")
        graph = apply_ab_variant(build_prompt(**build_args, **ab.build_overrides()) if ab is not None
                                 and ab.build_overrides() else base_graph, ab)
        if character_refinement:
            graph = build_character_refinement(request, reference_names, video_names[0], character_refinement)
        if swap_mask:
            ref2va = graph[Node.REF2VA]["inputs"]
            graph = apply_swap_mask(graph, swap_mask, ref2va["width"], ref2va["height"], ref2va["length"])
        if reskin:
            ref2va = graph[Node.REF2VA]["inputs"]
            graph = apply_reskin(graph, reskin, ref2va["width"], ref2va["height"], ref2va["length"])
        if hunt is not None:
            graph = (seed_hunt.preview_graph if hunt["role"] == "preview" else seed_hunt.finish_graph)(graph, hunt)
        elif not character_refinement:
            # read-only research probes when the server's probe-all switch is on (app/probe_graph.py)
            graph = attach_research_probes(graph, client_job_id)
        director_summary = {
            **({"render_profile": {"profile": request.profile, "options": render_options,
                                    "model": graph["127"]["inputs"]["unet_name"],
                                    "encoder": graph["128"]["inputs"]["clip_name"],
                                    "quality_verified": False}}
               if request.profile != "bf16" or render_options else {}),
            **({"sampling_profile": sampling_profile} if sampling_profile != "standard" else {}),
            **({"swap_mask": len(swap_mask["performers"])} if swap_mask else {}),
            **({"reskin": reskin["strength"]} if reskin else {}),
            # Kept for Mac job history; structural control no longer exists.
            "style": compiled["style"], "control": "none", "control_asset_id": None,
            "camera_guide": camera_guide_video_needed(compiled["camera_state"]),
            "camera_optics": camera_optics,
            "director_config": director_config,
            "director_fallback": director_fallback,
            "color_grade": color_grade,
            # absent = summary unchanged; target_* are the measured Lab statistics of the reference.
            **({"color_match": color_match} if color_match else {}),
            "film_look": film_look,
            **({"camera_effect": effect} if effect else {}),
            **({"continuation": {"frames": continuation["frames"], "audio": continuation["audio"]}}
               if continuation else {}),  # absent = summary unchanged
            "character_refinement": character_refinement,
            "guide_frames": len(compiled["guide_frames"]),
            "quality_intent": compiled["quality_intent"],
            "audio_intent": compiled["audio_intent"], "steps": compiled["steps"],
            "cache_mode": compiled["cache_mode"], "style_adapter": compiled["style_adapter"],
            "roles": compiled["roles"], "visual_evidence": compiled["visual_evidence"],
        }
        if ab is not None:
            base_hash = graph_fingerprint(base_graph)
            director_summary["ab"] = {
                **ab.describe(), **graph_fingerprint(graph),
                # Equal across variants of one A/B run = the same prompt, seed, size and references.
                "base_graph_sha256": base_hash["graph_sha256"], "base_prompt_sha256": base_hash["prompt_sha256"],
                "prompt": graph["136"]["inputs"]["prompt"],
                # The raw Qwen plan: scripts/h3_ab.py sends it back as ab_director_plan_json.
                # {"director_fallback": ...} when Qwen could not plan: replayed as the same fallback.
                "director_plan": qwen_plan if qwen_plan is not None else (
                    {"director_fallback": director_fallback} if director_fallback else None),
            }
        if hunt is not None:
            director_summary["seed_hunt"] = {**hunt, "package": seed_hunt.package_rel(hunt)}
        director_summary["cache_execution"] = cache_from_graph(graph)
        director_summary["execution_record"] = {"version": 1, "engine": "h3",
            "stage": "graph_prepared", **graph_fingerprint(graph)}
        if is_ir_prompt(request.prompt):
            director_summary["ir_execution"] = graph_fingerprint(graph)
        director_json = json.dumps(director_summary, ensure_ascii=False, sort_keys=True)
        # Minted before the POST: if this process dies between ComfyUI accepting the graph
        # and the registry write, /api/jobs/by-client finds the job in ComfyUI by this id.
        planned_prompt_id = str(uuid.uuid4())
        if registry is not None:
            is_new, record = registry.accept(client_job_id, fingerprint,
                                             planned_remote_id=planned_prompt_id,
                                             director_json=director_json)
            if not is_new:
                return _client_receipt(client_job_id, await recover_accepted(registry, comfy, record))
        async with async_render_submission():
            prompt_id = await comfy.queue(graph, prompt_id=planned_prompt_id)
        if ab is not None:
            comfy.track_vram(prompt_id)
        if registry is not None:
            registry.queued(client_job_id, prompt_id, director_json)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Не удалось запустить генерацию: {exc}") from exc
    return {"job_id": prompt_id, "status": "queued", "client_job_id": client_job_id or None,
            "director": director_summary}


def _client_receipt(client_job_id: str, record: dict) -> dict:
    return {"client_job_id": client_job_id, "job_id": record["remote_job_id"],
            "status": record["state"] if record["remote_job_id"] else "needs_reconciliation",
            "director": json.loads(record["director_json"]) if record.get("director_json") else None}


# Shown on the Mac when ComfyUI no longer knows a job this API queued: its queue and
# history live in process memory, so a ComfyUI restart (or crash) drops them.
LOST_JOB_ERROR = ("ComfyUI на GPU больше не знает это задание: ComfyUI перезапускался или упал во время "
                  "рендера, результат потерян. Автоповтор отключён — запусти задание заново.")


def mark_lost(status: dict, job_id: str, registry: JobRegistry) -> dict:
    """`unknown` for a job this registry queued is a terminal loss, not a pending state."""
    if status.get("status") == "unknown" and registry.owns_remote(job_id):
        status = {**status, "lost": True, "error": LOST_JOB_ERROR}
    return status


@app.post("/api/graph-jobs", status_code=202, dependencies=[Depends(gpu_admission_dependency)])
async def create_graph_job(body: dict) -> dict:
    """Render a graph edited in ComfyUI (h3-studio-bridge). Studio sends only ids: both graphs are
    read from this ComfyUI, checked by app.graph_edit and queued unchanged under client_job_id."""
    from .graph_edit import edit_problems, graph_changes

    source_id, edit_id, client_job_id = (str(body.get(key) or "") for key in ("source_job_id", "edit_id", "client_job_id"))
    if not (source_id and edit_id and client_job_id):
        raise HTTPException(422, "Нужны source_job_id, edit_id и client_job_id")
    try:
        edit = await comfy.bridge(f"edited/{edit_id}")
        source = await comfy.bridge(f"graph/{source_id}")
    except Exception as exc:
        raise HTTPException(502, f"ComfyUI не отдал графы (h3-studio-bridge установлен?): {exc}") from exc
    if edit is None or source is None or edit.get("source_prompt_id") != source_id:
        raise HTTPException(404, "Правка или исходный граф не найдены в этом ComfyUI")
    graph = edit["prompt"]
    problems = edit_problems(source["prompt"], graph)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    changes = graph_changes(source["prompt"], graph)
    director_json = json.dumps({"comfyui_edit": {"edit_id": edit_id, "source_job_id": source_id, **changes},
                                "execution_record": {"version": 1, "engine": "h3", "stage": "comfyui_edit",
                                                     **graph_fingerprint(graph)}},
                               ensure_ascii=False, sort_keys=True)
    registry = _registry()
    planned_prompt_id = str(uuid.uuid4())
    try:
        is_new, record = registry.accept(client_job_id, f"comfyui-edit:{edit_id}",
                                         planned_remote_id=planned_prompt_id, director_json=director_json)
        if not is_new:
            return _client_receipt(client_job_id, await recover_accepted(registry, comfy, record))
        async with async_render_submission():
            prompt_id = await comfy.queue(graph, prompt_id=planned_prompt_id)
        registry.queued(client_job_id, prompt_id, director_json)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Не удалось запустить генерацию: {exc}") from exc
    return {"job_id": prompt_id, "status": "queued", "client_job_id": client_job_id,
            "director": json.loads(director_json)}


@app.get("/api/jobs/by-client/{client_job_id}")
async def job_by_client(client_job_id: str) -> dict:
    registry = _registry()
    record = await recover_accepted(registry, comfy, registry.get(client_job_id))
    if record is None:
        raise HTTPException(404, "Задание с таким client_job_id не найдено")
    return _client_receipt(client_job_id, record)


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    status = mark_lost(await comfy.status(job_id), job_id, _registry())
    if status["status"] in {"success", "error"} and hasattr(comfy, 'cache_metrics'):
        metrics = await comfy.cache_metrics(job_id)
        if metrics is not None:
            status['cache_runtime'] = metrics
    if status["status"] == "success":
        status["video_url"] = f"/api/jobs/{job_id}/video"
        status.pop("video", None)
    return status


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    if not _registry().owns_remote(job_id):
        raise HTTPException(404, "Задание не принадлежит Studio")
    cancelled = await comfy.cancel(job_id)
    if not cancelled:
        raise HTTPException(409, "Задание уже завершено или не найдено на GPU")
    return {"cancelled": True}


@app.get("/api/jobs/{job_id}/preview")
async def job_preview(job_id: str) -> Response:
    if not _registry().owns_remote(job_id):
        raise HTTPException(404, "Задание не принадлежит Studio")
    frame = comfy.preview_frames.get(job_id)
    if not frame:
        raise HTTPException(404, "Предпросмотр ещё не получен")
    return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/video")
async def job_video(job_id: str) -> StreamingResponse:
    status = await comfy.status(job_id)
    if status["status"] != "success":
        raise HTTPException(409, "Видео ещё не готово")
    video = status["video"]
    headers = {"Content-Disposition": f'inline; filename="{video["filename"]}"'}
    return StreamingResponse(comfy.stream_video(video), media_type="video/mp4", headers=headers)


app.include_router(acting_router)
app.include_router(make_upscale_router(comfy, _registry))
app.include_router(make_audio_jobs_router(comfy))
app.include_router(make_scenarist_router(comfy))
app.include_router(make_context_ir_router())
app.include_router(make_scene_planner_router(comfy))
app.include_router(make_scene_edits_router(comfy))
app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="studio")
