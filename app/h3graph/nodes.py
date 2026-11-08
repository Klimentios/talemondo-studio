"""Semantic roles of the H3 graph nodes.

The ComfyUI ids are a contract: saved jobs, the A/B harness (app/ab_variant.py), the golden tests and
scripts/h3_graph_validate.py read them. New code names the role (Node.MODEL), never the number ("127").
"""
from __future__ import annotations


class Node:
    SAVE = "92"
    VIDEO_VAE = "119"
    AUDIO_VAE = "120"
    DECODE_AUDIO = "121"
    DECODE_VIDEO = "122"
    SAMPLER = "123"
    SCHEDULER = "124"
    SAMPLE = "125"
    GUIDER = "126"
    MODEL = "127"
    TEXT_ENCODER = "128"
    NOISE = "129"
    MUX = "130"
    REF2VA = "136"
    CACHE = "310"
    LORA = "311"
    LORA_SECOND = "312"
    CAMERA_GUIDE = "330"
    COLOR_MATCH = "339"
    COLOR_GRADE = "340"
    FILM_LOOK = "341"

    # Ranges: reference loaders count up from FIRST_LOADER; scene guides count up from FIRST_SCENE_GUIDE.
    FIRST_LOADER = 137
    FIRST_SCENE_GUIDE = 320


def out(node_id: str, slot: int = 0) -> list:
    """A ComfyUI link to an output slot."""
    return [node_id, slot]


def node(class_type: str, title: str, **inputs: object) -> dict[str, object]:
    return {"inputs": inputs, "class_type": class_type, "_meta": {"title": title}}
