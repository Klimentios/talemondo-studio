"""Two-pass hires (app/h3graph/hires.py), A/B only."""
import pytest

from app.ab_variant import apply_ab_variant, parse_ab_variant
from app.contract import GenerationRequest
from app.graph import build_prompt
from app.h3graph import hires
from app.h3graph.nodes import Node


def request(prompt="<Picture 1> walks.", quality="native"):
    return GenerationRequest(prompt, 5, "16:9", quality, steps=25, seed=3)


def variant(**fields):
    keys = ("name", "scheduler", "lora", "lora_strength", "sparse_attention", "director_plan_json")
    base = {key: fields.pop(key, "") for key in keys}
    parsed, _ = parse_ab_variant(**base, **fields)
    return parsed


def test_default_graph_is_untouched():
    assert build_prompt(request(), ["a.png"]) == build_prompt(request(), ["a.png"], hires=None)


def test_two_pass_wiring():
    one = build_prompt(request(), ["a.png"])
    two = build_prompt(request(), ["a.png"], hires={"method": "bislerp", "tail": 0.25})
    final = (one[Node.REF2VA]["inputs"]["width"], one[Node.REF2VA]["inputs"]["height"])
    assert final == (1344, 768)
    assert (two[Node.REF2VA]["inputs"]["width"], two[Node.REF2VA]["inputs"]["height"]) == (672, 384)
    assert two[Node.SCHEDULER]["inputs"]["steps"] == 25  # pass 1 runs every step
    split, up, guider, sample = (two[n] for n in (hires.SPLIT, hires.UPSCALE, hires.GUIDER, hires.SAMPLE))
    assert split["class_type"] == "SplitSigmasDenoise" and split["inputs"]["denoise"] == 0.25
    assert up["inputs"]["samples"] == [Node.SAMPLE, 0] and up["inputs"]["sigmas"] == [hires.SPLIT, 1]
    assert up["inputs"]["audio_denoise"] == 0.0 and up["inputs"]["method"] == "bislerp"
    assert up["inputs"]["positive"] == two[Node.GUIDER]["inputs"]["conditioning"]
    assert guider["inputs"]["conditioning"] == [hires.UPSCALE, 1]
    assert sample["inputs"]["latent_image"] == [hires.UPSCALE, 0] and sample["inputs"]["sigmas"] == [hires.SPLIT, 1]
    assert two[Node.DECODE_VIDEO]["inputs"]["samples"] == [hires.SAMPLE, 0]
    assert two[Node.DECODE_AUDIO]["inputs"]["samples"] == [hires.SAMPLE, 0]
    # Same model chain for both passes (cache/LoRA included).
    assert up["inputs"]["model"] == guider["inputs"]["model"] == two[Node.GUIDER]["inputs"]["model"]


def test_scene_guides_and_continuation_feed_the_upscaler():
    graph = build_prompt(request("<Picture 1> walks into <Picture 2>."), ["a.png", "b.png"],
                         guide_frames=[(2, -1)], continuation={"file": "c.mp4", "frames": 72, "audio": True},
                         hires={"method": "bislerp", "tail": 0.25})
    assert graph[hires.UPSCALE]["inputs"]["positive"] == [str(Node.FIRST_SCENE_GUIDE), 0]


@pytest.mark.parametrize("quality,ok", [("native", True), ("quick", False)])
def test_final_size_must_halve_onto_the_grid(quality, ok):
    req = request(quality=quality)
    if ok:
        build_prompt(req, ["a.png"], hires={"method": "bislerp", "tail": 0.25})
    elif req.width % 64 or req.height % 64:
        with pytest.raises(ValueError, match="делиться на 64"):
            build_prompt(req, ["a.png"], hires={"method": "bislerp", "tail": 0.25})


def test_ab_field_validation_and_overrides():
    parsed = variant(hires="learned", hires_tail="0.3")
    assert parsed.build_overrides()["hires"] == {"method": "learned", "tail": 0.3}
    assert hires.REQUIRED_NODES <= parsed.required_nodes()
    assert variant(hires="bislerp").build_overrides()["hires"]["tail"] == hires.DEFAULT_TAIL
    for bad in ({"hires": "nearest"}, {"hires_tail": "0.3"}, {"hires": "bislerp", "hires_tail": "0.9"},
                {"hires": "bislerp", "resolution": "768p"}):
        with pytest.raises(ValueError):
            variant(**bad)


def test_ab_lora_reaches_both_passes():
    parsed = variant(hires="bislerp", lora="style3d", lora_strength="1.0")
    graph = apply_ab_variant(build_prompt(request(), ["a.png"], **parsed.build_overrides()), parsed)
    for node_id in (Node.SCHEDULER, Node.GUIDER, hires.UPSCALE, hires.GUIDER):
        assert graph[node_id]["inputs"]["model"] == ["312", 0], node_id
