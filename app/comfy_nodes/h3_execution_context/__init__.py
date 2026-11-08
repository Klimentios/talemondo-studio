"""Optional prompt observer installed beside the graph bridge; adds no Comfy nodes."""
import json
import logging
import os
import time
import uuid
from pathlib import Path

from .runtime import capture_context

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}


def context_dir():
    import folder_paths
    return Path(folder_paths.get_output_directory()) / '.h3-execution-context'


def capture_prompt(data):
    try:
        if not isinstance(data, dict) or not isinstance(data.get('prompt'), dict):
            return data
        import folder_paths
        import nodes
        if data.get('prompt_id') is None:
            data['prompt_id'] = str(uuid.uuid4())
        uid = str(uuid.UUID(str(data['prompt_id'])))
        if uid != data['prompt_id']:
            return data
        root = Path(os.environ.get('H3_ROOT', str(Path(folder_paths.base_path).parent)))
        index = root / 'verified-weights.json'
        inventory = json.loads(index.read_text()) if index.is_file() else {}
        manifest = capture_context(data['prompt'], root, inventory, nodes.NODE_CLASS_MAPPINGS,
                                   folder_paths.get_full_path)
        manifest.update(prompt_id=uid, observed_at=time.time())
        directory = context_dir()
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f'.{uid}.tmp'
        tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        tmp.replace(directory / f'{uid}.json')
    except Exception:
        logging.exception('H3 execution context unavailable; prompt is unchanged')
    return data


def register():
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    server.add_on_prompt_handler(capture_prompt)

    @server.routes.get('/h3/studio/execution/{prompt_id}')
    async def read_context(request):
        given = request.match_info['prompt_id']
        try:
            uid = str(uuid.UUID(given))
        except ValueError:
            raise web.HTTPBadRequest(text='Invalid prompt id')
        if uid != given:
            raise web.HTTPBadRequest(text='Noncanonical prompt id')
        path = context_dir() / f'{uid}.json'
        if not path.is_file():
            raise web.HTTPNotFound(text='Execution context not recorded')
        return web.json_response(json.loads(path.read_text()))


try:
    register()
except ImportError:
    pass
