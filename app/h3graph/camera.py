"""The 3D camera move: words in the prompt, or (opt-in) a rendered guide video as one more <Video>."""
from __future__ import annotations

import json
import os

from ..camera_guide_vendor.camera_prompt import compile_projected
from ..camera_words import camera_phrase_from_state
from .core import FPS
from .nodes import Node, node, out
from .references import ReferenceSlots

_CAMERA_POSE_KEYS = ("orbit", "elevation", "distance", "target", "fov",
                     "focal_length_mm", "aperture_f", "focus_distance_m")

STATIC_FRAMING_LOCK = (
    "FRAMING LOCK: keep exactly this shot size and composition from the first frame to the last. "
    "No close-up inserts, no push-in or zoom to the face, no cut to a portrait at the start or end; "
    "subjects keep the same scale in frame throughout."
)


def is_static_camera_state(camera_state: dict | None) -> bool:
    """A locked camera: every key frame has the same pose, lens and focus."""
    keys = (camera_state or {}).get("keys") or []
    if not keys:
        return False
    first = {name: keys[0].get(name) for name in _CAMERA_POSE_KEYS}
    return all({name: key.get(name) for name in _CAMERA_POSE_KEYS} == first for key in keys[1:])


def camera_guide_size(width: int, height: int) -> tuple[int, int]:
    """Canvas of the rendered camera guide (a mannequin on a floor grid).

    Ref2VA encodes a reference video at its own size, never upscaled (nodes_minimax_h3.py), so the
    guide adds (w/16)*(h/16) tokens to every latent frame. It only has to carry the camera path:
    256x448 adds 448 tokens per latent frame instead of 1792 at 512x896, while the rendered
    clip keeps its own resolution. H3_CAMERA_GUIDE_SHORT_EDGE=512 restores the previous size.
    """
    short = int(os.environ.get("H3_CAMERA_GUIDE_SHORT_EDGE", "256"))
    if not 64 <= short <= 768 or short % 32:
        raise ValueError("H3_CAMERA_GUIDE_SHORT_EDGE: 64–768, кратно 32")
    long = max(32, round(short * 1.75 / 32) * 32)
    return (short, long) if height > width else (long, short)


def camera_guide_video_needed(camera_state: dict | None) -> bool:
    # The guide video shows a mannequin moving through the set. Ref2VA reads it as a <Video>
    # reference with a figure in it, and that figure competes with the photo references:
    # a push-in with the guide dropped a hero's costume from <Picture 2>,
    # while the same scene without it kept every reference. The move itself always reaches H3
    # as text (compile_projected: distances, angles, lens, rack focus). The guide also added ~30 %
    # tokens. H3_CAMERA_GUIDE_VIDEO=1 sends the video for moves where the exact path matters more.
    if not camera_state or os.environ.get("H3_CAMERA_GUIDE_VIDEO") != "1":
        return False
    return not is_static_camera_state(camera_state) or os.environ.get("H3_STATIC_CAMERA_GUIDE") == "1"


def apply_camera(graph: dict, slots: ReferenceSlots, *, camera_state: dict | None, camera_optics: dict | None,
                 length: int, width: int, height: int, camera_words: bool, official_prompt: bool,
                 guide_video: bool) -> list[str]:
    """Adds the camera to the job; returns the prompt sentences for a legacy (non-IR) prompt.

    An official six-section prompt names the move in its own words and gets nothing here.
    """
    if not camera_state or official_prompt:
        return []
    focus_mode = (camera_optics or {}).get("focus_mode")
    if not guide_video:
        additions = [camera_phrase_from_state(camera_state, length) if camera_words else
                     compile_projected(camera_state, frames=length, fps=24, focus_mode=focus_mode)]
        if is_static_camera_state(camera_state) and os.environ.get("H3_FRAMING_LOCK") == "1":
            # Opt-in: an explicit framing lock for static cameras (af300571 opened on a face close-up).
            # Off by default at the user's request; the camera text itself carries the framing.
            additions.append(STATIC_FRAMING_LOCK)
        return additions
    camera_json = json.dumps(camera_state, ensure_ascii=False, separators=(",", ":"))
    guide_width, guide_height = camera_guide_size(width, height)
    graph[Node.CAMERA_GUIDE] = node(
        "H3OperatorCamera" if camera_optics else "H3CameraGuide",
        "Optical camera trajectory to RGB guide" if camera_optics else "Camera trajectory to RGB guide",
        camera_state=camera_json, width=guide_width, height=guide_height,
        frames=length, fps=FPS, subject_shape="mannequin",
        floor_cues="plain", source_mode="render", video_path="",
        **(camera_optics or {}),
    )
    slot = slots.add_video_slot(out(Node.CAMERA_GUIDE))
    camera_prompt = compile_projected(camera_state, frames=length, fps=24, focus_mode=focus_mode)
    return [f"A generated camera guide follows <Video {slot + 1}>; use it for camera trajectory only. " + camera_prompt]
