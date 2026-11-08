"""ComfyUI graph of one H3 Ref2VA job, assembled from app/h3graph modules.

The output is pinned byte for byte by tests/test_graph_fingerprint.py (59 option combinations),
tests/test_render_golden.py and the approved picture-only recipe (tests/test_approved_recipe.py).
"""
from __future__ import annotations

from .camera_optics import normalize_camera_optics
from .color_grade import normalize_color_grade, normalize_film_look
from .contract import GenerationRequest, reference_prompt
from .h3_ir import is_ir_prompt
from .h3graph import core, prompt as h3prompt
from .h3graph.camera import (STATIC_FRAMING_LOCK, apply_camera, camera_guide_size,  # noqa: F401 (public API)
                             camera_guide_video_needed, is_static_camera_state)
from .h3graph.hires import apply_hires
from .h3graph.guides import chain_conditioning, check_scene_guides, guide_frame_sentence, resolve_guide_frame  # noqa: F401
from .h3graph.model_chain import (STYLE_ADAPTERS as _STYLE_ADAPTERS, build_model_chain,  # noqa: F401
                                  check_adapter, effective_cache_mode, resolve_cache_mode)
from .h3graph.nodes import Node, node as _node  # noqa: F401
from .h3graph.post import build_post_chain
from .h3graph.prompt import COMBAT_CHOREOGRAPHY_DIRECTION  # noqa: F401
from .h3graph.references import add_loaders, check_counts
from .reference_manifest import validate_dense_prompt
from .render_profiles import apply_render_options

_PROFILES = core.PROFILES
SPEED_MODES = ("quality",)


def build_prompt(
    request: GenerationRequest,
    references: list[str],
    reference_videos: list[str] | None = None,
    reference_audios: list[str] | None = None,
    use_video_audio: bool = True,
    style_adapter: str = "none",
    cache_mode: str | None = None,
    sampling_profile: str = "standard",
    cache_config: dict | None = None,
    camera_state: dict | None = None,
    camera_optics: dict | None = None,
    guide_frames: list[tuple[int, int]] | None = None,
    color_grade: dict | None = None,
    color_match: dict | None = None,
    film_look: dict | None = None,
    camera_words: bool = False,
    continuation: dict | None = None,
    color_match_impl: str = "h3colorgrade",
    camera_effect: str | None = None,
    speed: str = "quality",
    hires: dict | None = None,
    render_options: dict | None = None,
) -> dict[str, dict[str, object]]:
    """ComfyUI graph of one H3 job.

    camera_words (A/B only, app/ab_variant.py): a legacy prompt gets the camera move in the official
    words (app/camera_words.py) instead of the numeric compile_projected text.
    An official six-section prompt (is_ir_prompt) is never prefixed: it carries its own camera words.
    continuation ({"file", "frames", "audio"}, app/continuation.py): the last 22 frames and their sound
    of a previous clip anchored at frame 0 (MiniMaxH3AddGuide); not a Ref2VA reference.
    color_match: {picture, strength, method, target_mean, target_std} — the scene match runs first
    (node 339), a preset color_grade after it (340). color_match_impl "colormatch_v2" is the A/B
    variant with KJNodes ColorMatchV2 (per frame) instead of the clip-wide H3ColorGrade.
    camera_effect: an official prompt embedding (app/camera_effects.py), written as embedding:<name>.
    hires ({"method", "tail"}, A/B only): two-pass render, app/h3graph/hires.py.
    """
    if speed not in SPEED_MODES:
        raise ValueError("Доступен только полный режим качества")
    reference_videos = reference_videos or []
    reference_audios = reference_audios or []
    guide_frames = guide_frames or []
    check_counts(len(references), len(reference_videos), len(reference_audios))
    check_adapter(style_adapter)
    official_prompt = is_ir_prompt(request.prompt)
    # The six-section format must open with "subject_definitions:" and names the camera move in words;
    # neither the numeric camera text nor an unlabelled guide video may be added to it.
    guide_video = camera_guide_video_needed(camera_state) and not official_prompt
    if len(reference_videos) + guide_video > 3:
        raise ValueError("Камера и пользовательские видео вместе ограничены тремя видеогидами H3")
    camera_optics = normalize_camera_optics(camera_optics, camera_state)
    color_grade = normalize_color_grade(color_grade)
    film_look = normalize_film_look(film_look)
    cache_mode = resolve_cache_mode(cache_mode)

    graph = core.skeleton(request, sampling_profile)
    slots = add_loaders(graph, references, reference_videos, reference_audios, use_video_audio)

    text = h3prompt.style_prompt(request.prompt, style_adapter)
    camera_sentences = apply_camera(
        graph, slots, camera_state=camera_state, camera_optics=camera_optics, length=request.length,
        width=request.width, height=request.height, camera_words=camera_words,
        official_prompt=official_prompt, guide_video=guide_video)
    resolved_guides = check_scene_guides(guide_frames, len(references), request.length, continuation)
    additions = h3prompt.option_sentences(text, camera_sentences, resolved_guides, request.length, continuation)
    text = h3prompt.final_prompt(text, additions, style_adapter, camera_effect)

    prompt = reference_prompt(text, video_audio_indices=tuple(slots.video_audio_indices))
    validate_dense_prompt(prompt, len(references), slots.video_count, len(reference_audios),
                          len(slots.video_audio_indices))
    graph[Node.REF2VA] = _node("MiniMaxH3ReferenceToVideo", "MiniMax H3 Ref2VA", **{
        "width": request.width,
        "height": request.height,
        "length": request.length,
        "ref_image_size": request.ref_image_size,
        "clip": [Node.TEXT_ENCODER, 0],
        "vae": [Node.VIDEO_VAE, 0],
        "audio_vae": [Node.AUDIO_VAE, 0],
        **slots.inputs,
        "prompt": prompt,
    })

    core.wire_conditioning(graph, chain_conditioning(graph, slots, guide_frames, continuation))
    cache_mode = effective_cache_mode(cache_mode, bool(reference_videos))
    core.wire_model(graph, build_model_chain(graph, cache_mode, style_adapter, cache_config))
    core.wire_frames(graph, build_post_chain(graph, slots, color_match=color_match,
                                             color_match_impl=color_match_impl,
                                             color_grade=color_grade, film_look=film_look))
    if hires:
        apply_hires(graph, **hires)
    apply_render_options(graph, render_options, style_adapter)
    return graph
