"""Character Swap LoRA V1 vs V1.5 (A/B, 04.11.2026): the file reaches the LoRA node."""
from app.contract import GenerationRequest
from app.director import missing_model_files
from app.graph import build_prompt
from app.h3graph.model_chain import STYLE_ADAPTERS
from local_studio.store import SETTINGS_KEYS, SWAP_LORAS


def test_v15_maps_to_its_own_file_and_default_stays_v1():
    assert SWAP_LORAS == {"v1": "character_swap", "v1.5": "character_swap_v15"}
    assert "swap_lora" in SETTINGS_KEYS
    assert STYLE_ADAPTERS["character_swap"][0] == "h3_character_swap_pro4500_1000.safetensors"
    assert STYLE_ADAPTERS["character_swap_v15"] == ("h3_character_swap_v1.5.safetensors", 1.0)
    assert missing_model_files("character_swap_v15", {}) == ["h3_character_swap_v1.5.safetensors"]


def test_v15_reaches_the_lora_loader():
    request = GenerationRequest("<Picture 1> walks.", 5, "9:16", "custom", megapixels=0.8, steps=24)
    graph = build_prompt(request, ["a.png"], style_adapter="character_swap_v15")
    loras = [n for n in graph.values() if n["class_type"] == "LoraLoaderModelOnly"]
    assert [n["inputs"]["lora_name"] for n in loras] == ["h3_character_swap_v1.5.safetensors"]
    assert loras[0]["_meta"]["title"] == "Character Swap LoRA"
