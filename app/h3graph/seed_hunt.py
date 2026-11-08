"""Seed hunt: N cheap previews of one job, then the chosen one finished at full size.

The community SEEDHUNTER recipe on our weights and quality rules (no turbo, no fewer steps): a preview is
pass 1 of the two-pass hires render (app/h3graph/hires.py) — every step, a quarter of the pixels, its own
seed — that keeps its latent and conditioning in an LQ package (ComfyUI-MiniMaxH3-LatentUpscaler,
output/h3_lq_stash/) and decodes a small MP4 to look at. Finishing loads that package instead of sampling
pass 1 again: the same latent is upscaled ×2 and pass 2 re-runs the schedule tail at full size, so the
finished clip is the preview the user picked, only sharper. The finish graph needs no text encoder or
reference loaders — the conditioning comes from the package.
"""
from __future__ import annotations

import json
import uuid

from .hires import GUIDER, SPLIT, UPSCALE, apply_hires
from .nodes import Node, node, out

SAVE_NODE = "MiniMaxH3LQPackageSave"
LOAD_NODE = "MiniMaxH3LQPackageLoad"
PREVIEW_NODES = frozenset({SAVE_NODE})
FINISH_NODES = frozenset({LOAD_NODE})
PACKAGE = "364"
PROJECT = "studio_seed_hunt"
COUNT_RANGE = (2, 6)
ROLES = ("preview", "finish")


def parse(raw: str) -> dict | None:
    """seed_hunt_json of a GPU job: {"role": "preview"|"finish", "hunt_id": UUID, "index": 1..6}."""
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("seed_hunt_json: некорректный JSON") from exc
    if not isinstance(data, dict) or data.get("role") not in ROLES:
        raise ValueError("seed_hunt_json: role preview или finish")
    try:
        hunt_id = str(uuid.UUID(str(data.get("hunt_id"))))
    except ValueError as exc:
        raise ValueError("seed_hunt_json: hunt_id должен быть UUID") from exc
    index = data.get("index")
    if type(index) is not int or not 1 <= index <= COUNT_RANGE[1]:
        raise ValueError(f"seed_hunt_json: index 1–{COUNT_RANGE[1]}")
    return {"role": data["role"], "hunt_id": hunt_id, "index": index}


def package_rel(hunt: dict) -> str:
    """Where the preview's package lands under output/h3_lq_stash (a fresh hunt id: no suffix)."""
    return f"{PROJECT}/{hunt['hunt_id']}/s{hunt['index']}"


def required_nodes(hunt: dict) -> frozenset[str]:
    from .hires import REQUIRED_NODES
    return REQUIRED_NODES | (PREVIEW_NODES if hunt["role"] == "preview" else FINISH_NODES)


def _two_pass(graph: dict) -> dict:
    if UPSCALE not in graph:
        apply_hires(graph)
    return graph


def preview_graph(graph: dict, hunt: dict) -> dict:
    """Pass 1 only, its latent kept in a package and decoded small. In place; returns the graph."""
    _two_pass(graph)
    conditioning = graph[UPSCALE]["inputs"]["positive"]
    for key in (SPLIT, UPSCALE, GUIDER, "363"):
        graph.pop(key)
    graph[PACKAGE] = node(SAVE_NODE, f"Подбор seed: превью {hunt['index']}",
                          samples=out(Node.SAMPLE), positive=conditioning,
                          filename_prefix=f"s{hunt['index']}", project=f"{PROJECT}/{hunt['hunt_id']}",
                          seed=graph[Node.NOISE]["inputs"]["noise_seed"], note="Studio seed hunt")
    graph[Node.DECODE_VIDEO]["inputs"]["samples"] = out(PACKAGE)
    graph[Node.DECODE_AUDIO]["inputs"]["samples"] = out(PACKAGE)
    return graph


def finish_graph(graph: dict, hunt: dict) -> dict:
    """Pass 2 of the picked preview: its package replaces pass 1. In place; returns the graph."""
    _two_pass(graph)
    graph[PACKAGE] = node(LOAD_NODE, f"Подбор seed: превью {hunt['index']}",
                          selection_json=json.dumps({"version": 1, "root": "h3_lq_stash",
                                                     "packages": [package_rel(hunt)]}),
                          load_source="selection list", chunk_mode="direct load", scene_threshold=27.0,
                          min_scene_seconds=1.0, max_scene_seconds=5.0, index=0, index_mode="fixed", edit_json="")
    graph[UPSCALE]["inputs"]["samples"] = out(PACKAGE, 0)
    graph[UPSCALE]["inputs"]["positive"] = out(PACKAGE, 1)
    return graph
