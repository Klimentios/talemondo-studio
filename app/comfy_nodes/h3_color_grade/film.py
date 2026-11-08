"""Film look for the decoded H3 clip: tone contrast (S-curve) and moving film grain.

Driven by the Studio «Камера» preset (носитель плёнка 35/16 мм, винтаж, зерно, контраст).
Grain is regenerated every frame (like real film) from a fixed seed, so a re-render is identical.
"""
from __future__ import annotations

import torch
import torch.nn.functional as F


def s_curve(x: torch.Tensor, amount: float) -> torch.Tensor:
    """amount > 0 adds contrast, < 0 flattens; 0 = identity. Midtones stay put."""
    if amount == 0:
        return x
    smooth = x * x * (3 - 2 * x)
    return (x + (smooth - x) * (2 * amount)).clamp(0, 1)


def film_look(images: torch.Tensor, contrast: float, grain: float, grain_size: float,
              seed: int, chunk: int = 16) -> torch.Tensor:
    """images: [frames, H, W, C] 0..1 → same shape."""
    if contrast == 0 and grain == 0:
        return images
    frames, height, width, _ = images.shape
    out = torch.empty_like(images)
    generator = torch.Generator(device="cpu")
    gh, gw = max(1, round(height / grain_size)), max(1, round(width / grain_size))
    for start in range(0, frames, chunk):
        block = images[start:start + chunk].float()
        rgb = s_curve(block[..., :3], contrast)
        if grain > 0:
            noises = []
            for index in range(start, min(frames, start + chunk)):
                generator.manual_seed(int(seed) * 100003 + index)
                noises.append(torch.randn(1, 1, gh, gw, generator=generator))
            noise = torch.cat(noises).to(rgb.device)
            if (gh, gw) != (height, width):
                noise = F.interpolate(noise, size=(height, width), mode="bilinear", align_corners=False)
            noise = noise.permute(0, 2, 3, 1)  # [n, H, W, 1]
            luma = (0.2126 * rgb[..., 0:1] + 0.7152 * rgb[..., 1:2] + 0.0722 * rgb[..., 2:3])
            weight = (4 * luma * (1 - luma)).clamp(0.15, 1)  # strongest in midtones, like film
            rgb = (rgb + noise * weight * (0.055 * grain)).clamp(0, 1)
        out[start:start + chunk, ..., :3] = rgb.to(images.dtype)
        if images.shape[-1] > 3:
            out[start:start + chunk, ..., 3:] = images[start:start + chunk, ..., 3:]
    return out
