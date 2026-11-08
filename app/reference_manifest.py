from __future__ import annotations

from dataclasses import dataclass
import re


_TAG = re.compile(r"<(Picture|Video|Audio) (\d+)>")
_TRAILING_AT_TAG = re.compile(r"<(Picture|Video|Audio) (\d+)@>")
_KIND_TAG = {"image": "Picture", "video": "Video", "audio": "Audio"}


@dataclass(frozen=True)
class CompiledManifest:
    prompt: str
    images: tuple[dict, ...]
    videos: tuple[dict, ...]
    audios: tuple[dict, ...]
    mapping: dict[str, str]


def compile_reference_manifest(
    prompt: str, assets: list[dict], *, use_video_audio: bool = False
) -> CompiledManifest:
    """Compile stable editor labels into H3's dense per-kind upload indexes.

    The returned arrays are the *only* permitted upload order for this prompt.
    An unknown label is an error, never silently assigned to a different file.
    """
    groups: dict[str, list[dict]] = {"image": [], "video": [], "audio": []}
    seen_ids: set[str] = set()
    seen_labels: set[str] = set()
    for asset in assets:
        kind = asset.get("kind")
        number = asset.get("number")
        asset_id = asset.get("asset_id")
        if kind not in groups or not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError("Некорректный тип или номер референса")
        if not isinstance(asset_id, str) or not asset_id or asset_id in seen_ids:
            raise ValueError("Повторный или пустой asset_id")
        seen_ids.add(asset_id)
        tag = f"<{_KIND_TAG[kind]} {number}>"
        if tag in seen_labels:
            raise ValueError(f"Повторный номер {tag}")
        seen_labels.add(tag)
        groups[kind].append(asset)
    if len(groups["image"]) > 9 or len(groups["video"]) > 3 or len(groups["audio"]) > 3 or len(assets) > 12:
        raise ValueError("Превышен лимит референсов H3")
    mapping: dict[str, str] = {}
    for kind, target in (("image", "Picture"), ("video", "Video")):
        for dense, asset in enumerate(groups[kind], 1):
            mapping[f"<{target} {asset['number']}>"] = f"<{target} {dense}>"
    soundtrack_count = 0
    if use_video_audio:
        for asset in groups["video"]:
            soundtrack = asset.get("soundtrack_number")
            if soundtrack is None:
                raise ValueError(f"Не указан soundtrack_number для <Video {asset['number']}>")
            if not isinstance(soundtrack, int) or isinstance(soundtrack, bool) or soundtrack < 1:
                raise ValueError("Некорректный номер звуковой дорожки")
            soundtrack_count += 1
            label = f"<Audio {soundtrack}>"
            if label in mapping:
                raise ValueError(f"Повторный номер {label}")
            mapping[label] = f"<Audio {soundtrack_count}>"
    for dense, asset in enumerate(groups["audio"], soundtrack_count + 1):
        label = f"<Audio {asset['number']}>"
        if label in mapping:
            raise ValueError(f"Повторный номер {label}")
        mapping[label] = f"<Audio {dense}>"

    def replace(match: re.Match[str]) -> str:
        label = match.group(0)
        if label not in mapping:
            raise ValueError(f"Референс {label} отсутствует в задании")
        return mapping[label]

    # A pasted tag can contain the editor's @ trigger immediately before `>`.
    # Treat that spelling as the same reference, then validate its asset normally.
    normalized = _TRAILING_AT_TAG.sub(lambda match: f"<{match.group(1)} {match.group(2)}>", prompt)
    compiled = _TAG.sub(replace, normalized)
    return CompiledManifest(
        prompt=compiled,
        images=tuple(groups["image"]),
        videos=tuple(groups["video"]),
        audios=tuple(groups["audio"]),
        mapping=mapping,
    )


def validate_dense_prompt(prompt: str, image_count: int, video_count: int,
                          audio_count: int, video_soundtrack_count: int = 0) -> None:
    limits = {"Picture": image_count, "Video": video_count,
              "Audio": audio_count + video_soundtrack_count}
    for match in _TAG.finditer(prompt):
        number = int(match.group(2))
        if number < 1 or number > limits[match.group(1)]:
            raise ValueError(f"Референс {match.group(0)} не привязан к загруженному файлу")
