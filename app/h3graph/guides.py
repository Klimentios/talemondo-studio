"""Frame anchors on the Ref2VA conditioning: a previous clip's tail (continuation) and picture keyframes."""
from __future__ import annotations

from .. import continuation as continuation_take
from .nodes import Node, node, out
from .references import ReferenceSlots


def resolve_guide_frame(frame_idx: int, length: int, guide_frames: int = 1) -> int:
    """MiniMaxH3AddGuide semantics (ComfyUI b5cc8830, comfy_extras/nodes_minimax_h3.py): a negative
    frame_idx counts from the end (-1 = last frame); index + guide length must fit inside the clip."""
    resolved = frame_idx if frame_idx >= 0 else length + frame_idx
    if resolved < 0 or resolved + guide_frames > length:
        raise ValueError(f"Guide-кадр {frame_idx} вне ролика из {length} кадров")
    return resolved


def guide_frame_sentence(image_number: int, resolved: int, length: int, fps: int = 24) -> str:
    """Official full-reference wording for a concrete frame anchor (guide §5.3)."""
    tag = f"<Picture {image_number}>"
    if resolved == 0:
        return f"The shot begins from {tag}."
    if resolved == length - 1:
        return f"The shot ends on {tag}."
    minutes, seconds = divmod(resolved / fps, 60)
    return f"At {int(minutes):02d}:{seconds:06.3f}, the shot's keyframe corresponds to {tag}."


def check_scene_guides(guide_frames: list[tuple[int, int]], image_count: int, length: int,
                       continuation: dict | None) -> list[tuple[int, int]]:
    """Validates (picture number, frame index) pairs; returns (picture number, resolved frame)."""
    resolved_guides = []
    for image_number, frame_idx in guide_frames:
        if type(image_number) is not int or type(frame_idx) is not int:
            raise ValueError("Guide frames require integer image and frame indexes")
        if not 1 <= image_number <= image_count:
            raise ValueError("Guide references must point to a loaded image")
        resolved = resolve_guide_frame(frame_idx, length)
        if continuation and resolved < continuation_take.CLIP_FRAMES:
            raise ValueError(f"Guide-кадр {frame_idx} попадает на продолжение дубля "
                             f"(кадры 0–{continuation_take.CLIP_FRAMES - 1})")
        resolved_guides.append((image_number, resolved))
    return resolved_guides


def chain_conditioning(graph: dict, slots: ReferenceSlots, guide_frames: list[tuple[int, int]],
                       continuation: dict | None) -> list:
    """Continuation first (frame 0), then each picture keyframe; returns the conditioning for the guider."""
    conditioning_node = Node.REF2VA
    if continuation:
        graph.update(continuation_take.continuation_nodes(
            continuation["file"], continuation["frames"], continuation["audio"],
            positive=out(Node.REF2VA), latent=out(Node.REF2VA, 1)))
        conditioning_node = continuation_take.CONDITIONING_NODE
    for offset, (image_number, frame_idx) in enumerate(guide_frames):
        guide_id = str(Node.FIRST_SCENE_GUIDE + offset)
        graph[guide_id] = node(
            "MiniMaxH3AddGuide", f"Scene guide at frame {frame_idx}",
            positive=out(conditioning_node), vae=out(Node.VIDEO_VAE), audio_vae=out(Node.AUDIO_VAE),
            latent=out(Node.REF2VA, 1), image=out(slots.image_loader(image_number)), frame_idx=frame_idx,
        )
        conditioning_node = guide_id
    return out(conditioning_node)
