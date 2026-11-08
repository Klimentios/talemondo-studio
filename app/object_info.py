"""Reading ComfyUI /object_info. Choice lists come in two formats on the same server:
legacy nodes (UNETLoader, VAELoader, LoraLoaderModelOnly…) send [["a", "b"], {...}], nodes on the
current schema (KSamplerSelect, BasicScheduler…) send ["COMBO", {"options": ["a", "b"]}]."""
from __future__ import annotations


def combo_options(spec: object) -> list:
    if not isinstance(spec, (list, tuple)) or not spec:
        return []
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        options = spec[1].get("options")
        return options if isinstance(options, list) else []
    return []


def input_options(node_info: dict, node_type: str, input_name: str) -> list:
    """Choices of a required or optional COMBO input; [] when the node or input is unknown."""
    inputs = (node_info.get(node_type) or {}).get("input") or {}
    spec = (inputs.get("required") or {}).get(input_name, (inputs.get("optional") or {}).get(input_name))
    return combo_options(spec)
