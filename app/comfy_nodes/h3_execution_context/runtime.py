"""Prompt-time context only: no weight reads, graph edits or inference."""
from __future__ import annotations

import hashlib
import json
import marshal
import platform
import sys
from pathlib import Path

try:
    from .inventory import check_weight
except ImportError:
    from app.model_inventory import check_weight

CATEGORIES = {'unet_name': ('diffusion_models', 'unet'), 'clip_name': ('text_encoders', 'clip'),
              'vae_name': ('vae',), 'lora_name': ('loras',), 'ckpt_name': ('checkpoints',)}
EXTENSIONS = ('.safetensors', '.ckpt', '.pt', '.pth', '.bin', '.gguf')


def graph_digest(graph):
    return hashlib.sha256(json.dumps(graph, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def loaded_methods(cls):
    methods = {}
    if cls is None:
        return methods
    for name, value in vars(cls).items():
        value = getattr(value, '__func__', value)
        code = getattr(value, '__code__', None)
        if code is not None:
            methods[name] = hashlib.sha256(marshal.dumps(code)).hexdigest()
    return methods


def capture_context(graph, root: Path, inventory: dict, classes: dict, resolve) -> dict:
    root = root.resolve()
    weights = []
    types = sorted({node.get('class_type') for node in graph.values()
                    if isinstance(node, dict) and isinstance(node.get('class_type'), str)})
    for uid, node in graph.items():
        if not isinstance(node, dict):
            continue
        for key, value in (node.get('inputs') or {}).items():
            if not isinstance(value, str) or not value.lower().endswith(EXTENSIONS):
                continue
            candidates = set()
            for category in CATEGORIES.get(key, ()):
                try:
                    found = resolve(category, value)
                except (KeyError, ValueError, OSError):
                    continue
                if found:
                    candidates.add(str(Path(found).resolve()))
            record = {'node_id': uid, 'input': key, 'name': value, 'state': 'unknown', 'sha256': None}
            if len(candidates) == 1:
                target = Path(next(iter(candidates)))
                if target.is_relative_to(root):
                    resolved = str(target.relative_to(root))
                    original = next((relative for relative, entry in inventory.get('entries', {}).items()
                                     if entry.get('resolved') == resolved), None)
                    if original:
                        record.update(check_weight(root, inventory, original))
                    else:
                        record['reason'] = 'not_attested'
                else:
                    record['reason'] = 'outside_inventory_root'
            else:
                record['reason'] = 'unresolved_or_ambiguous_weight'
            weights.append(record)
    return {'schema_version': 1, 'stage': 'prompt_received', 'graph_sha256': graph_digest(graph),
            'python_version': platform.python_version(),
            'pytorch_version': getattr(sys.modules.get('torch'), '__version__', None),
            'node_methods_sha256': {name: loaded_methods(classes.get(name)) for name in types},
            'node_scope': 'loaded_class_methods_only', 'weights': weights,
            'weights_scope': 'disk_attestation_metadata_check',
            'environment_complete': False}
