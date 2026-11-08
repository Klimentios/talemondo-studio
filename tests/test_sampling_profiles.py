"""Sampling profiles reach the real ComfyUI sampler and scheduler nodes."""
from app.contract import GenerationRequest
from app.graph import build_prompt
from app.h3graph.nodes import Node
from app.sampling import sampling_inputs


def test_ref_beta_sets_res_multistep_and_beta():
    # Comfy-Org R2V template note: beta/normal beat simple for reference-heavy prompts (A/B only).
    assert sampling_inputs("ref_beta") == ("res_multistep", "beta")
    graph = build_prompt(GenerationRequest("<Picture 1> walks.", 5, "9:16", "custom", megapixels=0.4, steps=24),
                         ["a.png"], sampling_profile="ref_beta")
    assert graph[Node.SAMPLER]["inputs"]["sampler_name"] == "res_multistep"
    assert graph[Node.SCHEDULER]["inputs"]["scheduler"] == "beta"
    assert sampling_inputs("standard") == ("res_multistep", "simple")  # default unchanged
