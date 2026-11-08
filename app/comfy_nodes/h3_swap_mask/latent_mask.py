"""Mask and frame math for the masked hero swap (torch only, importable without ComfyUI).

H3 latent video [B, 24, T, H/16, W/16]: latent frame k covers FRAME_PER_TOKEN[k % 5] pixel frames
(1, 4, 4, 4, 4 — comfy/ldm/minimax/model.py) and the DiT patches 2x2 latent cells, one row per 32x32 px.
A cell is regenerated when any of its pixels in any of its frames belongs to a replaced performer.
"""
import torch
import torch.nn.functional as F

FRAME_PER_TOKEN = (1, 4, 4, 4, 4)


def frame_spans(latent_t: int) -> list[tuple[int, int]]:
    spans, start = [], 0
    for k in range(latent_t):
        size = FRAME_PER_TOKEN[k % 5]
        spans.append((start, start + size))
        start += size
    return spans


def pixel_frames(latent_t: int) -> int:
    return frame_spans(latent_t)[-1][1]


def fit_frames(images: torch.Tensor, width: int, height: int, length: int) -> torch.Tensor:
    """[F, H, W, C] 0..1 → exactly `length` frames at width x height: centre crop to the aspect, then resize;
    a short source repeats its last frame, a long one is cut."""
    frames = images[:length]
    if frames.shape[0] < length:
        frames = torch.cat([frames, frames[-1:].expand(length - frames.shape[0], *frames.shape[1:])], dim=0)
    _, h, w, _ = frames.shape
    target = width / height
    if w / h > target:
        crop = round(h * target)
        left = (w - crop) // 2
        frames = frames[:, :, left:left + crop]
    elif w / h < target:
        crop = round(w / target)
        top = (h - crop) // 2
        frames = frames[:, top:top + crop]
    frames = frames.permute(0, 3, 1, 2)
    frames = F.interpolate(frames, size=(height, width), mode="bilinear", align_corners=False, antialias=True)
    return frames.permute(0, 2, 3, 1).clamp(0, 1).contiguous()


def crop_box(width: int, height: int, src_w: int, src_h: int) -> tuple[float, float, float, float]:
    """The centre crop fit_frames takes from a src_w x src_h frame, as (x, y, w, h) fractions of it."""
    target, aspect = width / height, src_w / src_h
    if aspect > target:
        w = round(src_h * target) / src_w
        return ((1 - w) / 2, 0.0, w, 1.0)
    if aspect < target:
        h = round(src_w / target) / src_h
        return (0.0, (1 - h) / 2, 1.0, h)
    return (0.0, 0.0, 1.0, 1.0)


def remap_box(box, crop) -> list[float]:
    """A box drawn on the full source frame → the same region on the centre-cropped job frame."""
    x, y, w, h = box
    cx, cy, cw, ch = crop
    left, top = max(0.0, (x - cx) / cw), max(0.0, (y - cy) / ch)
    right, bottom = min(1.0, (x + w - cx) / cw), min(1.0, (y + h - cy) / ch)
    if right - left < 0.01 or bottom - top < 0.01:
        raise ValueError("Рамка исполнителя целиком вне кадра задания после обрезки под формат")
    return [left, top, right - left, bottom - top]


def latent_noise_mask(mask: torch.Tensor, latent_t: int, latent_h: int, latent_w: int) -> torch.Tensor:
    """[F, H, W] pixel mask (1 = regenerate) → [1, 1, T, h, w] latent noise mask on the 32 px DiT grid."""
    if mask.dim() != 3:
        raise ValueError("Маска замены: ожидается [кадры, высота, ширина]")
    frames = pixel_frames(latent_t)
    if mask.shape[0] < frames:
        mask = torch.cat([mask, mask[-1:].expand(frames - mask.shape[0], *mask.shape[1:])], dim=0)
    binary = (mask[:frames] > 0.5).float()
    per_latent = torch.stack([binary[a:b].amax(dim=0) for a, b in frame_spans(latent_t)])  # [T, H, W]
    cells = F.adaptive_max_pool2d(per_latent[:, None], (max(1, latent_h // 2), max(1, latent_w // 2)))
    grid = F.interpolate(cells, size=(latent_h, latent_w), mode="nearest")  # [T, 1, h, w]
    return grid.permute(1, 0, 2, 3)[None]  # [1, 1, T, h, w]
