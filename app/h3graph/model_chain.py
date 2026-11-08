"""What sits between the Ref2VA weights and the sampler: FirstBlockCache, then style/character LoRAs."""
from __future__ import annotations

import os

from .nodes import Node, node, out

from ..cache_policy import CACHE_MODES, normalize_cache_config

# Adapters are advertised only when the corresponding file is installed.
STYLE_ADAPTERS = {
    "style3d": ("3dsrx_minimax_h3_rank16-step00001250.safetensors", 1.0),
    "cartoon2d": ("minimax-h3-rough-2d-cartoon-illustration-1200.safetensors", 1.0),
    "realism_people": ("h3-realism-people-t2v-i2v-r2v.safetensors", 0.7),
    # Experimental H3 Ref2VA character replacement. Upstream recommends 1.0; no trigger word.
    "character_swap": ("h3_character_swap_pro4500_1000.safetensors", 1.0),
    # akatz V1.5 (17.10.2026, upstream "recommended", strength 1.0): A/B against V1 via Studio swap_lora.
    "character_swap_v15": ("h3_character_swap_v1.5.safetensors", 1.0),
}
COMBAT_LORAS = (("wushu_action_h3_lora_v5_3000_pruned.safetensors", 0.5),
                ("wushu_spatial_physics_clean_3000_pruned.safetensors", 0.3))
ADAPTER_NAMES = frozenset({"none", "combat", *STYLE_ADAPTERS})


def check_adapter(style_adapter: str) -> None:
    if style_adapter not in ADAPTER_NAMES:
        raise ValueError("Неизвестная стилевая LoRA")


def resolve_cache_mode(cache_mode: str | None) -> str:
    cache_mode = (cache_mode or os.environ.get("H3_CACHE_MODE", "safe")).lower()
    if cache_mode not in CACHE_MODES:
        raise ValueError("Кеш MiniMax H3 должен быть off, safe, fast или custom")
    return cache_mode


def effective_cache_mode(cache_mode: str, has_reference_videos: bool) -> str:
    """The cache mode the graph really uses: the user's choice.

    The named Fast preset judges reuse by the global mean and can soften fast local motion (arms,
    hands); Safe adds the temporal guard. Studio lets the user pick; nothing is overridden.
    """
    # The user's choice is kept (2026-10-05). Forcing Safe for every video job (2026-10-04 evening) made
    # movement-video renders 2-2.5x slower than the day before (13ac71ee 17 min vs db66c0b8 43 min) and
    # left the user-approved dance 741aec51, rendered with Fast, unreproducible.
    return cache_mode


def build_model_chain(graph: dict, cache_mode: str, style_adapter: str, cache_config: dict | None = None) -> list:
    """Adds cache and LoRA nodes after the weights; returns the model the sampler uses."""
    custom = normalize_cache_config(cache_mode, cache_config)
    model_source = out(Node.MODEL)
    if cache_mode != "off":
        modes = {
            "safe": "Custom — manual values",
            "custom": "Custom — manual values",
            "fast": "H3 Fast — 0.10 / max 2",
        }
        graph[Node.CACHE] = node(
            "ApplyMiniMaxH3FirstBlockCache", "H3 FirstBlockCache",
            # threshold is used only by the "Custom" (safe) mode; the Fast preset fixes 0.10.
            model=model_source, mode=modes[cache_mode], threshold=0.08,
            start_percent=0.10, end_percent=0.95,
            max_consecutive_hits=2, temporal_guard=cache_mode == "safe",
        )
        if custom is not None:
            graph[Node.CACHE]["inputs"].update(custom)
        model_source = out(Node.CACHE)
    if style_adapter != "none":
        lora_name, strength = COMBAT_LORAS[0] if style_adapter == "combat" else STYLE_ADAPTERS[style_adapter]
        graph[Node.LORA] = node(
            "LoraLoaderModelOnly", "Character Swap LoRA" if style_adapter.startswith("character_swap") else "Style LoRA",
            model=model_source, lora_name=lora_name, strength_model=strength,
        )
        model_source = out(Node.LORA)
        if style_adapter == "combat":
            lora_name, strength = COMBAT_LORAS[1]
            graph[Node.LORA_SECOND] = node(
                "LoraLoaderModelOnly", "Combat spatial physics LoRA", model=model_source,
                lora_name=lora_name, strength_model=strength,
            )
            model_source = out(Node.LORA_SECOND)
    return model_source
