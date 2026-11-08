#!/usr/bin/env python3
"""Validate H3 ComfyUI graphs against a live ComfyUI /object_info, without running them.

    python3 scripts/h3_graph_validate.py --object-info http://127.0.0.1:28188/object_info GRAPH.json [...]

A GRAPH.json is either an API-format graph ({node_id: {class_type, inputs}}) or a dict with a
"graph" key (tests/golden/*_gpu_*.json). Reports unknown node classes, missing required inputs,
unknown inputs, values outside a combo list (e.g. a weight file not installed) or numeric range,
and links to missing nodes. Exit 1 if anything is wrong.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


UPLOADED_AT_JOB_TIME = {"image", "video", "file", "audio"}


def load_object_info(source: str) -> dict:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=60) as response:
            return json.load(response)
    return json.loads(Path(source).read_text())


def _spec_options(spec: list) -> tuple[object, dict]:
    kind = spec[0] if spec else None
    options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    return kind, options


def validate(graph: dict, info: dict) -> list[str]:
    problems: list[str] = []
    for node_id, node in graph.items():
        cls = node.get("class_type")
        where = f"[{node_id}] {cls}"
        if cls not in info:
            problems.append(f"{where}: класс узла не установлен в ComfyUI")
            continue
        schema = info[cls].get("input", {})
        required = schema.get("required", {}) or {}
        optional = schema.get("optional", {}) or {}
        known = {**required, **optional}
        inputs = node.get("inputs", {})
        for name in required:
            # A dynamic (autogrow) input such as "images" arrives as "images.image_1", …
            if name not in inputs and not any(key.startswith(f"{name}.") for key in inputs):
                problems.append(f"{where}: нет обязательного входа {name}")
        for name, value in inputs.items():
            # "format.codec": a sub-input of a dynamic combo; its parent is checked instead.
            if "." in name and name.split(".", 1)[0] in known:
                continue
            if name not in known:
                problems.append(f"{where}: неизвестный вход {name}={value!r}")
                continue
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                if str(value[0]) not in graph:
                    problems.append(f"{where}: вход {name} ссылается на отсутствующий узел {value[0]}")
                else:
                    source = graph[str(value[0])].get("class_type")
                    outputs = info.get(source, {}).get("output")
                    if isinstance(outputs, list):
                        if value[1] < 0 or value[1] >= len(outputs):
                            problems.append(f"{where}: вход {name} ссылается на отсутствующий выход {value[1]} узла {source}")
                        else:
                            expected, _ = _spec_options(known[name])
                            actual = outputs[value[1]]
                            if isinstance(expected, str) and expected != "*" and actual != "*" and expected != actual:
                                problems.append(f"{where}: вход {name} ожидает {expected}, выход {source} имеет тип {actual}")
                continue
            if cls.startswith("Load") and name in UPLOADED_AT_JOB_TIME:
                continue  # the GPU API uploads these files when the job is submitted
            kind, options = _spec_options(known[name])
            choices = kind if isinstance(kind, list) else (options.get("options") if kind == "COMBO" else None)
            if isinstance(choices, list) and choices and all(not isinstance(c, dict) for c in choices):
                if value not in choices:
                    preview = ", ".join(map(str, choices[:6]))
                    problems.append(f"{where}: {name}={value!r} не из списка ({preview}{'…' if len(choices) > 6 else ''})")
            elif isinstance(kind, str) and kind in {"INT", "FLOAT"} and isinstance(value, (int, float)) and not isinstance(value, bool):
                low, high = options.get("min"), options.get("max")
                if (low is not None and value < low) or (high is not None and value > high):
                    problems.append(f"{where}: {name}={value} вне диапазона [{low}, {high}]")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--object-info", required=True, help="URL or saved object_info.json")
    parser.add_argument("graphs", nargs="+")
    args = parser.parse_args()
    info = load_object_info(args.object_info)
    failed = 0
    for path in args.graphs:
        data = json.loads(Path(path).read_text())
        graph = data.get("graph", data) if isinstance(data, dict) else data
        problems = validate(graph, info)
        print(f"{'OK  ' if not problems else 'FAIL'} {path} ({len(graph)} узлов)")
        for problem in problems:
            print(f"     {problem}")
        failed |= bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
