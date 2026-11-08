"""Reskin of a source video (A/B, 05.11.2026): the source clip is the starting latent, not pure noise.

The source frames are VAE-encoded into the target latent (H3SourceEditMask with a full mask from SolidMask) and the
scheduler starts part-way down (BasicScheduler denoise = strength), so composition, motion, camera and acting stay
the source's while the look (heroes, world, medium) is redrawn from the reference pictures and the prompt. Lower
strength keeps more of the source, higher strength lets the heroes' own shapes through. Same idea as SDEdit /
"denoise strength" in image-to-image; no new server nodes (h3-swap-mask + ComfyUI core SolidMask).
"""
from __future__ import annotations

import math

from .h3graph.nodes import Node, node, out
from .swap_mask import _video_components

RESKIN_NODES = frozenset({"H3SourceFrames", "H3SourceEditMask", "SolidMask"})
_FRAMES, _MASK, _EDIT = "611", "612", "613"
STRENGTH_RANGE = (0.3, 0.95)


def normalize_reskin(value) -> dict | None:
    if value in (None, {}):
        return None
    if not isinstance(value, dict) or set(value) != {"strength"}:
        raise ValueError("Рескин: только strength")
    strength = value["strength"]
    low, high = STRENGTH_RANGE
    if type(strength) not in (int, float) or not math.isfinite(strength) or not low <= strength <= high:
        raise ValueError(f"Рескин: сила шума от {low} до {high}")
    return {"strength": round(float(strength), 3)}


def apply_reskin(graph: dict, spec: dict, width: int, height: int, length: int) -> dict:
    """Source frames → full-frame edit latent → sampler that starts at `strength` of the noise schedule."""
    components = _video_components(graph)
    graph[_FRAMES] = node("H3SourceFrames", "Рескин: кадры исходника", images=out(components),
                          width=width, height=height, length=length, characters_json="[]")
    graph[_MASK] = node("SolidMask", "Рескин: весь кадр", value=1.0, width=width, height=height)
    sample = graph[Node.SAMPLE]["inputs"]
    graph[_EDIT] = node("H3SourceEditMask", "Рескин: исходник в латент", latent=sample["latent_image"],
                        vae=out(Node.VIDEO_VAE), images=out(_FRAMES), mask=out(_MASK))
    sample["latent_image"] = out(_EDIT)
    graph[Node.SCHEDULER]["inputs"]["denoise"] = spec["strength"]
    return graph
