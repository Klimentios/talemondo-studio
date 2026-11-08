from __future__ import annotations

import argparse
import json
from pathlib import Path


OFFICIAL_REF2VA_TEMPLATE = """subject_definitions:
- @ref1: [identity, age, wardrobe, immutable facial features]

summary:
[One clear sentence: subject, action, location, camera, mood]

retention_analysis:
- @ref1: fully_preserved
- @video1: partially_preserved [motion/camera only]
- @audio1: reference [timbre only]

detailed_description:
[Chronological beats. Describe camera path, body mechanics, secondary motion, lighting, shadows, reflections, materials and settling. Dialogue: <d>[Russian] ...</d>]

overall_soundscape:
[Diegetic ambience, Foley, spatial placement, voice]

non_diegetic_music:
none"""


def _by_id(workflow: dict) -> dict[int, dict]:
    return {node["id"]: node for node in workflow["nodes"]}


def tune_workflow(workflow: dict, *, use_bf16: bool = True) -> dict:
    nodes = _by_id(workflow)
    required = {127, 128, 136, 143, 210, 211, 212, 213, 220, 221, 222}
    missing = sorted(required - nodes.keys())
    if missing:
        raise ValueError(f"Unexpected workflow: missing node ids {missing}")

    if use_bf16:
        nodes[127]["widgets_values"][0] = "minimax_h3_ref2va_bf16.safetensors"
        nodes[128]["widgets_values"][0] = "qwen3vl_32b_minimax_h3_bf16.safetensors"
    nodes[143]["widgets_values"][0] = 25
    nodes[210]["inputs"][0]["label"] = "Формат: выкл. 16:9 / вкл. 9:16"

    nodes[212]["widgets_values"][0] = "1344 + a*(-576)"
    nodes[213]["widgets_values"][0] = "768 + a*(576)"
    nodes[212]["inputs"] = [item for item in nodes[212]["inputs"] if item.get("name") != "values.b"]
    nodes[213]["inputs"] = [item for item in nodes[213]["inputs"] if item.get("name") != "values.b"]

    converter = nodes[220]
    converter["type"] = "H3ReferencePrompt"
    converter["title"] = "H3 Ref2VA: @ref → <Picture>"
    converter["widgets_values"] = [OFFICIAL_REF2VA_TEMPLATE]
    converter["inputs"] = [
        {
            "name": "prompt",
            "type": "STRING",
            "widget": {"name": "prompt"},
            "link": 300,
        }
    ]
    converter["outputs"] = [{"name": "prompt", "type": "STRING", "links": [301]}]

    workflow["nodes"] = [
        node for node in workflow["nodes"] if node["id"] not in {211, 221, 222}
    ]
    workflow["links"] = [
        link for link in workflow["links"] if link[0] not in {302, 303, 311, 313}
    ]
    link_301 = next((link for link in workflow["links"] if link[0] == 301), None)
    if link_301 is None:
        raise ValueError("Unexpected workflow: link 301 is missing")
    link_301[3] = 136
    link_301[4] = 9
    nodes[136]["inputs"][9]["link"] = 301

    workflow["revision"] = int(workflow.get("revision", 0)) + 1
    return workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Tune the saved MiniMax H3 Studio workflow")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--int8", action="store_true", help="Keep full INT8 model files")
    args = parser.parse_args()

    workflow = json.loads(args.input.read_text(encoding="utf-8"))
    tuned = tune_workflow(workflow, use_bf16=not args.int8)
    args.output.write_text(
        json.dumps(tuned, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
