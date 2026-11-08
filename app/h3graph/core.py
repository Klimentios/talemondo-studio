"""Model weights, sampler, decode and output: the skeleton every H3 Ref2VA job shares."""
from __future__ import annotations

import secrets

from ..contract import GenerationRequest
from ..sampling import sampling_inputs
from .nodes import Node, node, out

from ..render_profiles import PROFILES
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
FPS = 24.0


def skeleton(request: GenerationRequest, sampling_profile: str) -> dict[str, dict[str, object]]:
    """Loaders → sampler → decode → MP4. The Ref2VA conditioning node and the model chain are wired later."""
    sampler_name, scheduler_name = sampling_inputs(sampling_profile)
    profile = PROFILES[request.profile]
    return {
        Node.SAVE: node(
            "SaveVideo",
            "Сохранить видео",
            filename_prefix="video/MiniMax_H3_Studio",
            format="auto",
            **{"format.codec": "auto"},
            codec="auto",
            video=out(Node.MUX),
        ),
        Node.VIDEO_VAE: node("VAELoader", "Видео VAE", vae_name=VIDEO_VAE),
        Node.AUDIO_VAE: node("VAELoader", "Аудио VAE", vae_name=AUDIO_VAE),
        Node.DECODE_AUDIO: node("VAEDecodeAudio", "Декодировать аудио", samples=out(Node.SAMPLE), vae=out(Node.AUDIO_VAE)),
        Node.DECODE_VIDEO: node("VAEDecode", "Декодировать видео", samples=out(Node.SAMPLE), vae=out(Node.VIDEO_VAE)),
        Node.SAMPLER: node("KSamplerSelect", "Сэмплер", sampler_name=sampler_name),
        Node.SCHEDULER: node(
            "BasicScheduler",
            f"Base — {request.steps} шагов",
            scheduler=scheduler_name,
            steps=request.steps,
            denoise=1.0,
            model=out(Node.MODEL),
        ),
        Node.SAMPLE: node(
            "SamplerCustomAdvanced",
            "Генерация",
            noise=out(Node.NOISE),
            guider=out(Node.GUIDER),
            sampler=out(Node.SAMPLER),
            sigmas=out(Node.SCHEDULER),
            latent_image=out(Node.REF2VA, 1),
        ),
        Node.GUIDER: node("BasicGuider", "Гид", model=out(Node.MODEL), conditioning=out(Node.REF2VA)),
        Node.MODEL: node(
            "UNETLoader",
            "Полная MiniMax H3 Ref2VA" if request.profile == "bf16" else f"H3 эксперимент: {request.profile}",
            unet_name=profile["unet"],
            weight_dtype="default",
        ),
        Node.TEXT_ENCODER: node(
            "CLIPLoader",
            "Полный текстовый энкодер",
            clip_name=profile["clip"],
            type="minimax",
            device="default",
        ),
        Node.NOISE: node(
            "RandomNoise",
            "Seed",
            noise_seed=request.seed if request.seed is not None else secrets.randbelow(2**53),
        ),
        Node.MUX: node(
            "CreateVideo",
            "Собрать MP4",
            fps=FPS,
            bit_depth=8,
            color_space="sRGB",
            codec="none",
            images=out(Node.DECODE_VIDEO),
            audio=out(Node.DECODE_AUDIO),
        ),
    }


def wire_model(graph: dict, model_source: list) -> None:
    graph[Node.SCHEDULER]["inputs"]["model"] = model_source
    graph[Node.GUIDER]["inputs"]["model"] = model_source


def wire_conditioning(graph: dict, conditioning: list) -> None:
    graph[Node.GUIDER]["inputs"]["conditioning"] = conditioning


def wire_frames(graph: dict, frames: list) -> None:
    graph[Node.MUX]["inputs"]["images"] = frames
