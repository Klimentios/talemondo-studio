"""Clip-wide color transfer toward a reference photo (Reinhard, CIE Lab).

Statistics are taken over the WHOLE clip, so every frame gets the same
correction (no flicker). Frames are processed in chunks to bound memory.
Pure torch; no extra dependencies on the GPU server.
"""
from __future__ import annotations

import torch

_D65 = (0.95047, 1.0, 1.08883)
_RGB2XYZ = torch.tensor([[0.4124564, 0.3575761, 0.1804375],
                         [0.2126729, 0.7151522, 0.0721750],
                         [0.0193339, 0.1191920, 0.9503041]])
_XYZ2RGB = torch.linalg.inv(_RGB2XYZ)


def _srgb_to_linear(x: torch.Tensor) -> torch.Tensor:
    return torch.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(x: torch.Tensor) -> torch.Tensor:
    x = x.clamp(min=0.0)
    return torch.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)


def rgb_to_lab(rgb: torch.Tensor) -> torch.Tensor:
    """[..., 3] sRGB in 0..1 → Lab (L 0..100)."""
    xyz = _srgb_to_linear(rgb.clamp(0, 1)) @ _RGB2XYZ.to(rgb).T
    xyz = xyz / torch.tensor(_D65, dtype=rgb.dtype, device=rgb.device)
    eps = 216 / 24389
    f = torch.where(xyz > eps, xyz.clamp(min=1e-12) ** (1 / 3), (24389 / 27 * xyz + 16) / 116)
    return torch.stack((116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])), -1)


def lab_to_rgb(lab: torch.Tensor) -> torch.Tensor:
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    eps = 216 / 24389
    kappa = 24389 / 27
    def inv(f):
        return torch.where(f ** 3 > eps, f ** 3, (116 * f - 16) / kappa)
    xyz = torch.stack((inv(fx), inv(fy), inv(fz)), -1) * torch.tensor(_D65, dtype=lab.dtype, device=lab.device)
    return _linear_to_srgb(xyz @ _XYZ2RGB.to(lab).T).clamp(0, 1)


def clip_lab_stats(images: torch.Tensor, chunk: int = 16) -> tuple[torch.Tensor, torch.Tensor]:
    total = torch.zeros(3, dtype=torch.float64)
    total_sq = torch.zeros(3, dtype=torch.float64)
    count = 0
    for start in range(0, images.shape[0], chunk):
        lab = rgb_to_lab(images[start:start + chunk, ..., :3].float()).reshape(-1, 3).double().cpu()
        total += lab.sum(0)
        total_sq += (lab * lab).sum(0)
        count += lab.shape[0]
    mean = total / count
    std = (total_sq / count - mean * mean).clamp(min=1e-6).sqrt()
    return mean.float(), std.float()


def grade_clip(images: torch.Tensor, target_mean, target_std, strength: float,
               preserve_luminance: bool = False, chunk: int = 16) -> torch.Tensor:
    """images: [frames, H, W, C] float 0..1 (ComfyUI IMAGE). Returns the same shape."""
    if strength <= 0:
        return images
    src_mean, src_std = clip_lab_stats(images, chunk)
    tgt_mean = torch.tensor(target_mean, dtype=torch.float32)
    tgt_std = torch.tensor(target_std, dtype=torch.float32).clamp(min=1e-3)
    # Limit how far contrast can be stretched: protects skin and noise in flat shots.
    scale = (tgt_std / src_std).clamp(0.5, 2.0)
    out = torch.empty_like(images)
    for start in range(0, images.shape[0], chunk):
        frames = images[start:start + chunk]
        lab = rgb_to_lab(frames[..., :3].float())
        dev = lab.device
        moved = (lab - src_mean.to(dev)) * scale.to(dev) + tgt_mean.to(dev)
        if preserve_luminance:
            moved[..., 0] = lab[..., 0]
        mixed = lab + (moved - lab) * float(strength)
        rgb = lab_to_rgb(mixed).to(frames.dtype)
        out[start:start + chunk, ..., :3] = rgb
        if frames.shape[-1] > 3:
            out[start:start + chunk, ..., 3:] = frames[..., 3:]
    return out
