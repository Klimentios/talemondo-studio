"""Read-only FBCache instrumentation; installed beside pinned upstream nodes.py."""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

_LOCK = threading.Lock()


def snapshot(cache, preset, elapsed, completed, node_id):
    def finite(values):
        numbers = sorted(float(v) for v in values if math.isfinite(float(v)))
        return {'count': len(numbers), 'min': numbers[0], 'max': numbers[-1]} if numbers else {'count': 0}
    return {'sampler_node_id': str(node_id), 'preset': preset,
            'config': dataclasses.asdict(cache.config),
            'full_forward_calls': cache.full_steps, 'reused_forward_calls': cache.cached_steps,
            'reused_call_numbers': list(cache.cached_step_numbers),
            'residual_difference': finite(cache.diff_values),
            'temporal_difference': finite(cache.temporal_diff_values),
            'sample_wrapper_wall_seconds': max(0, elapsed), 'completed': bool(completed)}


def append_report(directory, prompt_id, report):
    uid = str(uuid.UUID(prompt_id))
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    path = root / (uid + '.json')
    with _LOCK:
        data = json.loads(path.read_text()) if path.exists() else {'version': 1, 'prompt_id': uid, 'passes': []}
        data['passes'].append(report)
        with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, allow_nan=False)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def record(cache, preset, started, completed):
    # Instrumentation failure cannot change sampling or mask an inference exception.
    try:
        from comfy_execution.utils import get_executing_context
        import folder_paths
        context = get_executing_context()
        if context is None:
            logging.warning('STUDIO cache metrics unavailable: missing execution context')
            return
        report = snapshot(cache, preset, time.monotonic() - started, completed, context.node_id)
        append_report(Path(folder_paths.get_output_directory()) / '.h3-cache-metrics', context.prompt_id, report)
    except Exception:
        logging.exception('STUDIO cache metrics unavailable')


def register_route():
    from aiohttp import web
    import folder_paths
    from server import PromptServer

    @PromptServer.instance.routes.get('/h3/cache-metrics/{prompt_id}')
    async def metrics(request):
        try:
            uid = str(uuid.UUID(request.match_info['prompt_id']))
        except ValueError:
            raise web.HTTPBadRequest()
        path = Path(folder_paths.get_output_directory()) / '.h3-cache-metrics' / (uid + '.json')
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.json_response(json.loads(path.read_text()))
