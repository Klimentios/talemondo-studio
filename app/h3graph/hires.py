"""Two-pass H3 hires: pass 1 at half size, latent ×2, pass 2 on the tail of the same schedule.

The community recipe (Ref2vaMulti, ViralFamilyShorts, SEEDHUNTER; docs/H3_MASTER_PLAN_2026-10-07.md):
pass 1 runs every step (>= 24, no turbo) at a quarter of the pixels, MiniMaxH3LatentUpscaleCombined
doubles the joint AV latent and re-noises the video at the first tail sigma, pass 2 re-runs only the low
sigmas (SplitSigmasDenoise) at full size. Pass-1 audio is locked (audio_denoise 0). The Combined node also
scales the reference and keyframe latents inside the conditioning, so scene guides and continuation keep
their place. Node pack: ComfyUI-MiniMaxH3-LatentUpscaler (installed on the server image).

A/B only until the user approves it on a GPU render: app/ab_variant.py ``ab_hires``.
"""
from __future__ import annotations

from .nodes import Node, node, out

UPSCALE_NODE = "MiniMaxH3LatentUpscaleCombined"
REQUIRED_NODES = frozenset({"SplitSigmasDenoise", UPSCALE_NODE, "BasicGuider", "SamplerCustomAdvanced"})
# The pack's default learned checkpoint name; it must be passed even when an interpolation method is used.
LEARNED_CHECKPOINT = "h3_clean_latent_upscaler_film_epoch200.safetensors"
METHODS = {"learned": "learned model", "bislerp": "bislerp"}
DEFAULT_TAIL = 0.25
TAIL_RANGE = (0.1, 0.5)

SPLIT = "360"
UPSCALE = "361"
GUIDER = "362"
SAMPLE = "363"


def pass_one_size(width: int, height: int) -> tuple[int, int]:
    """Pass-1 canvas: exactly half of the final size, still on H3's 32-pixel grid."""
    if width % 64 or height % 64:
        raise ValueError(f"Hires ×2: итоговый размер {width}×{height} должен делиться на 64 "
                         f"(первый проход — ровно половина, кратная 32)")
    return width // 2, height // 2


def apply_hires(graph: dict, *, method: str = "bislerp", tail: float = DEFAULT_TAIL) -> dict:
    """Turns a built one-pass graph into the two-pass one, in place; returns it."""
    if method not in METHODS:
        raise ValueError(f"Hires: метод {', '.join(METHODS)}")
    if not TAIL_RANGE[0] <= tail <= TAIL_RANGE[1]:
        raise ValueError(f"Hires: хвост второго прохода {TAIL_RANGE[0]}–{TAIL_RANGE[1]}")
    ref = graph[Node.REF2VA]["inputs"]
    ref["width"], ref["height"] = pass_one_size(ref["width"], ref["height"])
    model = graph[Node.GUIDER]["inputs"]["model"]
    conditioning = graph[Node.GUIDER]["inputs"]["conditioning"]
    graph[SPLIT] = node("SplitSigmasDenoise", f"Hires: хвост {round(tail * 100)} % расписания",
                        sigmas=out(Node.SCHEDULER), denoise=tail)
    graph[UPSCALE] = node(
        UPSCALE_NODE, "Hires: латент ×2",
        samples=out(Node.SAMPLE), method=METHODS[method], learned_model=LEARNED_CHECKPOINT,
        model=model, noise=out(Node.NOISE), sigmas=out(SPLIT, 1),
        audio_denoise=0.0, noise_resample="independent", match_stats=0.0, positive=conditioning,
    )
    graph[GUIDER] = node("BasicGuider", "Hires: гид второго прохода", model=model, conditioning=out(UPSCALE, 1))
    graph[SAMPLE] = node("SamplerCustomAdvanced", "Hires: второй проход",
                         noise=out(Node.NOISE), guider=out(GUIDER), sampler=out(Node.SAMPLER),
                         sigmas=out(SPLIT, 1), latent_image=out(UPSCALE))
    graph[Node.DECODE_VIDEO]["inputs"]["samples"] = out(SAMPLE)
    graph[Node.DECODE_AUDIO]["inputs"]["samples"] = out(SAMPLE)
    return graph
