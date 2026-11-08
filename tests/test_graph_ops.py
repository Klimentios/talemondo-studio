"""app/graph_ops.py: structural graph edits checked against /object_info (shared by workflows and job edits)."""
import pytest

from app.graph_ops import GraphEditError, apply_ops, catalog

INFO = {
    "UNETLoader": {"input": {"required": {"unet_name": [["h3_bf16.safetensors"]]}}, "output": ["MODEL"], "display_name": "Load Diffusion Model"},
    "LoraLoaderModelOnly": {"input": {"required": {"model": ["MODEL"], "lora_name": [["style.safetensors"]],
                                                   "strength_model": ["FLOAT", {"default": 1.0}]}}, "output": ["MODEL"]},
    "ModelSamplingSD3": {"input": {"required": {"model": ["MODEL"], "shift": ["FLOAT", {"default": 3.0, "min": 0, "max": 100}]}},
                         "output": ["MODEL"], "display_name": "ModelSamplingSD3", "category": "model/patch"},
    "ModelSamplingFlux": {"input": {"required": {"model": ["MODEL"], "max_shift": ["FLOAT", {"default": 1.15}]}}, "output": ["MODEL"]},
    "BasicGuider": {"input": {"required": {"model": ["MODEL"]}}, "output": ["GUIDER"]},
    "EmptyVideo": {"input": {"required": {"width": ["INT", {"default": 480, "min": 32, "max": 4096, "step": 32}],
                                          "length": ["INT", {"default": 124, "min": 5, "max": 3600, "step": 17}]}}, "output": ["LATENT"]},
}
GRAPH = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "h3_bf16.safetensors"}},
         "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": "style.safetensors", "strength_model": 0.5}},
         "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 5.0}},
         "4": {"class_type": "BasicGuider", "inputs": {"model": ["3", 0]}},
         "5": {"class_type": "EmptyVideo", "inputs": {"width": 480, "length": 124}}}


def test_remove_lora_with_bypass_keeps_the_model_chain():
    result = apply_ops(GRAPH, [{"op": "remove_node", "node": "2", "bypass": True}], INFO)
    assert result["graph"]["3"]["inputs"]["model"] == ["1", 0] and "2" not in result["graph"] and not result["problems"]


def test_replace_keeps_links_and_fills_new_required_inputs():
    result = apply_ops(GRAPH, [{"op": "replace_node", "node": "3", "class_type": "ModelSamplingFlux"}], INFO)
    node = result["graph"]["3"]
    assert node["inputs"] == {"model": ["2", 0], "max_shift": 1.15} and result["graph"]["4"]["inputs"]["model"] == ["3", 0]
    with pytest.raises(GraphEditError, match="не отдаёт MODEL"):
        apply_ops(GRAPH, [{"op": "replace_node", "node": "3", "class_type": "BasicGuider"}], INFO)


def test_int_step_and_cycles_are_refused():
    assert apply_ops(GRAPH, [{"op": "set", "node": "5", "input": "length", "value": 141}], INFO)["graph"]["5"]["inputs"]["length"] == 141
    for value in (481, 130):
        with pytest.raises(GraphEditError, match="шаг"):
            apply_ops(GRAPH, [{"op": "set", "node": "5", "input": "width" if value == 481 else "length", "value": value}], INFO)
    looped = apply_ops(GRAPH, [{"op": "link", "node": "2", "input": "model", "from": ["3", 0]}], INFO)
    assert any("Цикл" in problem for problem in looped["new_problems"])


def test_catalog_puts_the_named_class_first():
    assert catalog(INFO, "model sampling")[0]["class_type"].startswith("ModelSampling")
    assert catalog(INFO, "ModelSamplingSD3")[0]["class_type"] == "ModelSamplingSD3"
