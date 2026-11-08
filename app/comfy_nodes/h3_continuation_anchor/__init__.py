"""«Продолжение», A/B anchor (installed by scripts/install_h3_h200_overlay.sh into custom_nodes/h3-continuation-anchor).

Our continuation anchors the previous clip's last frames as a MiniMaxH3AddGuide keyframe: the model sees
them as a guide and redraws them, so the new clip may not start exactly where the old one ended.
ComfyUI-H3-LongTake (MIT) measured that jump at joins: 5.4–6.8x the normal frame-to-frame change with a
keyframe anchor, up to 19x with 22 context frames, 1.5–1.7x when the frames are copied into the latent and
protected by the denoise mask ("inpaint" anchor). H3ContinuationInpaint is that inpaint anchor, built from
the decoded tail of the previous MP4 (our continuation has the video, not its latent): the frames and their
sound are VAE-encoded, written over the head of the empty AV latent, and masked out of denoising.
A/B only (app/ab_variant.py ab_anchor) until a GPU render decides.
"""
import torch

import comfy.nested_tensor
from comfy.ldm.minimax.model import FRAME_PER_TOKEN, FRAME_RESCALE
from comfy_extras.nodes_minimax_h3 import _encode_ref_audio, _resize


def latent_steps(frames: int) -> int | None:
    """Latent tokens that cover exactly `frames` pixel frames (1/4/4/4/4 cycle); None if none does."""
    steps = covered = 0
    while covered < frames:
        covered += FRAME_PER_TOKEN[steps % 5]
        steps += 1
    return steps if covered == frames else None


class H3ContinuationInpaint:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
                    "latent": ("LATENT",), "vae": ("VAE",), "image": ("IMAGE",)},
                "optional": {"audio_vae": ("VAE",), "audio": ("AUDIO",)}}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "anchor"
    CATEGORY = "STUDIO/H3"
    DESCRIPTION = ("Continuation by inpaint: the previous clip's last frames (and sound) are copied into the head "
                   "of the H3 AV latent and protected by the denoise mask instead of travelling as a keyframe.")

    def anchor(self, latent, vae, image, audio_vae=None, audio=None):
        samples = latent["samples"]
        if not getattr(samples, "is_nested", False) or len(samples.tensors) != 2 or samples.tensors[0].shape[1] != 24:
            raise ValueError("H3ContinuationInpaint expects a MiniMax H3 AV latent")
        video, sound = (tensor.clone() for tensor in samples.tensors)
        frames = int(image.shape[0])
        steps = latent_steps(frames)
        if steps is None or steps >= video.shape[2]:
            raise ValueError(f"H3ContinuationInpaint: {frames} frames do not fit the latent grid (5, 22, 39…)")
        height, width = video.shape[3] * 16, video.shape[4] * 16
        encoded = vae.encode(_resize(image, width, height, "center"))
        if encoded.ndim == 4:
            encoded = encoded.unsqueeze(0)
        # The VAE pads a short clip to its chunk length; the first `steps` tokens are the real frames.
        if encoded.shape[2] < steps:
            raise ValueError(f"H3ContinuationInpaint: VAE gave {encoded.shape[2]} tokens for {frames} frames, expected {steps}")
        video[:, :, :steps] = encoded[:1, :, :steps].to(video)
        video_mask = torch.ones(1, 1, *video.shape[2:], dtype=torch.float32)
        video_mask[:, :, :steps] = 0.0
        sound_mask = torch.ones(1, 1, *sound.shape[2:], dtype=torch.float32)
        if audio is not None and audio_vae is not None:
            encoded_audio, length = _encode_ref_audio(audio_vae, audio)
            length = min(length, int(FRAME_RESCALE * frames), sound.shape[-1])
            sound[..., :length] = encoded_audio[:1, ..., :length].to(sound)
            sound_mask[..., :length] = 0.0
        return ({**latent, "samples": comfy.nested_tensor.NestedTensor((video, sound)),
                 "noise_mask": comfy.nested_tensor.NestedTensor((video_mask, sound_mask))},)


NODE_CLASS_MAPPINGS = {"H3ContinuationInpaint": H3ContinuationInpaint}
NODE_DISPLAY_NAME_MAPPINGS = {"H3ContinuationInpaint": "H3 Continuation (inpaint anchor)"}
