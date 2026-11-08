#!/usr/bin/env python3
"""Can H3 run without Qwen? Render a Studio graph twice on the research GPU and compare (docs/reference/H3_ENCODER_RESEARCH.md).

  python3 scripts/h3_code_cache_test.py --prompt-id COMFY_PROMPT_ID --name code-cache-C

A: the graph as Studio built it + H3CodeSave after the Ref2VA node (Qwen encodes; the full conditioning and latent
   are written to api-data/probes/<name>/h3_input.pt).
B: the same graph with every consumer of the Ref2VA outputs wired to H3CodeLoad — ComfyUI then never executes the
   Ref2VA node, the CLIPLoader or Qwen. Same seed, sampler, steps.
Reports render time, which nodes ComfyUI executed and the MD5 of the decoded video and audio of both MP4s.
Research only: never against a production queue.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY = "http://127.0.0.1:18188"


def http(method: str, url: str, body: dict | None = None, timeout: float = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def run(graph: dict, label: str, out: Path) -> dict:
    client = str(uuid.uuid4())
    started = time.time()
    pid = http("POST", f"{COMFY}/prompt", {"prompt": graph, "client_id": client})["prompt_id"]
    print(f"{label}: prompt {pid}", flush=True)
    while True:
        time.sleep(5)
        hist = http("GET", f"{COMFY}/history/{pid}").get(pid)
        if hist and hist.get("status", {}).get("completed") is not None and (
                hist["status"].get("completed") or hist["status"].get("status_str") == "error"):
            break
    seconds = time.time() - started
    msgs = hist["status"].get("messages", [])
    cached = next((m[1].get("nodes", []) for m in msgs if m[0] == "execution_cached"), [])
    times = {m[0]: m[1].get("timestamp") for m in msgs if m[0] in ("execution_start", "execution_success")}
    videos = [f for o in hist["outputs"].values() for k, v in o.items() if isinstance(v, list)
              for f in v if isinstance(f, dict) and str(f.get("filename", "")).endswith(".mp4")]
    result = {"label": label, "prompt_id": pid, "status": hist["status"].get("status_str"), "wall_s": round(seconds, 1),
              "exec_s": round((times["execution_success"] - times["execution_start"]) / 1000, 1)
              if len(times) == 2 else None,
              "cached_nodes": cached, "executed_nodes": sorted(set(graph) - set(cached))}
    if videos:
        f = videos[0]
        q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")})
        target = out / f"{label}.mp4"
        urllib.request.urlretrieve(f"{COMFY}/view?{q}", target)
        for stream in ("v", "a"):
            md5 = subprocess.run(["ffmpeg", "-v", "error", "-i", str(target), "-map", f"0:{stream}", "-f", "md5", "-"],
                                 capture_output=True, text=True, timeout=120).stdout.strip()
            result[f"md5_{stream}"] = md5
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def reachable(graph: dict) -> dict:
    """Only the nodes the saved outputs depend on: B then carries no Ref2VA, CLIPLoader or reference loaders."""
    keep, todo = set(), [k for k, n in graph.items() if "filename_prefix" in n["inputs"]]
    while todo:
        k = todo.pop()
        if k in keep:
            continue
        keep.add(k)
        todo += [v[0] for v in graph[k]["inputs"].values() if isinstance(v, list) and len(v) == 2 and str(v[0]) in graph]
    return {k: v for k, v in graph.items() if k in keep}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt-id", required=True)
    parser.add_argument("--name", default="code-cache")
    args = parser.parse_args()
    out = ROOT / "logs/probes" / args.name
    out.mkdir(parents=True, exist_ok=True)
    graph = http("GET", f"{COMFY}/h3/studio/graph/{args.prompt_id}")["prompt"]
    r2v = [k for k, n in graph.items() if n["class_type"] in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo")]
    if len(r2v) != 1:
        sys.exit(f"ожидался один узел Ref2VA, найдено {len(r2v)}")
    r2v = r2v[0]
    ids = [int(k) for k in graph if k.isdigit()]
    node = str(max(ids) + 1)

    a = json.loads(json.dumps(graph))
    for n in a.values():
        for key, value in n["inputs"].items():
            if value in ([r2v, 0], [r2v, 1]):
                n["inputs"][key] = [node, value[1]]
    a[node] = {"class_type": "H3CodeSave", "inputs": {"conditioning": [r2v, 0], "latent": [r2v, 1], "name": args.name}}
    b = json.loads(json.dumps(graph))
    for n in b.values():
        for key, value in n["inputs"].items():
            if value in ([r2v, 0], [r2v, 1]):
                n["inputs"][key] = [node, value[1]]
    b[node] = {"class_type": "H3CodeLoad", "inputs": {"name": args.name}}
    b = reachable(b)
    for g, label in ((a, "A-qwen"), (b, "B-no-qwen")):
        for n in g.values():
            if "filename_prefix" in n["inputs"]:
                n["inputs"]["filename_prefix"] = f"probes/{args.name}-{label}"
    (out / "graphs.json").write_text(json.dumps({"A": a, "B": b}, ensure_ascii=False), encoding="utf-8")
    results = [run(a, "A-qwen", out), run(b, "B-no-qwen", out)]
    same = results[0].get("md5_v") == results[1].get("md5_v") and results[0].get("md5_a") == results[1].get("md5_a")
    summary = {"results": results, "identical_video_and_audio": same,
               "nodes_removed_in_B": sorted((n, graph[n]["class_type"]) for n in set(graph) - set(b))}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ИТОГ:", json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
