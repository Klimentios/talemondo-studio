"""Masked hero swap contract and graph wiring (custom nodes: app/comfy_nodes/h3_swap_mask, h3_character_refine).

swap_mask = {"performers": [{"name": "HERO", "box": [x, y, w, h]}, ...], "grow": 24, "feather": 0}
box: the source performer on the first frame of the part, 0..1 of the FULL source frame (H3SourceFrames
moves it into the centre crop of the job canvas). The track follows each one
through the clip; only those regions are regenerated, the rest stays the source picture.
"""
from __future__ import annotations

import json
import math

from .h3graph.nodes import Node, node, out

SWAP_MASK_NODES = frozenset({"H3SourceFrames", "H3SourceEditMask", "H3CharacterTrack", "H3CharacterMask"})
_FRAMES, _TRACK, _MASK, _EDIT = "601", "602", "603", "604"


def normalize_swap_mask(value) -> dict | None:
    if value in (None, {}):
        return None
    if not isinstance(value, dict) or set(value) - {"performers", "grow", "feather"}:
        raise ValueError("Маска замены: performers, grow, feather")
    performers = value.get("performers")
    if not isinstance(performers, list) or not 1 <= len(performers) <= 4:
        raise ValueError("Маска замены: 1–4 исполнителя")
    clean, names = [], set()
    for item in performers:
        if not isinstance(item, dict) or set(item) != {"name", "box"}:
            raise ValueError("Исполнитель маски: name и box")
        name, box = item["name"], item["box"]
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or "\n" in name or name.casefold() in names:
            raise ValueError("Исполнитель маски: имя героя, у каждого своё")
        if not isinstance(box, list) or len(box) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in box):
            raise ValueError("Область исполнителя: x, y, ширина, высота от 0 до 1")
        x, y, w, h = (float(v) for v in box)
        # A box that runs a little past the frame edge (a brain box around a body at the edge) is clamped.
        if not (-0.05 <= x < 1 and -0.05 <= y < 1 and w > 0.01 and h > 0.01 and x + w <= 1.05 and y + h <= 1.05):
            raise ValueError("Область исполнителя: x, y, ширина, высота от 0 до 1")
        left, top = max(0.0, x), max(0.0, y)
        x, y, w, h = (round(v, 6) for v in (left, top, min(1.0, x + w) - left, min(1.0, y + h) - top))
        names.add(name.casefold())
        clean.append({"name": name.strip(), "box": [x, y, w, h]})
    grow, feather = value.get("grow", 24), value.get("feather", 0)
    if type(grow) is not int or not 0 <= grow <= 128 or type(feather) is not int or not 0 <= feather <= 64:
        raise ValueError("Маска замены: grow 0–128 px, feather 0–64 px")
    return {"performers": clean, "grow": grow, "feather": feather}


def _video_components(graph: dict) -> str:
    for key, item in graph.items():
        if item["class_type"] == "GetVideoComponents" and item.get("_meta", {}).get("title") == "@video1 components":
            return key
    raise ValueError("Маска замены: в графе нет исходного видео")


def apply_swap_mask(graph: dict, spec: dict, width: int, height: int, length: int) -> dict:
    """Source frames → SAM2 tracks → performer mask → source latent with a noise mask → sampler."""
    components = _video_components(graph)
    characters = [{"name": p["name"], "box": p["box"]} for p in spec["performers"]]
    # Boxes are drawn on the full source frame; H3SourceFrames crops to the job aspect and moves them.
    graph[_FRAMES] = node("H3SourceFrames", "Маска: кадры исходника", images=out(components),
                          width=width, height=height, length=length,
                          characters_json=json.dumps(characters, ensure_ascii=False))
    graph[_TRACK] = node("H3CharacterTrack", "Маска: трек исполнителей", images=out(_FRAMES),
                         characters_json=out(_FRAMES, 1))
    graph[_MASK] = node("H3CharacterMask", "Маска: исполнители", tracks=out(_TRACK),
                        characters=",".join(str(i) for i in range(len(characters))), mode="replace",
                        grow=spec["grow"], feather=spec["feather"])
    sample = graph[Node.SAMPLE]["inputs"]
    graph[_EDIT] = node("H3SourceEditMask", "Маска: только исполнители", latent=sample["latent_image"],
                        vae=out(Node.VIDEO_VAE), images=out(_FRAMES), mask=out(_MASK))
    sample["latent_image"] = out(_EDIT)
    return graph
