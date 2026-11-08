"""Masked hero swap for H3 Ref2VA (installed by scripts/install_h3_h200_overlay.sh into custom_nodes/h3-swap-mask).

Only the replaced performers are redrawn: the source clip is VAE-encoded into the target latent and the
latent noise mask (1 = regenerate) keeps every other pixel of the source. A second cat, a source actor or a
foreign room outside the mask cannot be invented (rejects of 03–04.11.2026). The performer mask comes from
H3CharacterTrack + H3CharacterMask (SAM2.1, h3-character-refine). Same idea as the upstream Character Swap
recipe (SAM mask + edit mask); this code is our own. A/B route until a render is accepted.
"""
import json

import comfy.nested_tensor
import torch

from .latent_mask import crop_box, fit_frames, latent_noise_mask, remap_box


class H3SourceFrames:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"images": ("IMAGE",),
                             "width": ("INT", {"default": 672, "min": 32, "max": 4096, "step": 32}),
                             "height": ("INT", {"default": 1216, "min": 32, "max": 4096, "step": 32}),
                             "length": ("INT", {"default": 243, "min": 5, "max": 3600})},
                "optional": {"characters_json": ("STRING", {"default": "[]", "multiline": True})}}

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "characters_json")
    FUNCTION = "fit"
    CATEGORY = "STUDIO/H3"
    DESCRIPTION = ("Source clip at the job's canvas and exact frame count (centre crop, last frame repeated); "
                   "performer boxes drawn on the full source frame are moved into the cropped frame.")

    def fit(self, images, width, height, length, characters_json="[]"):
        crop = crop_box(width, height, int(images.shape[2]), int(images.shape[1]))
        characters = [{**c, "box": remap_box(c["box"], crop)} for c in json.loads(characters_json or "[]")]
        return (fit_frames(images, width, height, length), json.dumps(characters, ensure_ascii=False))


class H3SourceEditMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"latent": ("LATENT",), "vae": ("VAE",), "images": ("IMAGE",), "mask": ("MASK",)}}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "apply"
    CATEGORY = "STUDIO/H3"
    DESCRIPTION = ("Write the source clip into the H3 AV latent and regenerate only the masked performers "
                   "(video); the sound is regenerated whole.")

    def apply(self, latent, vae, images, mask):
        samples = latent["samples"]
        if not getattr(samples, "is_nested", False) or len(samples.tensors) != 2 or samples.tensors[0].shape[1] != 24:
            raise ValueError("H3SourceEditMask expects a MiniMax H3 AV latent")
        video, sound = (tensor.clone() for tensor in samples.tensors)
        _, _, t, h, w = video.shape
        encoded = vae.encode(images[:, :, :, :3])
        if encoded.ndim == 4:
            encoded = encoded.unsqueeze(0)
        # The VAE may pad a clip to its chunk length; the first t tokens are the real frames.
        if encoded.shape[2] < t or tuple(encoded.shape[3:]) != (h, w):
            raise ValueError(f"Исходник после VAE {tuple(encoded.shape)} не совпадает с кадром {tuple(video.shape)}")
        video[:] = encoded[:1, :, :t].to(video)
        video_mask = latent_noise_mask(mask.to(torch.float32).cpu(), t, h, w)
        if float(video_mask.sum()) == 0:
            raise ValueError("Маска замены пуста: исполнитель не найден")
        sound_mask = torch.ones(1, 1, *sound.shape[2:], dtype=torch.float32)
        return ({**latent, "samples": comfy.nested_tensor.NestedTensor((video, sound)),
                 "noise_mask": comfy.nested_tensor.NestedTensor((video_mask, sound_mask))},)


NODE_CLASS_MAPPINGS = {"H3SourceFrames": H3SourceFrames, "H3SourceEditMask": H3SourceEditMask}
NODE_DISPLAY_NAME_MAPPINGS = {"H3SourceFrames": "H3 Source Frames", "H3SourceEditMask": "H3 Source Edit Mask"}
