"""Reference loaders and their Ref2VA slots, in the dense per-kind order H3 numbers them."""
from __future__ import annotations

from dataclasses import dataclass, field

from .nodes import Node, node, out

MAX_IMAGES = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3
MAX_TOTAL = 12


def check_counts(images: int, videos: int, audios: int) -> None:
    if images > MAX_IMAGES:
        raise ValueError("Можно загрузить не больше девяти изображений")
    if videos > MAX_VIDEOS or audios > MAX_AUDIOS:
        raise ValueError("Можно загрузить не больше трёх видео и трёх аудио")
    if images + videos + audios > MAX_TOTAL:
        raise ValueError("Всего можно использовать не больше 12 референсов")
    if not images and not videos:
        raise ValueError("В Ref2VA нужно минимум одно изображение или видео")


@dataclass
class ReferenceSlots:
    """What the loaders produced: Ref2VA inputs, the image loader ids (<Picture N> = image_nodes[N-1])
    and the <Video N> numbers whose own soundtrack is a reference too."""
    inputs: dict[str, object] = field(default_factory=dict)
    image_nodes: list[str] = field(default_factory=list)
    video_audio_indices: list[int] = field(default_factory=list)
    video_count: int = 0

    def image_loader(self, picture_number: int) -> str:
        return self.image_nodes[picture_number - 1]

    def add_video_slot(self, source: list) -> int:
        """Appends one more <Video> input (the camera guide); returns its 0-based slot."""
        slot = self.video_count
        self.inputs[f"ref_videos.ref_video_{slot}"] = source
        self.video_count += 1
        return slot


def add_loaders(graph: dict, images: list[str], videos: list[str], audios: list[str],
                use_video_audio: bool) -> ReferenceSlots:
    slots = ReferenceSlots()
    next_id = Node.FIRST_LOADER
    for index, filename in enumerate(images):
        node_id = str(next_id)
        next_id += 1
        slots.image_nodes.append(node_id)
        graph[node_id] = node("LoadImage", f"@ref{index + 1}", image=filename)
        slots.inputs[f"ref_images.ref_image_{index}"] = out(node_id)

    for index, filename in enumerate(videos):
        load_id, components_id = str(next_id), str(next_id + 1)
        next_id += 2
        graph[load_id] = node("LoadVideo", f"@video{index + 1}", file=filename)
        graph[components_id] = node("GetVideoComponents", f"@video{index + 1} components", video=out(load_id))
        slots.inputs[f"ref_videos.ref_video_{index}"] = out(components_id)
        if use_video_audio:
            slots.inputs[f"ref_video_audios.ref_video_audio_{index}"] = out(components_id, 1)
            slots.video_audio_indices.append(index + 1)
    slots.video_count = len(videos)

    for index, filename in enumerate(audios):
        node_id = str(next_id)
        next_id += 1
        graph[node_id] = node("LoadAudio", f"@audio{index + 1}", audio=filename)
        slots.inputs[f"ref_audios.ref_audio_{index}"] = out(node_id)
    return slots
