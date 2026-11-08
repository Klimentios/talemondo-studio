#!/usr/bin/env python3
"""Read-only Studio/API/ComfyUI configuration check, usable on a CPU stand or a new GPU.

No prompt is submitted and no media or weights are loaded. Passing proves graph
compatibility and loaded integration handlers, not successful model inference.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.contract import gpu_contract_problem
from app.studio_extensions import runtime_problems
from local_studio.motion_director import GraphPlan
from scripts.h3_graph_validate import validate

ROOT = Path(__file__).resolve().parents[1]


def json_get(url: str):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def configuration_checks(api_url: str, comfy_url: str) -> list[dict]:
    checks = []
    def check(name, action):
        try:
            problems = action()
        except Exception as exc:
            problems = [str(exc)]
        checks.append({'name': name, 'ok': not problems, 'problems': problems})
    check('Studio ↔ API version and Director schema', lambda: list(filter(None, [
        gpu_contract_problem(json_get(api_url.rstrip('/') + '/api/capabilities'))])))
    def status(path):
        try:
            with urllib.request.urlopen(comfy_url.rstrip('/') + path, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
    check('Loaded graph exchange and execution context', lambda: runtime_problems(status))
    try:
        info = json_get(comfy_url.rstrip('/') + '/object_info')
    except Exception as exc:
        checks.append({'name': 'ComfyUI object_info', 'ok': False, 'problems': [str(exc)]})
        return checks
    paths = sorted(set((ROOT / 'tests/golden').glob('*_graph.json')) |
                   set((ROOT / 'tests/golden').glob('*_gpu_*.json')))
    for path in paths:
        data = json.loads(path.read_text())
        graph = data.get('graph', data)
        # *_gpu_* also includes Director-plan fixtures, not just API graphs.
        if not graph or not all(isinstance(n, dict) and 'class_type' in n for n in graph.values()):
            continue
        check(path.name, lambda graph=graph: validate(graph, info))
    # The existing regression matrix exercises Studio's actual graph builder,
    # including camera, references, cache, style, grading and continuation.
    # Keep this a repository verification tool, not an inference dependency.
    from tests.graph_fingerprint_cases import render
    for name, graph in render().items():
        check('Studio options: ' + name, lambda graph=graph: validate(graph, info))
    plan = GraphPlan(width=768, height=1344, seed=42, segments=[
        {'id': 'contract', 'mode': 't2v', 'prompt': 'A quiet landscape.', 'duration': 5}])
    check('Motion Director compiled from Studio', lambda: validate(plan.graph()['graph'], info))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', required=True)
    parser.add_argument('--comfy-url', required=True)
    args = parser.parse_args()
    checks = configuration_checks(args.api_url, args.comfy_url)
    print(json.dumps({'scope': 'configuration_only', 'submitted': False,
                      'inference_verified': False, 'checks': checks}, ensure_ascii=False, indent=2))
    return int(any(not check['ok'] for check in checks))


if __name__ == '__main__':
    raise SystemExit(main())
