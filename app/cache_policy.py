"""Validated FirstBlockCache configuration and a report of the actual submitted node.

Temporal guard measures temporal slices; it is not a face/eye ROI quality guarantee.
Historical safe/fast graph inputs stay unchanged for recipe reproducibility.
"""
from __future__ import annotations

import math

CACHE_MODES = ("off", "safe", "fast", "custom")
SAFE = dict(threshold=0.08, start_percent=0.10, end_percent=0.95,
            max_consecutive_hits=2, temporal_guard=True)
FAST = {**SAFE, "threshold": 0.10, "temporal_guard": False}
CUSTOM_MODE = "Custom — manual values"


def normalize_cache_config(mode: str, value: dict | None = None) -> dict | None:
    if mode not in CACHE_MODES:
        raise ValueError("Кэш MiniMax H3: off, safe, fast или custom")
    if mode != "custom":
        if value is not None:
            raise ValueError("Ручные параметры кэша применяются только в режиме CUSTOM")
        return None
    if not isinstance(value, dict) or set(value) != set(SAFE):
        raise ValueError("CUSTOM: нужны threshold, start_percent, end_percent, max_consecutive_hits, temporal_guard")
    for key in ("threshold", "start_percent", "end_percent"):
        n = value[key]
        if type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1:
            raise ValueError(f"CUSTOM: {key} должен быть числом от 0 до 1")
    if value["start_percent"] >= value["end_percent"]:
        raise ValueError("CUSTOM: начало окна должно быть меньше конца")
    if type(value["max_consecutive_hits"]) is not int or not 1 <= value["max_consecutive_hits"] <= 20:
        raise ValueError("CUSTOM: max_consecutive_hits — целое число от 1 до 20")
    if type(value["temporal_guard"]) is not bool:
        raise ValueError("CUSTOM: temporal_guard должен быть true или false")
    return dict(value)


def cache_from_graph(graph: dict) -> dict:
    nodes = []
    for node_id, item in graph.items():
        if item.get("class_type") != "ApplyMiniMaxH3FirstBlockCache":
            continue
        inputs = item["inputs"]
        mode = inputs["mode"]
        if mode == CUSTOM_MODE:
            effective = normalize_cache_config("custom", {k: inputs[k] for k in SAFE})
        elif mode == "H3 Fast — 0.10 / max 2":
            effective = dict(FAST)
        else:
            effective = None  # Unknown upstream preset: do not invent its parameters.
        nodes.append({"node_id": str(node_id), "node_mode": mode, "effective": effective})
    return {"version": 1, "enabled": bool(nodes), "nodes": nodes,
            "effective": nodes[0]["effective"] if len(nodes) == 1 else None,
            "runtime_counters": None}


def custom_available(node_info: dict) -> bool:
    from .object_info import input_options
    spec = node_info.get("ApplyMiniMaxH3FirstBlockCache") or {}
    inputs = spec.get("input", {})
    fields = {**inputs.get("required", {}), **inputs.get("optional", {})}
    return CUSTOM_MODE in input_options(node_info, "ApplyMiniMaxH3FirstBlockCache", "mode") and set(SAFE) <= set(fields)
