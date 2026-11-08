"""Official MiniMax H3 prompt embeddings (camera / visual effects) — job setting ``camera_effect``.

Source: huggingface.co/Comfy-Org/MiniMax-H3, folder embeddings/ (the workflow template calls them
"style embeddings", ``minimaxh3_*.safetensors``), pinned at revision REVISION. ComfyUI supports them
since ComfyUI#15697: put the file into models/embeddings/ and write ``embedding:<file name without
extension>`` in the prompt. At our pin (b5cc8830, comfy/sd1_clip.py) the token must start the prompt or
follow whitespace: the tokenizer splits on ``(?<=\\s)embedding:``.

Weights are installed by default after approval on 2026-10-05; H3_CAMERA_EFFECTS=0 omits them.
"""
from __future__ import annotations

from .h3_ir import is_ir_prompt

REPO = "Comfy-Org/MiniMax-H3"
REVISION = "e5eb578a89295337b8ff433a035929ce0279e0b6"
# setting value -> (embedding name, size in bytes, sha256) from the HF API at REVISION.
EFFECTS = {
    "art_is_explosion": ("minimaxh3_art_is_explosion", 512120, "00e8679c77ce1fc3fd3accb67bc4db56d626b6b27609797504ab00e2b997145f"),
    "blooming_flowers": ("minimaxh3_blooming_flowers", 1259640, "934d1d2c50bf27cd0b3785b0f44b0b62119a90b2901f9193782d86ff7191cbe9"),
    "bullet_time": ("minimaxh3_bullet_time", 962680, "e318b200d28c22fcf41d9b25ce481dd5a766f0ac50561663e33401975ea1bcae"),
    "dark_magic": ("minimaxh3_dark_magic", 604280, "71df9fe76f9cbb34a7508fc49d46bf35ee51744aaa17789c6406e1bec49f5866"),
    "fire_breath": ("minimaxh3_fire_breath", 1208440, "ed048cf52802774a819e916bbf6ad6eaa047027abc6cd0decc63daccbb93c9f1"),
    "four_seasons": ("minimaxh3_four_seasons", 1454200, "9a432e89afc942d1ea27055372a6fb81c4dc0f4c0498a49b9f5eff1a015f1034"),
    "kiss_camera": ("minimaxh3_kiss_camera", 993400, "9da82f56c26f2a81b80803a31486d31036c122112203b62d4991759a90ddf4b8"),
    "spiral_ascent": ("minimaxh3_spiral_ascent", 1341560, "767b6bdccee4c41805802a07bf3c04ba9582fbb8e7bc4bd416ff8e000e3e29b9"),
    "storm_magic": ("minimaxh3_storm_magic", 1403000, "5047cd4499207ef20d62872c7153a476e216e7edabd12b595802f567666d7425"),
    "truman_show": ("minimaxh3_truman_show", 921720, "3830e6ab9d7b95965f5497a6691bd92e33f8bf7a62bafb7b2331b268ff98aa00"),
}


def normalize_camera_effect(value: object) -> str | None:
    """Allow-listed effect name, None for absent/empty/"none"."""
    if value in (None, "", "none"):
        return None
    if not isinstance(value, str) or value not in EFFECTS:
        raise ValueError(f"camera_effect: неизвестный эффект; доступны: {', '.join(EFFECTS)}")
    return value


def embedding_name(effect: str) -> str:
    return EFFECTS[effect][0]


def installed_effects(embeddings: list[str] | None) -> list[str]:
    """Effects whose embedding ComfyUI lists (GET /embeddings returns names without extension)."""
    present = set(embeddings or [])
    return [effect for effect, (name, _, _) in EFFECTS.items() if name in present]


def with_camera_effect(prompt: str, effect: str | None) -> str:
    """Insert ``embedding:<name>`` per the official usage. A six-section prompt must keep opening with
    ``subject_definitions:``, so the token opens detailed_description (like the style LoRA trigger)."""
    if not effect:
        return prompt
    token = f"embedding:{embedding_name(effect)}"
    if token in prompt:
        return prompt
    if is_ir_prompt(prompt) and "detailed_description:\n" in prompt:
        return prompt.replace("detailed_description:\n", f"detailed_description:\n{token} ", 1)
    return f"{token} {prompt}"
