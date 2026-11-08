"""Packed-sequence budget of one H3 Ref2VA job, mirroring ComfyUI's MiniMax H3 code.

Sources (ComfyUI on the Mac copy, the same code as the GPU server):
comfy_extras/nodes_minimax_h3.py (align_frame_count, video_latent_t, temporal_shape, adapt_canvas,
MiniMaxH3ReferenceToVideo.execute), comfy/ldm/minimax/model.py (PackedLayout: one 2x2-patch row per
32x32 px of a latent frame, two rows per audio latent frame) and comfy/text_encoders/minimax.py
(Qwen3-VL sees each picture once and a reference video at 2 fps in 2-frame blocks).

The DiT attends over the whole packed sequence every step, so cost grows with total_rows squared.
Numbers are estimates for planning and lint, not a guarantee of VRAM or time.
"""
from __future__ import annotations

import math

FPS = 24
AUDIO_LATENT_FPS = 40
BASE_SHORT_EDGE = 768
MAX_PIXELS = 768 * 1344
REF_IMAGE_SHORT_EDGE = 2048
QWEN_MAX_PIXELS = 16777216
TEXT_TOKENS_PER_CHAR = 0.24  # mean 0.237 (0.214–0.256) over 20 Studio prompts of 03–04.11 with the H3 tokenizer


def align_frame_count(n: int) -> int:
    while n % 17 != 5:
        n += 1
    return n


def video_latent_t(frame_count: int) -> int:
    return 2 if frame_count <= 5 else ((frame_count - 5) // 17) * 5 + 2


def _r32(x: float) -> int:
    return max(32, round(x / 32) * 32)


def adapt_canvas(width: int, height: int) -> tuple[int, int]:
    ratio = width / height
    w, h = (BASE_SHORT_EDGE * ratio, BASE_SHORT_EDGE) if ratio >= 1 else (BASE_SHORT_EDGE, BASE_SHORT_EDGE / ratio)
    if w * h > MAX_PIXELS:
        s = math.sqrt(MAX_PIXELS / (w * h))
        w, h = w * s, h * s
    return _r32(w), _r32(h)


def _rows(width: int, height: int) -> int:
    return (height // 32) * (width // 32)


def _qwen_rows(width: int, height: int) -> int:
    h, w = round(height / 32) * 32, round(width / 32) * 32
    if h * w > QWEN_MAX_PIXELS:
        b = math.sqrt(height * width / QWEN_MAX_PIXELS)
        h, w = math.floor(height / b / 32) * 32, math.floor(width / b / 32) * 32
    return (h // 32) * (w // 32)


def reference_image_size(width: int, height: int, mode: str, out_w: int, out_h: int) -> tuple[int, int]:
    scale = min(1.0, math.sqrt(out_w * out_h / (width * height))) if mode == "match" \
        else min(1.0, REF_IMAGE_SHORT_EDGE / min(width, height))
    return _r32(width * scale), _r32(height * scale)


def job_budget(out_w: int, out_h: int, seconds: float, images: list[tuple[int, int]] = (),
               videos: list[tuple[int, int, float]] = (), ref_image_size: str = "max",
               prompt_chars: int = 0) -> dict:
    """images: (w, h) per picture; videos: (w, h, seconds) per reference video, in connection order."""
    frames = align_frame_count(max(5, round(seconds * FPS)))
    target = video_latent_t(frames) * _rows(out_w, out_h) + round(frames / FPS * AUDIO_LATENT_FPS) * 2
    text = round(prompt_chars * TEXT_TOKENS_PER_CHAR)
    picture_rows = 0
    for w, h in images:
        tw, th = reference_image_size(w, h, ref_image_size, out_w, out_h)
        picture_rows += _rows(tw, th)
        text += _qwen_rows(tw, th) + 6
    video_rows = 0
    for vw, vh, vs in videos:
        cw, ch = adapt_canvas(vw, vh)
        if vw * vh < cw * ch:
            cw, ch = _r32(vw), _r32(vh)
        n = min(round(vs * FPS), frames)
        while n % 17 != 5 and n > 5:
            n -= 1
        video_rows += video_latent_t(n) * _rows(cw, ch) + round(n / FPS * AUDIO_LATENT_FPS) * 2
        blocks = math.ceil(len(range(0, n, FPS // 2)) / 2)
        text += blocks * (_qwen_rows(cw, ch) + 6)
    total = text + picture_rows + video_rows + target
    return {"frames": frames, "target_rows": target, "text_rows": text, "picture_rows": picture_rows,
            "video_rows": video_rows, "total_rows": total,
            "attention_vs_target": round((total / target) ** 2, 2)}
