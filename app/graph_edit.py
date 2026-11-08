"""A graph edited by hand in ComfyUI and sent back to Studio (h3-studio-bridge «Отправить в Studio»).

Studio shows the changes before the render; the GPU API checks the same rules again before
queueing. Models stay those of the Studio graph: a new or swapped weight file (turbo, int8,
another base) is refused, everything else — prompt, seed, steps, wiring, extra nodes — may change.
"""
from __future__ import annotations

import json

MODEL_SUFFIXES = (".safetensors", ".pt", ".pth", ".ckpt", ".gguf", ".bin", ".onnx", ".sft")


def model_files(graph: dict) -> set[str]:
    return {value for node in graph.values() if isinstance(node, dict)
            for value in (node.get("inputs") or {}).values()
            if isinstance(value, str) and value.lower().endswith(MODEL_SUFFIXES)}


def _shown(value):
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
        return f"← узел {value[0]}"
    return value


def graph_changes(source: dict, edited: dict) -> dict:
    """{added, removed, changed}; a changed input lists literal values, links as «← узел N»."""
    added = [{"node": key, "class_type": edited[key].get("class_type")} for key in edited if key not in source]
    removed = [{"node": key, "class_type": source[key].get("class_type")} for key in source if key not in edited]
    changed = []
    for key in source:
        if key not in edited:
            continue
        before, after = source[key], edited[key]
        if before.get("class_type") != after.get("class_type"):
            changed.append({"node": key, "class_type": after.get("class_type"), "input": "class_type",
                            "before": before.get("class_type"), "after": after.get("class_type")})
            continue
        old, new = before.get("inputs") or {}, after.get("inputs") or {}
        for name in sorted(set(old) | set(new)):
            if old.get(name) != new.get(name):
                changed.append({"node": key, "class_type": after.get("class_type"), "input": name,
                                "before": _shown(old.get(name)), "after": _shown(new.get(name))})
    return {"added": added, "removed": removed, "changed": changed}


def edit_problems(source: dict, edited: dict) -> list[str]:
    if not isinstance(edited, dict) or not edited or not all(
            isinstance(node, dict) and isinstance(node.get("class_type"), str) for node in edited.values()):
        return ["Граф из ComfyUI пустой или не в API-формате"]
    # Public edition: any model, LoRA or turbo/quantized weight may be swapped in; the change list shows it.
    problems = []
    if edited == source:
        problems.append("Граф не изменён: это тот же рендер, повтори задание в Studio")
    return problems


def input_spec(node_info: dict, class_type: str, name: str):
    inputs = (node_info.get(class_type) or {}).get("input") or {}
    return (inputs.get("required") or {}).get(name, (inputs.get("optional") or {}).get(name))


def apply_settings(graph: dict, settings: list[dict], node_info: dict) -> dict:
    """An agent's edit: [{node, input, value}] on literal inputs of existing nodes, checked against
    the node schema (/object_info/<class>): links and model files stay as Studio built them."""
    from .object_info import combo_options

    edited = json.loads(json.dumps(graph))
    if not settings:
        raise ValueError("Нет изменений: передай [{node, input, value}]")
    for item in settings:
        key, name, value = str(item.get("node")), item.get("input"), item.get("value")
        node = edited.get(key)
        if node is None:
            raise ValueError(f"Узла {key} нет в графе задания")
        current = (node.get("inputs") or {}).get(name)
        if isinstance(current, list) or isinstance(value, (list, dict)):
            raise ValueError(f"{node['class_type']} #{key} · {name}: связи меняются только в редакторе ComfyUI")
        spec = input_spec(node_info, node["class_type"], name)
        if spec is None:
            raise ValueError(f"{node['class_type']} #{key}: входа «{name}» нет в схеме узла")
        options = combo_options(spec)
        if options and value not in options:
            raise ValueError(f"{node['class_type']} #{key} · {name}: «{value}» не из списка {options[:12]}")
        limits = spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) else {}
        if spec[0] in ("INT", "FLOAT"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (spec[0] == "INT" and int(value) != value):
                raise ValueError(f"{node['class_type']} #{key} · {name}: нужно число {spec[0]}")
            if ("min" in limits and value < limits["min"]) or ("max" in limits and value > limits["max"]):
                raise ValueError(f"{node['class_type']} #{key} · {name}: вне {limits.get('min')}…{limits.get('max')}")
            value = int(value) if spec[0] == "INT" else float(value)
        elif spec[0] == "STRING" and not isinstance(value, str) or spec[0] == "BOOLEAN" and not isinstance(value, bool):
            raise ValueError(f"{node['class_type']} #{key} · {name}: нужен тип {spec[0]}")
        node["inputs"][name] = value
    return edited
