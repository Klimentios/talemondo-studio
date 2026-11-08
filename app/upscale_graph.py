"""Native ComfyUI SeedVR2 video graph. H3 generation graph is untouched."""
from __future__ import annotations

import os
from pathlib import Path


MODEL = "seedvr2_3b_fp16.safetensors"
VAE = "seedvr2_ema_vae_fp16.safetensors"
# SeedVR2 checkpoints (Comfy-Org/SeedVR2). 3B is the Studio default; 7B fp16 is an A/B option of the
# upscale pass (ab_seedvr2_model=7b), installed only with H3_SEEDVR2_7B=1.
SEEDVR2_MODELS = {"3b": MODEL, "7b": "seedvr2_7b_fp16.safetensors"}


def seedvr2_models_available(root: str | None = None) -> list[str]:
    """SeedVR2 sizes whose checkpoint is on disk (models/diffusion_models under H3_COMFY_ROOT)."""
    base = Path(root or os.environ.get("H3_COMFY_ROOT", "/workspace/h3/ComfyUI"))
    return [name for name, file in SEEDVR2_MODELS.items() if (base / "models/diffusion_models" / file).is_file()]
# RIFE 4.17 (Fannovel16/ComfyUI-Frame-Interpolation, pinned by scripts/install_h3_h200_overlay.sh).
RIFE_CKPT = "rife417.pth"
RIFE_CKPT_PATH = "custom_nodes/ComfyUI-Frame-Interpolation/ckpts/rife/rife417.pth"  # under ComfyUI/
# Finished-clip passes: SeedVR2 2x, RIFE 2x frame rate (24 -> 48 fps), or both. RIFE only takes
# integer multipliers, so 60 fps from 24 would need 120 fps and frame dropping — not offered.
MODES = {"upscale": "SeedVR2 ×2", "smooth": "RIFE 48 fps", "upscale_smooth": "SeedVR2 ×2 + RIFE 48 fps"}
_SEEDVR2_NODES = {"LoadVideo", "GetVideoComponents", "ResizeImageMaskNode", "SeedVR2Preprocess",
                  "VAEEncodeTiled", "VAELoader", "UNETLoader", "SeedVR2Conditioning", "KSampler",
                  "SeedVR2TemporalChunk", "SeedVR2TemporalMerge", "VAEDecodeTiled",
                  "SeedVR2PostProcessing", "CreateVideo", "SaveVideo"}


def required_nodes(mode: str = "upscale") -> set[str]:
    if mode == "smooth":
        return {"LoadVideo", "GetVideoComponents", "RIFE VFI", "CreateVideo", "SaveVideo"}
    return _SEEDVR2_NODES | ({"RIFE VFI"} if mode == "upscale_smooth" else set())


def required_weights(mode: str = "upscale", model: str = "3b") -> list[tuple[str, str]]:
    """(models-relative or ComfyUI-relative path, kind) the pass needs on disk."""
    weights = [] if mode == "smooth" else [(f"models/diffusion_models/{SEEDVR2_MODELS[model]}", "SeedVR2"),
                                           (f"models/vae/{VAE}", "SeedVR2 VAE")]
    return weights + ([(RIFE_CKPT_PATH, "RIFE 4.17")] if mode in {"smooth", "upscale_smooth"} else [])


def _rife(frames: list) -> dict:
    # Quality settings: no fast mode, ensemble on, full precision; the upscaled clip stays untouched.
    return {"class_type": "RIFE VFI", "inputs": {
        "ckpt_name": RIFE_CKPT, "frames": frames, "clear_cache_after_n_frames": 10, "multiplier": 2,
        "fast_mode": False, "ensemble": True, "scale_factor": 1.0, "dtype": "float32",
        "torch_compile": False, "batch_size": 1}}


def build_upscale_graph(source_name: str, job_id: str, fps: float, scale: float = 2.0, mode: str = "upscale",
                        model: str = "3b") -> dict:
    if not 0 < fps <= 120 or not 1 < scale <= 4:
        raise ValueError("Недопустимая частота кадров или масштаб")
    if mode not in MODES:
        raise ValueError("Неизвестный режим улучшения ролика")
    if model not in SEEDVR2_MODELS:
        raise ValueError("Неизвестная модель SeedVR2")
    if mode != "upscale" and fps * 2 > 120:
        raise ValueError("Плавность ×2 поддерживается до 60 кадров/с исходника")
    def node(kind: str, **inputs: object) -> dict:
        return {"class_type": kind, "inputs": inputs}
    if mode == "smooth":
        return {
            "1": node("LoadVideo", file=source_name),
            "2": node("GetVideoComponents", video=["1", 0]),
            "20": _rife(["2", 0]),
            "12": node("CreateVideo", images=["20", 0], audio=["2", 1], fps=fps * 2,
                       bit_depth=8, color_space="sRGB", codec="none"),
            "13": node("SaveVideo", video=["12", 0], filename_prefix=f"rife/{job_id}",
                       format="auto", **{"format.codec": "auto"}, codec="auto"),
        }
    graph = {
        "1": node("LoadVideo", file=source_name),
        "2": node("GetVideoComponents", video=["1", 0]),
        # resize_type is a dynamic combo: its sub-inputs are namespaced, like "resize_type.longer_size"
        # in graph.py. A bare `multiplier` fails ComfyUI validation ("Required input is missing").
        "3": node("ResizeImageMaskNode", input=["2", 0], resize_type="scale by multiplier",
                  **{"resize_type.multiplier": scale}, scale_method="lanczos"),
        "4": node("SeedVR2Preprocess", resized_images=["3", 0]),
        "5": node("VAELoader", vae_name=VAE),
        "6": node("VAEEncodeTiled", pixels=["4", 0], vae=["5", 0], tile_size=512,
                  overlap=128, temporal_size=64, temporal_overlap=8),
        "14": node("SeedVR2TemporalChunk", latent=["6", 0], temporal_overlap=4,
                   chunking_mode="auto"),
        "7": node("UNETLoader", unet_name=SEEDVR2_MODELS[model], weight_dtype="default"),
        "8": node("SeedVR2Conditioning", model=["7", 0], vae_conditioning=["14", 0]),
        "9": node("KSampler", model=["7", 0], positive=["8", 0], negative=["8", 1],
                  latent_image=["14", 0], seed=0, steps=1, cfg=1.0,
                  sampler_name="euler", scheduler="simple", denoise=1.0),
        "15": node("SeedVR2TemporalMerge", latents=["9", 0], temporal_overlap=["14", 1]),
        "10": node("VAEDecodeTiled", samples=["15", 0], vae=["5", 0], tile_size=512,
                   overlap=128, temporal_size=64, temporal_overlap=8),
        "11": node("SeedVR2PostProcessing", images=["10", 0],
                   original_resized_images=["3", 0], color_correction_method="lab"),
        "12": node("CreateVideo", images=["11", 0], audio=["2", 1], fps=fps,
                   bit_depth=8, color_space="sRGB", codec="none"),
        "13": node("SaveVideo", video=["12", 0], filename_prefix=f"seedvr2/{job_id}",
                   format="auto", **{"format.codec": "auto"}, codec="auto"),
    }
    if mode == "upscale_smooth":
        graph["20"] = _rife(["11", 0])
        graph["12"]["inputs"].update(images=["20", 0], fps=fps * 2)
    return graph
