"""Evidence observed from ComfyUI history, distinct from prepared graph metadata."""
from __future__ import annotations

from app.fingerprints import json_sha256

RUNTIME_FIELDS = ('os', 'python_version', 'pytorch_version', 'comfyui_version')


def history_evidence(prompt_id: str, entry: dict, stats: dict | None = None) -> dict | None:
    envelope = entry.get('prompt')
    if not isinstance(envelope, list) or len(envelope) < 3 or envelope[1] != prompt_id:
        return None
    graph = envelope[2]
    if not isinstance(graph, dict) or not graph:
        return None
    status = entry.get('status') or {}
    runtime = (stats or {}).get('system') or {}
    if not isinstance(runtime, dict):
        runtime = {}
    versions = {key: runtime[key] for key in RUNTIME_FIELDS if isinstance(runtime.get(key), str)}
    timestamps = {message[0]: message[1]['timestamp']
                  for message in status.get('messages', [])
                  if isinstance(message, list) and len(message) == 2 and isinstance(message[1], dict)
                  and message[0] in {'execution_start', 'execution_success', 'execution_interrupted'}
                  and isinstance(message[1].get('timestamp'), (int, float))}
    return {'schema_version': 1, 'source': 'comfyui_history', 'prompt_id': prompt_id,
            'stage': 'history_observed', 'graph_sha256': json_sha256(graph),
            'node_types': sorted({node['class_type'] for node in graph.values()
                                  if isinstance(node, dict) and isinstance(node.get('class_type'), str)}),
            'status': status.get('status_str'), 'timestamps': timestamps,
            'runtime_observed': versions or None,
            'runtime_scope': 'server_report_at_observation',
            'weights_sha256': None, 'custom_node_versions': None}


def merge_prompt_context(evidence: dict, context: dict) -> dict:
    matches = (context.get('schema_version') == 1 and context.get('stage') == 'prompt_received'
               and context.get('prompt_id') == evidence.get('prompt_id')
               and context.get('graph_sha256') == evidence.get('graph_sha256'))
    return {**evidence, 'prompt_context_matches': matches,
            'prompt_context': context if matches else None}
