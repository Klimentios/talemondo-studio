"""Native ComfyUI App Mode controls for the local MiniMax H3 stack."""

from __future__ import annotations

import hashlib
import os
import re

import folder_paths
import nodes
from comfy_api.latest import InputImpl
from comfy_extras.nodes_audio import load as load_audio_file
from comfy_extras.nodes_minimax_h3 import (
    MiniMaxH3ImageToVideo,
    MiniMaxH3ReferenceToVideo,
)


NONE = "— не использовать —"
ASPECTS = ["16:9 — широкий", "9:16 — вертикальный"]
QUALITIES = ["480p — быстро", "768p — максимум H3"]
MODES = [
    "Текст → видео / Text to Video",
    "Кадры → видео / First & Last Frame",
    "Референсы → видео / Reference to Video",
]


def _files(content_type: str) -> list[str]:
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    files = [
        name
        for name in os.listdir(input_dir)
        if os.path.isfile(os.path.join(input_dir, name))
    ]
    return folder_paths.filter_files_content_types(files, [content_type])


def _upload_widget(content_type: str, upload_flag: str):
    return ([NONE, *_files(content_type)], {upload_flag: True})


def _selected(values: list[str], label: str) -> list[str]:
    """Return a compact ordered list and reject gaps that would renumber tags."""
    present = [value not in (None, "", NONE) for value in values]
    if any(present[index] and not all(present[:index]) for index in range(len(present))):
        raise ValueError(
            f"Заполните {label} по порядку без пропусков: 1, 2, 3…"
        )
    return [value for value, keep in zip(values, present) if keep]


def _compile_prompt(prompt: str) -> str:
    result = prompt or ""
    for index in range(1, 10):
        result = re.sub(
            rf"(?<![\w@])@ref{index}(?!\w)",
            f"<Picture {index}>",
            result,
            flags=re.IGNORECASE,
        )
    return result


class H3NativeReferenceComposer:
    """One App Mode node with optional image, video, and audio upload widgets."""

    @classmethod
    def INPUT_TYPES(cls):
        required = {
            "clip": ("CLIP",),
            "vae": ("VAE",),
            "audio_vae": ("VAE",),
            "fl_model": ("MODEL",),
            "ref_model": ("MODEL",),
            "mode": (MODES, {"default": MODES[2]}),
            "prompt": (
                "STRING",
                {
                    "multiline": True,
                    "dynamicPrompts": True,
                    "default": (
                        "<Picture 1> — точная личность главного героя.\n"
                        "<Picture 2> — костюм, реквизит или окружение.\n\n"
                        "Опишите один связный эпизод: действие, камеру, свет и звук."
                    ),
                },
            ),
            "duration": ("FLOAT", {"default": 5.0, "min": 5.0, "max": 15.0, "step": 0.5}),
            "aspect_ratio": (ASPECTS, {"default": ASPECTS[0]}),
            "quality": (QUALITIES, {"default": QUALITIES[0]}),
            "ref_image_size": (["max", "match"], {"default": "max"}),
            "first_frame": _upload_widget("image", "image_upload"),
            "last_frame": _upload_widget("image", "image_upload"),
        }
        for index in range(1, 10):
            required[f"picture_{index}"] = _upload_widget("image", "image_upload")
        for index in range(1, 4):
            required[f"video_{index}"] = _upload_widget("video", "video_upload")
        for index in range(1, 4):
            required[f"audio_{index}"] = _upload_widget("audio", "audio_upload")
        return {"required": required}

    RETURN_TYPES = ("MODEL", "CONDITIONING", "LATENT")
    RETURN_NAMES = ("model", "positive", "latent")
    FUNCTION = "compose"
    CATEGORY = "MiniMax H3"
    DESCRIPTION = (
        "Нативный Ref2VA composer: до 9 Picture, 3 Video и 3 Audio. "
        "Пустые слоты не попадают в граф."
    )

    def compose(
        self,
        clip,
        vae,
        audio_vae,
        fl_model,
        ref_model,
        mode,
        prompt,
        duration,
        aspect_ratio,
        quality,
        ref_image_size,
        first_frame,
        last_frame,
        **kwargs,
    ):
        picture_names = _selected(
            [kwargs[f"picture_{index}"] for index in range(1, 10)], "Picture"
        )
        video_names = _selected(
            [kwargs[f"video_{index}"] for index in range(1, 4)], "Video"
        )
        audio_names = _selected(
            [kwargs[f"audio_{index}"] for index in range(1, 4)], "Audio"
        )
        is_reference = mode.startswith("Референсы")
        is_keyframe = mode.startswith("Кадры")
        if is_reference and not picture_names and not video_names and not audio_names:
            raise ValueError(
                "Добавьте хотя бы один Picture, Video или Audio референс."
            )
        if is_keyframe and first_frame in (None, "", NONE):
            raise ValueError("Для режима «Кадры → видео» нужен начальный кадр.")

        if quality.startswith("480p"):
            width, height = 864, 480
        else:
            width, height = 1344, 768
        if aspect_ratio.startswith("9:16"):
            width, height = height, width

        length = max(5, round(float(duration) * 24))
        while length % 17 != 5:
            length += 1

        ref_images = {}
        for index, name in enumerate(picture_names):
            ref_images[f"ref_image_{index}"] = nodes.LoadImage().load_image(name)[0]

        ref_videos = {}
        ref_video_audios = {}
        for index, name in enumerate(video_names):
            path = folder_paths.get_annotated_filepath(name)
            components = InputImpl.VideoFromFile(path).get_components()
            ref_videos[f"ref_video_{index}"] = components.images
            if components.audio is not None:
                ref_video_audios[f"ref_video_audio_{index}"] = components.audio

        ref_audios = {}
        for index, name in enumerate(audio_names):
            path = folder_paths.get_annotated_filepath(name)
            waveform, sample_rate = load_audio_file(path)
            ref_audios[f"ref_audio_{index}"] = {
                "waveform": waveform.unsqueeze(0),
                "sample_rate": sample_rate,
            }

        if is_reference:
            output = MiniMaxH3ReferenceToVideo.execute(
                clip=clip,
                vae=vae,
                audio_vae=audio_vae,
                prompt=_compile_prompt(prompt),
                width=width,
                height=height,
                length=length,
                ref_image_size=ref_image_size,
                ref_images=ref_images,
                ref_videos=ref_videos,
                ref_video_audios=ref_video_audios,
                ref_audios=ref_audios,
            )
            return (ref_model, *output.result)

        start = (
            nodes.LoadImage().load_image(first_frame)[0]
            if is_keyframe and first_frame not in (None, "", NONE)
            else None
        )
        end = (
            nodes.LoadImage().load_image(last_frame)[0]
            if is_keyframe and last_frame not in (None, "", NONE)
            else None
        )
        output = MiniMaxH3ImageToVideo.execute(
            clip=clip,
            vae=vae,
            prompt=prompt,
            width=width,
            height=height,
            length=length,
            first_frame=start,
            last_frame=end,
        )
        return (fl_model, *output.result)

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        digest = hashlib.sha256()
        for prefix, limit in (
            ("picture", 9),
            ("video", 3),
            ("audio", 3),
            ("first_frame", 1),
            ("last_frame", 1),
        ):
            for index in range(1, limit + 1):
                name = kwargs.get(prefix) if limit == 1 else kwargs.get(f"{prefix}_{index}")
                if name in (None, "", NONE):
                    continue
                path = folder_paths.get_annotated_filepath(name)
                digest.update(name.encode("utf-8"))
                digest.update(str(os.path.getmtime(path)).encode("ascii"))
        return digest.hexdigest()


NODE_CLASS_MAPPINGS = {
    "H3NativeReferenceComposer": H3NativeReferenceComposer,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3NativeReferenceComposer": "MiniMax H3 — Референсы → видео",
}
