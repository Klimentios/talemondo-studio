"""Research probes on every render (docs/reference/H3_ENCODER_RESEARCH.md): opt-in per server.

When /workspace/h3/api-data/probe_all.json exists ({"steps": "0,12,23", "blocks": "20,40", "queries": 256}),
each Ref2VA job gets the read-only h3-probe nodes: the encoder code snapshot and a light attention map, saved
under api-data/probes/<client_job_id>/. The video is the same render: the attention probe calls the same
kernel on the same tensors (checked 05.11: BF16 + pytorch attention, no container kernel), the code probe
passes the conditioning through unchanged and never fails the job.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PROBE_FLAG = Path(os.environ.get("H3_PROBE_FLAG", "/workspace/h3/api-data/probe_all.json"))
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def probe_settings() -> dict | None:
    """The server's probe-all switch, or None when it is off or unreadable."""
    try:
        raw = json.loads(PROBE_FLAG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {"steps": str(raw.get("steps", "0,12,23")), "blocks": str(raw.get("blocks", "20,40")),
            "queries": int(raw.get("queries", 256)), "save_input": bool(raw.get("save_input", False))}


def insert_probes(graph: dict, name: str, *, steps: str, blocks: str, queries: int, edits: str = "",
                  attention: bool = True) -> dict:
    """Copy of the graph with H3ProbeEditCode (optional) → H3ProbeCode after the Ref2VA conditioning and
    H3ProbeAttention on the model the guider/sampler uses. Output file names are left as they are."""
    graph = json.loads(json.dumps(graph))
    r2v = [k for k, n in graph.items() if n.get("class_type") in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo")]
    if len(r2v) != 1:
        raise ValueError(f"expected one MiniMaxH3*ToVideo node, found {len(r2v)}")
    r2v = r2v[0]
    ids = [int(k) for k in graph if k.isdigit()]
    code_id, attn_id, edit_id = str(max(ids) + 1), str(max(ids) + 2), str(max(ids) + 3)
    inputs = graph[r2v]["inputs"]
    source = [r2v, 0]
    if edits:
        graph[edit_id] = {"class_type": "H3ProbeEditCode", "_meta": {"title": "H3 зонд: правка кода"},
                          "inputs": {"conditioning": [r2v, 0], "clip": inputs["clip"], "prompt": inputs["prompt"],
                                     "edits": edits, "name": name}}
        source = [edit_id, 0]
    for node in graph.values():
        for key, value in node["inputs"].items():
            if value == [r2v, 0] and node.get("class_type") != "H3ProbeEditCode":
                node["inputs"][key] = [code_id, 0]
    graph[code_id] = {"class_type": "H3ProbeCode", "_meta": {"title": "H3 зонд: код энкодера"},
                      "inputs": {"conditioning": source, "clip": inputs["clip"], "prompt": inputs["prompt"],
                                 "name": name}}
    if not attention:
        return graph
    users = [(k, n) for k, n in graph.items() if isinstance(n["inputs"].get("model"), list)
             and re.search(r"Guider|Sampler", n["class_type"])]
    if not users:
        raise ValueError("no guider/sampler with a model input")
    source_model = users[0][1]["inputs"]["model"]
    for _, node in users:
        node["inputs"]["model"] = [attn_id, 0]
    graph[attn_id] = {"class_type": "H3ProbeAttention", "_meta": {"title": "H3 зонд: карта внимания"},
                      "inputs": {"model": source_model, "name": name, "steps": steps, "blocks": blocks,
                                 "queries": queries, "seed": 0}}
    return graph


def save_h3_input(graph: dict, name: str) -> dict:
    """Copy of the graph where everything the sampler takes from the Ref2VA node (the full Qwen code with its extras
    and the starting latent) passes through H3CodeSave → api-data/probes/<name>/h3_input.pt. A later graph can run H3
    from that file without Qwen (H3CodeLoad; bit-identical, docs/reference/H3_ENCODER_RESEARCH.md)."""
    graph = json.loads(json.dumps(graph))
    r2v = [k for k, n in graph.items() if n.get("class_type") in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo")]
    if len(r2v) != 1:
        raise ValueError(f"expected one MiniMaxH3*ToVideo node, found {len(r2v)}")
    r2v = r2v[0]
    save_id = str(max(int(k) for k in graph if k.isdigit()) + 1)
    for node in graph.values():
        for key, value in node["inputs"].items():
            if value in ([r2v, 0], [r2v, 1]):
                node["inputs"][key] = [save_id, value[1]]
    graph[save_id] = {"class_type": "H3CodeSave", "_meta": {"title": "H3: сохранить вход (код+латент)"},
                      "inputs": {"conditioning": [r2v, 0], "latent": [r2v, 1], "name": name}}
    return graph


def attach_to_job(graph: dict, client_job_id: str) -> dict:
    """Probe-all hook used by the GPU API: unchanged graph when the switch is off or the graph is unusual."""
    settings = probe_settings()
    if not settings or not client_job_id:
        return graph
    name = _SAFE.sub("", client_job_id)[:64]
    save_input = settings.pop("save_input", False)
    try:
        graph = insert_probes(graph, name, **settings)
        return save_h3_input(graph, name) if save_input else graph
    except (ValueError, KeyError):
        return graph
