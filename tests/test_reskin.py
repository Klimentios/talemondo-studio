"""Reskin A/B (05.11.2026): the source clip is the starting latent and the scheduler starts part-way down."""
import pytest

from app.h3graph.nodes import Node, node, out
from app.reskin import RESKIN_NODES, apply_reskin, normalize_reskin


def test_strength_is_validated():
    assert normalize_reskin(None) is None
    assert normalize_reskin({"strength": 0.7}) == {"strength": 0.7}
    for bad in ({"strength": 0.1}, {"strength": 1.0}, {"strength": "0.7"}, {"strength": 0.7, "x": 1}):
        with pytest.raises(ValueError):
            normalize_reskin(bad)


def test_graph_starts_from_the_source_latent():
    graph = {
        "50": node("GetVideoComponents", "@video1 components", video=["49", 0]),
        Node.REF2VA: node("MiniMaxH3ReferenceToVideo", "Ref2VA", width=672, height=1216, length=158),
        Node.VIDEO_VAE: node("VAELoader", "Видео VAE", vae_name="v.safetensors"),
        Node.SCHEDULER: node("BasicScheduler", "Base", scheduler="simple", steps=24, denoise=1.0, model=["1", 0]),
        Node.SAMPLE: node("SamplerCustomAdvanced", "Генерация", latent_image=out(Node.REF2VA, 1)),
    }
    graph = apply_reskin(graph, {"strength": 0.7}, 672, 1216, 158)
    classes = {item["class_type"] for item in graph.values()}
    assert RESKIN_NODES <= classes
    edit = graph[graph[Node.SAMPLE]["inputs"]["latent_image"][0]]
    assert edit["class_type"] == "H3SourceEditMask" and edit["inputs"]["latent"] == out(Node.REF2VA, 1)
    assert graph[Node.SCHEDULER]["inputs"]["denoise"] == 0.7
    frames = graph[edit["inputs"]["images"][0]]
    assert frames["inputs"]["images"] == out("50") and frames["inputs"]["length"] == 158
