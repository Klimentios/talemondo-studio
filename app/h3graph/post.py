"""Decoded frames → [scene colour match] → [preset grade] → [film look] → MP4."""
from __future__ import annotations

from ..color_grade import color_grade_node_inputs
from ..color_match import IMPLEMENTATIONS as COLOR_MATCH_IMPLEMENTATIONS, color_match_grade_inputs, color_match_node_inputs
from .nodes import Node, node, out
from .references import ReferenceSlots


def build_post_chain(graph: dict, slots: ReferenceSlots, *, color_match: dict | None, color_match_impl: str,
                     color_grade: dict | None, film_look: dict | None) -> list:
    """Returns the frames CreateVideo muxes. color_match runs first (H3ColorGrade clip-wide; A/B
    ColorMatchV2 per frame), a preset color_grade after it, the film look last."""
    frames = out(Node.DECODE_VIDEO)
    if color_match:
        if not 1 <= color_match["picture"] <= len(slots.image_nodes):
            raise ValueError("color_match: картинка-образец цвета не прикреплена к заданию")
        if color_match_impl not in COLOR_MATCH_IMPLEMENTATIONS:
            raise ValueError("color_match: неизвестная реализация")
        if color_match_impl == "h3colorgrade":
            inputs = {"images": frames, **color_match_grade_inputs(color_match)}
        else:
            inputs = color_match_node_inputs(color_match, frames, out(slots.image_loader(color_match["picture"])))
        graph[Node.COLOR_MATCH] = node(COLOR_MATCH_IMPLEMENTATIONS[color_match_impl], "Цвет как в предыдущем кадре", **inputs)
        frames = out(Node.COLOR_MATCH)
    if color_grade:
        graph[Node.COLOR_GRADE] = node("H3ColorGrade", "Цветокор по фото-референсу",
                                       images=frames, **color_grade_node_inputs(color_grade))
        frames = out(Node.COLOR_GRADE)
    if film_look:
        graph[Node.FILM_LOOK] = node("H3FilmLook", "Плёночный характер камеры", images=frames, **film_look)
        frames = out(Node.FILM_LOOK)
    return frames
