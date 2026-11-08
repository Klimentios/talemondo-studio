"""Scene colour continuity: match a clip's colours to a reference picture.

Used with «С последнего кадра»: the previous clip's last frame (role «Кадр сцены» or «Окружение»)
is the colour reference, so cuts of one scene do not jump in white balance or contrast.

Implementation (2026-10-05): our clip-wide H3ColorGrade (app/comfy_nodes/h3_color_grade/grade.py).
The GPU API measures the reference picture's CIE Lab mean/std with exactly the statistics Studio
uses for the «Цвет» preset (preview/h3-director/photo-analyze.mjs labStatsFromPixels on a picture
scaled to ≤256 px), and H3ColorGrade applies ONE Reinhard transfer to every frame — no per-frame
flicker. KJNodes ColorMatchV2 (per-frame transfer, flicker risk) stays installed as the A/B variant
``ab_color_match=colormatch_v2`` (app/ab_variant.py). The Studio setting is unchanged:
{picture, strength, method}; ``method`` is used only by ColorMatchV2.
"""
from __future__ import annotations

import math
import subprocess

NODE = "H3ColorGrade"  # app/comfy_nodes/h3_color_grade (overlay installer)
LEGACY_NODE = "ColorMatchV2"  # kijai/ComfyUI-KJNodes, pinned by scripts/install_h3_h200_overlay.sh (A/B only)
IMPLEMENTATIONS = {"h3colorgrade": NODE, "colormatch_v2": LEGACY_NODE}
METHODS = ("mkl", "reinhard", "hm-mkl-hm")  # ColorMatchV2 transfers; mkl is the default
REFERENCE_ROLES = ("Кадр сцены", "Окружение")
STATS_EDGE = 256  # Studio: analyzePhotoFile / loadImagePixels scale the photo to ≤256 px


def normalize_color_match(value: dict | None, image_count: int) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"picture", "strength", "method"} or "picture" not in value:
        raise ValueError("color_match: ожидаются picture, strength, method")
    picture, strength = value["picture"], value.get("strength", 0.6)
    method = value.get("method", "mkl")
    if type(picture) is not int or not 1 <= picture <= image_count:
        raise ValueError("color_match: картинка-образец цвета не прикреплена к заданию")
    if type(strength) not in (int, float) or not math.isfinite(strength) or not 0.0 < strength <= 1.0:
        raise ValueError("color_match: сила от 0 до 1")
    if method not in METHODS:
        raise ValueError("color_match: неизвестный метод")
    return {"picture": picture, "strength": round(float(strength), 3), "method": method}


# --- Lab statistics: identical math to photo-analyze.mjs rgbToLab / labStatsFromPixels -------------

def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lab_f(t: float) -> float:
    return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116


_LINEAR = [_to_linear(value / 255) for value in range(256)]


def rgb_to_lab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """sRGB 0..1 → CIE Lab (D65)."""
    R, G, B = _to_linear(r), _to_linear(g), _to_linear(b)
    return _lab_from_linear(R, G, B)


def _lab_from_linear(R: float, G: float, B: float) -> tuple[float, float, float]:
    x = (0.4124564 * R + 0.3575761 * G + 0.1804375 * B) / 0.95047
    y = 0.2126729 * R + 0.7151522 * G + 0.0721750 * B
    z = (0.0193339 * R + 0.1191920 * G + 0.9503041 * B) / 1.08883
    fx, fy, fz = _lab_f(x), _lab_f(y), _lab_f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def lab_stats_from_rgb(pixels: bytes) -> dict:
    """{target_mean, target_std} of packed RGB24 pixels, rounded and clamped like labStatsFromPixels."""
    count = len(pixels) // 3
    if not count:
        raise ValueError("color_match: образец цвета пуст")
    total = [0.0, 0.0, 0.0]
    squares = [0.0, 0.0, 0.0]
    cache: dict[int, tuple[float, float, float]] = {}
    for offset in range(0, count * 3, 3):
        key = pixels[offset] << 16 | pixels[offset + 1] << 8 | pixels[offset + 2]
        lab = cache.get(key)
        if lab is None:
            lab = cache[key] = _lab_from_linear(_LINEAR[pixels[offset]], _LINEAR[pixels[offset + 1]],
                                                _LINEAR[pixels[offset + 2]])
        for channel in range(3):
            total[channel] += lab[channel]
            squares[channel] += lab[channel] * lab[channel]
    mean = [value / count for value in total]
    std = [math.sqrt(max(1e-6, squares[c] / count - mean[c] * mean[c])) for c in range(3)]
    rounded = lambda value: round(value * 100) / 100  # noqa: E731 - Math.round(v * 100) / 100
    return {
        "target_mean": [rounded(min(100, max(0, mean[0]))), rounded(min(127, max(-128, mean[1]))),
                        rounded(min(127, max(-128, mean[2])))],
        "target_std": [rounded(min(100, std[0])), rounded(min(128, std[1])), rounded(min(128, std[2]))],
    }


def lab_stats_from_image(data: bytes) -> dict:
    """Lab mean/std of a PNG/JPEG/WebP scaled to ≤256 px (never upscaled), as Studio measures photos."""
    edge = STATS_EDGE
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", "pipe:0", "-frames:v", "1",
               "-vf", f"scale='min({edge},iw)':'min({edge},ih)':force_original_aspect_ratio=decrease:flags=bilinear",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    try:
        result = subprocess.run(command, input=data, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"color_match: не удалось прочитать образец цвета: {exc}") from exc
    if result.returncode != 0 or not result.stdout:
        raise ValueError("color_match: не удалось прочитать образец цвета: "
                         + result.stderr.decode(errors="replace")[-200:])
    return lab_stats_from_rgb(result.stdout)


# --- node inputs -------------------------------------------------------------------------------

def color_match_grade_inputs(color_match: dict) -> dict:
    """H3ColorGrade inputs: the measured reference statistics at the Studio strength."""
    if "target_mean" not in color_match or "target_std" not in color_match:
        raise ValueError("color_match: нет Lab-статистики образца (GPU API измеряет её до графа)")
    mean, std = color_match["target_mean"], color_match["target_std"]
    return {"target_l_mean": mean[0], "target_a_mean": mean[1], "target_b_mean": mean[2],
            "target_l_std": std[0], "target_a_std": std[1], "target_b_std": std[2],
            "strength": color_match["strength"], "preserve_luminance": False}


def color_match_node_inputs(color_match: dict, frames: list, reference: list) -> dict:
    """ColorMatchV2 inputs (A/B variant colormatch_v2)."""
    return {"image_target": frames, "image_ref": reference, "method": color_match["method"],
            "strength": color_match["strength"], "multithread": True}
