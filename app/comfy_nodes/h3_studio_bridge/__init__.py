"""Studio bridge installed by scripts/install_h3_h200_overlay.sh into custom_nodes/h3-studio-bridge.

Every graph POSTed to /prompt (GPU API and Motion Director alike) is kept by its prompt id, and the
editor opens one with ?h3_job=<prompt id> — Studio's «Открыть в ComfyUI». «Отправить в Studio» keeps
the edited graph under edited/<edit id>; Studio lists it and the GPU API renders it (/api/graph-jobs).
«⇪ Воркфлоу в Studio» keeps any open graph under workflows/<id>; Studio imports it as «Свой воркфлоу».
Adds no nodes and does not change any graph: a missing prompt_id gets the canonical UUID ComfyUI
would mint itself.
"""
import json
import logging
import re
import time
import uuid
from pathlib import Path

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

KEEP_GRAPHS = 2000
_saved = 0


def graphs_dir() -> Path:
    import folder_paths
    return Path(folder_paths.get_output_directory()) / '.h3-graphs'


def canonical_id(value) -> str | None:
    try:
        uid = str(uuid.UUID(str(value)))
    except ValueError:
        return None
    return uid if uid == value else None


def save_graph(json_data, folder: Path | None = None):
    """on_prompt handler: must hand json_data back whatever happens."""
    global _saved
    try:
        graph = json_data.get('prompt') if isinstance(json_data, dict) else None
        if not isinstance(graph, dict):
            return json_data
        if json_data.get('prompt_id') is None:
            json_data['prompt_id'] = str(uuid.uuid4())
        uid = canonical_id(json_data['prompt_id'])
        if uid is None:  # ComfyUI refuses it with 400 anyway
            return json_data
        folder = folder or graphs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f'.{uid}.tmp'
        tmp.write_text(json.dumps({'prompt': graph, 'saved_at': time.time()}))
        tmp.replace(folder / f'{uid}.json')
        _saved += 1
        if _saved % 50 == 1:
            prune(folder)
    except Exception:
        logging.exception('STUDIO Studio bridge: graph not kept')
    return json_data


def prune(folder: Path, keep: int = KEEP_GRAPHS) -> None:
    files = sorted(folder.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


def find_graph(uid: str, folder: Path, prompt_queue=None) -> dict | None:
    """Kept file first; else ComfyUI's own queue/history (graphs queued before the bridge)."""
    path = folder / f'{uid}.json'
    if path.is_file():
        return {'prompt': json.loads(path.read_text())['prompt'], 'source': 'file'}
    if prompt_queue is None:
        return None
    entry = prompt_queue.get_history(prompt_id=uid).get(uid)
    if entry and len(entry.get('prompt') or ()) > 2:
        return {'prompt': entry['prompt'][2], 'source': 'history'}
    running, pending = prompt_queue.get_current_queue_volatile()
    for item in list(running) + list(pending):
        if len(item) > 2 and item[1] == uid:
            return {'prompt': item[2], 'source': 'queue'}
    return None


def save_edit(source_id: str, graph, folder: Path) -> str:
    if canonical_id(source_id) is None:
        raise ValueError('source_prompt_id must be a canonical UUID')
    if not isinstance(graph, dict) or not graph or not all(
            isinstance(node, dict) and isinstance(node.get('class_type'), str) for node in graph.values()):
        raise ValueError('prompt must be an API-format graph')
    edit_id = str(uuid.uuid4())
    edits = folder / 'edited'
    edits.mkdir(parents=True, exist_ok=True)
    tmp = edits / f'.{edit_id}.tmp'
    tmp.write_text(json.dumps({'source_prompt_id': source_id, 'prompt': graph, 'saved_at': time.time()}))
    tmp.replace(edits / f'{edit_id}.json')
    prune(edits, keep=200)
    return edit_id


def list_edits(source_id: str, folder: Path) -> list[dict]:
    items = []
    for path in (folder / 'edited').glob('*.json'):
        data = json.loads(path.read_text())
        if data.get('source_prompt_id') == source_id:
            items.append({'edit_id': path.stem, 'saved_at': data.get('saved_at')})
    return sorted(items, key=lambda item: item['saved_at'] or 0, reverse=True)[:20]


def read_edit(edit_id: str, folder: Path) -> dict | None:
    uid = canonical_id(edit_id)
    path = folder / 'edited' / f'{uid}.json'
    return json.loads(path.read_text()) if uid and path.is_file() else None


def save_workflow(name, graph, folder: Path, studio_id=None, origin=None) -> str:
    """«⇪ Воркфлоу в Studio»: any graph of the editor, for Studio's «Свои воркфлоу» to import.
    origin='studio' marks a copy Studio put here to open in the editor; Studio's inbox hides it."""
    if not isinstance(graph, dict) or not graph or not all(
            isinstance(node, dict) and isinstance(node.get('class_type'), str) for node in graph.values()):
        raise ValueError('prompt must be an API-format graph')
    if studio_id is not None and not re.fullmatch(r'wf_[0-9a-f]{12}', str(studio_id)):
        raise ValueError('studio_id must be a Studio workflow ID')
    if origin not in (None, 'studio'):
        raise ValueError('origin must be studio or absent')
    item_id = str(uuid.uuid4())
    target = folder / 'workflows'
    target.mkdir(parents=True, exist_ok=True)
    tmp = target / f'.{item_id}.tmp'
    tmp.write_text(json.dumps({'name': str(name or 'Воркфлоу из ComfyUI')[:120], 'prompt': graph, 'saved_at': time.time(),
                               **({'studio_id': studio_id} if studio_id else {}),
                               **({'origin': origin} if origin else {})}))
    tmp.replace(target / f'{item_id}.json')
    prune(target, keep=100)
    return item_id


def list_workflows(folder: Path) -> list[dict]:
    items = []
    for path in (folder / 'workflows').glob('*.json'):
        data = json.loads(path.read_text())
        items.append({'id': path.stem, 'name': data.get('name'), 'saved_at': data.get('saved_at'),
                      'nodes': len(data.get('prompt') or {}), 'studio_id': data.get('studio_id'),
                      'origin': data.get('origin')})
    return sorted(items, key=lambda item: item['saved_at'] or 0, reverse=True)[:50]


def read_workflow(item_id: str, folder: Path) -> dict | None:
    uid = canonical_id(item_id)
    path = folder / 'workflows' / f'{uid}.json'
    return json.loads(path.read_text()) if uid and path.is_file() else None


def register() -> None:
    from aiohttp import web
    from server import PromptServer

    server = PromptServer.instance
    server.add_on_prompt_handler(save_graph)

    @server.routes.get('/h3/studio/graph/{prompt_id}')
    async def graph(request):
        uid = canonical_id(request.match_info['prompt_id'])
        if uid is None:
            raise web.HTTPBadRequest(text='prompt_id must be a canonical UUID')
        found = find_graph(uid, graphs_dir(), server.prompt_queue)
        if found is None:
            raise web.HTTPNotFound(text='Graph is not in this ComfyUI')
        return web.json_response(found)

    @server.routes.post('/h3/studio/edited')
    async def send_edit(request):
        body = await request.json()
        try:
            edit_id = save_edit(body.get('source_prompt_id'), body.get('prompt'), graphs_dir())
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({'edit_id': edit_id})

    @server.routes.get('/h3/studio/edited')
    async def edits_of(request):
        source = canonical_id(request.query.get('source', ''))
        if source is None:
            raise web.HTTPBadRequest(text='source must be a canonical UUID')
        return web.json_response({'edits': list_edits(source, graphs_dir())})

    @server.routes.get('/h3/studio/edited/{edit_id}')
    async def edit(request):
        found = read_edit(request.match_info['edit_id'], graphs_dir())
        if found is None:
            raise web.HTTPNotFound(text='Edit is not in this ComfyUI')
        return web.json_response(found)

    @server.routes.post('/h3/studio/workflows')
    async def send_workflow(request):
        body = await request.json()
        try:
            item_id = save_workflow(body.get('name'), body.get('prompt'), graphs_dir(), body.get('studio_id'),
                                    body.get('origin'))
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({'id': item_id})

    @server.routes.get('/h3/studio/workflows')
    async def workflows(request):
        return web.json_response({'items': list_workflows(graphs_dir())})

    @server.routes.get('/h3/studio/workflows/{item_id}')
    async def workflow(request):
        found = read_workflow(request.match_info['item_id'], graphs_dir())
        if found is None:
            raise web.HTTPNotFound(text='Workflow is not in this ComfyUI')
        return web.json_response(found)


try:
    register()
except ImportError:  # imported outside ComfyUI (tests)
    pass
