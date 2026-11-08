"""Scenarist / Director model profiles (H3_SCENARIST_PROFILE).

One local vLLM serves both the Director (JSON plan) and the Scenarist (six-section IR). The
profile picks the model, its vLLM environment and the request extras. "default" is the proven
Qwen3-VL-8B on vLLM 0.15.0 and adds nothing to any request: tests/golden stays byte-identical.

The bash launcher scripts/run_h3_qwen_director.sh carries the same table for the vLLM command
line (tests/test_scenarist_profile.py checks that both agree).
"""
from __future__ import annotations

import copy
import os

DEFAULT_PROFILE = "default"

# Qwen3.5-family hybrid models (Gated DeltaNet + gated attention) think by default. Thinking is
# switched off per request; the server also sets it as its default (--default-chat-template-kwargs),
# but vLLM's qwen3 reasoning parser reads the *request* kwargs, so both must say false.
_NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}
# Model-card non-thinking sampling (top_p 0.8, top_k 20). The caller keeps its own temperature
# (Director 0 for strict JSON, Scenarist 0.4). presence_penalty 1.5 from the card is left out on
# purpose: it penalises the repeated <Subject N>/<Picture N> tags the IR format requires.
_NEXT_SCENARIST = {**_NO_THINKING, "top_p": 0.8, "top_k": 20}

PROFILES: dict[str, dict] = {
    DEFAULT_PROFILE: {
        "model": "Qwen/Qwen3-VL-8B-Instruct", "revision": None, "vllm": "0.15.0", "venv": "qwen-venv",
        "license": "Apache-2.0", "gpu_memory_utilization": 0.30, "max_model_len": 32768,
        # One model on the GPU at a time (user, 2026-10-08): even the small planner wakes only with an
        # idle ComfyUI queue and H3 unloaded, re-checked under the planning lock (no wake beside a render).
        "exclusive_gpu": True, "director_extras": {}, "scenarist_extras": {},
    },
    "qwen3.8-27b-fp8": {
        "model": "Qwen/Qwen3.8-27B-FP8", "revision": "017b9c7af6b5689d5dd426a76e0bc077eb5ca20a",
        "vllm": "0.19.1", "venv": "qwen-next-venv", "license": "Apache-2.0",
        "gpu_memory_utilization": 0.45, "max_model_len": 32768,
        # ~31 GB FP8 weights: wakes only with an idle ComfyUI queue and H3 unloaded.
        "exclusive_gpu": True, "director_extras": _NO_THINKING, "scenarist_extras": _NEXT_SCENARIST,
    },
    "qwen3.6-27b-fp8": {
        "model": "Qwen/Qwen3.6-27B-FP8", "revision": "e89b16ebf1988b3d6befa7de50abc2d76f26eb09",
        "vllm": "0.19.1", "venv": "qwen-next-venv", "license": "Apache-2.0",
        "gpu_memory_utilization": 0.45, "max_model_len": 32768,
        "exclusive_gpu": True, "director_extras": _NO_THINKING, "scenarist_extras": _NEXT_SCENARIST,
    },
}


def active_name() -> str:
    return (os.environ.get("H3_SCENARIST_PROFILE") or DEFAULT_PROFILE).strip().lower()


def active_profile(name: str | None = None) -> dict:
    name = (name or active_name()).strip().lower()
    if name not in PROFILES:
        raise ValueError(f"Неизвестный профиль сценариста H3_SCENARIST_PROFILE={name!r}; "
                         f"доступны: {', '.join(PROFILES)}")
    return {"name": name, **copy.deepcopy(PROFILES[name])}


def sleep_level() -> int:
    """vLLM sleep level after a request. 1 keeps weights in host RAM (default, ~0.3 s wake);
    2 discards them and reloads from disk on wake (fallback for hybrid-model sleep bugs)."""
    level = (os.environ.get("H3_QWEN_SLEEP_LEVEL") or "1").strip()
    if level not in {"1", "2"}:
        raise ValueError(f"H3_QWEN_SLEEP_LEVEL={level!r}: допустимо 1 или 2")
    return int(level)


def capability() -> dict:
    """Additive /api/capabilities entry: which planner model answers and whether it may share
    the GPU with an H3 render."""
    try:
        profile = active_profile()
    except ValueError as exc:
        return {"profile": active_name(), "model": None, "error": str(exc)}
    model = os.environ.get("H3_DIRECTOR_MODEL") or profile["model"]
    exclusive = bool(profile["exclusive_gpu"])
    hint = ("Модель сценариста просыпается только при пустой очереди ComfyUI и выгруженной H3; "
            "во время рендера сценарист отвечает 409 и ждёт." if exclusive else "")
    try:
        level = sleep_level()
    except ValueError as exc:
        level, hint = None, str(exc)
    return {"profile": profile["name"], "model": model, "revision": profile["revision"],
            "vllm": profile["vllm"], "gpu_memory_utilization": profile["gpu_memory_utilization"],
            "max_model_len": profile["max_model_len"], "exclusive_gpu": exclusive,
            "coexists_with_render": not exclusive, "sleep_level": level,
            "thinking": False if profile["director_extras"] else None, "hint": hint}
