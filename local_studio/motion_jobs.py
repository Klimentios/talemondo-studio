"""Durable Motion Director receipts, dispatched alongside the other GPU work."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import uuid

import httpx

from .motion_director import GraphPlan, StrictInput, upload_image, upload_video
from pydantic import Field
from .side_jobs import ACTIVE, SideQueueRules
from scripts.h3_graph_validate import validate


class Execute(StrictInput):
    expected_revision: int = Field(ge=1)
    story_approved: bool
    full_run: bool = False  # Ignore the GPU segment cache, e.g. after a server move.


def director_id(scene_id):
    """One node ID per scene: the GPL node keeps its segment cache in a folder named after it."""
    return 'md_' + hashlib.sha256(scene_id.encode()).hexdigest()[:16]


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def execution_metrics(status, plan_json):
    """GPU wall time from ComfyUI's own start/success stamps; per-segment is an average, not a measurement."""
    stamps = {name: data.get('timestamp') for name, data in status.get('messages', [])
              if isinstance(data, dict) and name in {'execution_start', 'execution_success'}}
    plan = json.loads(plan_json)
    seconds = sum(segment['duration'] for segment in plan['segments'])
    metrics = {'segments': len(plan['segments']), 'video_seconds': seconds}
    if isinstance(stamps.get('execution_start'), (int, float)) and isinstance(stamps.get('execution_success'), (int, float)):
        gpu = (stamps['execution_success'] - stamps['execution_start']) / 1000
        metrics.update(gpu_seconds=round(gpu, 1), gpu_seconds_per_video_second=round(gpu / seconds, 1),
                       mean_gpu_seconds_per_segment=round(gpu / len(plan['segments']), 1))
    return metrics


class MotionQueue(SideQueueRules):
    TABLE = 'motion_jobs'
    ID = 'motion_id'
    NOUN = 'сцена Motion Director'

    def __init__(self, store, comfy_url):
        self.store, self.comfy_url = store, comfy_url
        self._init_rules()
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS motion_jobs (
                motion_id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, revision INTEGER NOT NULL,
                plan_json TEXT NOT NULL, manifest_json TEXT NOT NULL, state TEXT NOT NULL,
                remote_job_id TEXT, output_path TEXT, error TEXT, graph_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(scene_id, revision))''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(motion_jobs)')}
            if 'output_sha256' not in columns:
                db.execute('ALTER TABLE motion_jobs ADD COLUMN output_sha256 TEXT')
            if 'metrics_json' not in columns:
                db.execute('ALTER TABLE motion_jobs ADD COLUMN metrics_json TEXT')
            if 'full_run' not in columns:
                db.execute('ALTER TABLE motion_jobs ADD COLUMN full_run INTEGER NOT NULL DEFAULT 0')

    def get(self, uid):
        return self._row(uid)

    def list(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('''SELECT motion_id,scene_id,revision,state,
                remote_job_id,output_path,output_sha256,metrics_json,error,created_at,updated_at FROM motion_jobs ORDER BY created_at DESC,rowid DESC''')]

    def scene_jobs(self, scene_id):
        with self.store.connect() as db:
            rows = [dict(row) for row in db.execute('''SELECT motion_id,scene_id,revision,state,
                remote_job_id,output_path,output_sha256,metrics_json,error,created_at,updated_at FROM motion_jobs
                WHERE scene_id=? ORDER BY created_at DESC,rowid DESC''', (scene_id,))]
        return [{**row, 'file_url': f"/api/studio/motion-director/jobs/{row['motion_id']}/file"
                 if row['state'] == 'completed' else None} for row in rows]

    def create(self, scene_id, revision, plan, full_run=False):
        plan = GraphPlan.model_validate(plan)
        plan.compile()
        manifest = {}
        for segment in plan.segments:
            media = [(ref.asset_id, 'image') for ref in segment.pictures]
            if segment.source_video:
                if not segment.source_video.asset_id:
                    raise ValueError('Для исполнения исходное видео должно иметь asset_id библиотеки')
                media.append((segment.source_video.asset_id, 'video'))
            for uid, kind in media:
                asset = self.store.get_asset(uid)
                if not asset or asset['kind'] != kind:
                    raise ValueError('Медиа сегмента отсутствует в библиотеке')
                path = Path(asset['path'])
                if not path.is_file() or not path.resolve().is_relative_to(self.store.assets_dir.resolve()):
                    raise ValueError('Медиа сегмента недоступно')
                digest = file_hash(path)
                if digest != asset['sha256']:
                    raise ValueError('Медиа изменилось после импорта')
                manifest[uid] = {'kind': kind, 'sha256': digest}
        serialized = plan.model_dump_json()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM motion_jobs WHERE scene_id=? AND revision=?', (scene_id, revision)).fetchone()
            if old:
                if old['plan_json'] != serialized or json.loads(old['manifest_json']) != manifest:
                    raise ValueError('Для этой ревизии уже существует другое задание')
                return dict(old)
            if db.execute("SELECT 1 FROM motion_jobs WHERE scene_id=? AND state NOT IN ('completed','failed','cancelled')", (scene_id,)).fetchone():
                raise ValueError('Сцена уже выполняется или требует сверки')
            uid = str(uuid.uuid4())
            db.execute("INSERT INTO motion_jobs(motion_id,scene_id,revision,plan_json,manifest_json,state,full_run) VALUES (?,?,?,?,?,'queued_local',?)",
                       (uid, scene_id, revision, serialized, json.dumps(manifest), int(full_run)))
        return self.get(uid)

    def update(self, uid, state, **fields):
        if state not in ACTIVE | {'completed', 'failed'} or fields.keys() - {'remote_job_id', 'output_path', 'output_sha256', 'error', 'graph_json', 'metrics_json'}:
            raise ValueError('Некорректное состояние задания')
        values = {'state': state, **fields}
        with self.store.connect() as db:
            db.execute(f"UPDATE motion_jobs SET {','.join(key+'=?' for key in values)},updated_at=CURRENT_TIMESTAMP "
                       "WHERE motion_id=? AND state NOT IN ('completed','failed','cancelled')", (*values.values(), uid))

    def previous_plan(self, scene_id, uid):
        """The scene's last finished generation: its segments are what the GPU cache holds."""
        with self.store.connect() as db:
            row = db.execute("SELECT plan_json FROM motion_jobs WHERE scene_id=? AND motion_id<>? AND state='completed' "
                             "ORDER BY updated_at DESC,rowid DESC LIMIT 1", (scene_id, uid)).fetchone()
        return GraphPlan.model_validate_json(row['plan_json']) if row else None

    async def cached_on_gpu(self, client, scene_id, indices):
        """HEAD the node's own cache files (video + audio) for segments that will not be sampled."""
        for index in sorted(indices):
            for name in (f'seg_{index:04d}.pt', f'seg_{index:04d}.audio.pt'):
                response = await client.head(f'{self.comfy_url}/view', params={
                    'filename': name, 'subfolder': f'minimax_seg_cache/{director_id(scene_id)}', 'type': 'output'})
                if response.status_code != 200:
                    return False
        return True

    def claim(self, uid):
        with self.store.connect() as db:
            return db.execute("UPDATE motion_jobs SET state='uploading',updated_at=CURRENT_TIMESTAMP "
                              "WHERE motion_id=? AND state='queued_local'", (uid,)).rowcount == 1

    def cancel_waiting(self, scene_id, uid):
        """Only cancel before dispatch owns the receipt; never interrupt shared ComfyUI."""
        with self.store.connect() as db:
            row = db.execute('SELECT scene_id,state FROM motion_jobs WHERE motion_id=?', (uid,)).fetchone()
            if not row or row['scene_id'] != scene_id:
                raise LookupError('Задание этой сцены не найдено')
            changed = db.execute("UPDATE motion_jobs SET state='cancelled',error='Ожидание отменено пользователем',"
                                 "updated_at=CURRENT_TIMESTAMP WHERE motion_id=? AND scene_id=? AND state='queued_local'",
                                 (uid, scene_id)).rowcount
            if not changed:
                raise ValueError('Задание уже передаётся, выполняется или завершено; отмена ожидания недоступна')
        return next(row for row in self.scene_jobs(scene_id) if row['motion_id'] == uid)

    async def _lookup(self, uid):
        async with httpx.AsyncClient(timeout=30) as client:
            history = await client.get(f'{self.comfy_url}/history')
            history.raise_for_status()
            queue = await client.get(f'{self.comfy_url}/queue')
            queue.raise_for_status()
        candidates = [entry.get('prompt') for entry in history.json().values() if isinstance(entry, dict)]
        data = queue.json()
        candidates += data.get('queue_running', []) + data.get('queue_pending', [])
        found = {item[1] for item in candidates if isinstance(item, list) and len(item) > 3
                 and isinstance(item[3], dict) and item[3].get('studio_motion_id') == uid}
        if len(found) > 1:
            raise ValueError('Найдено несколько удалённых заданий для одной квитанции')
        return {'job_id': found.pop()} if found else None

    async def run_once(self):
        job = self.first_active()
        if not job or not self.comfy_url:
            return False
        uid, state = job['motion_id'], job['state']
        if state == 'uploading':
            # Crash could have occurred after POST /prompt; never resend blindly.
            self.park(uid, 'Восстановление после прерывания; сверка ComfyUI')
            return True
        if state == 'queued_local':
            plan = GraphPlan.model_validate_json(job['plan_json'])
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.get(f'{self.comfy_url}/queue')
                    response.raise_for_status()
                    queue = response.json()
                    if not isinstance(queue, dict) or not all(isinstance(queue.get(key), list) for key in ('queue_running', 'queue_pending')):
                        raise ValueError('ComfyUI вернул некорректную очередь')
                    if queue['queue_running'] or queue['queue_pending']:
                        return False
                    if not self.claim(uid):
                        return False
                    uploaded = {}
                    for asset_id, entry in json.loads(job['manifest_json']).items():
                        asset = self.store.get_asset(asset_id)
                        if not asset or await asyncio.to_thread(file_hash, Path(asset['path'])) != entry['sha256']:
                            raise ValueError('Медиа изменилось после постановки задания')
                        transfer = upload_image if entry['kind'] == 'image' else upload_video
                        uploaded[asset_id] = await transfer(self.store, client, self.comfy_url, asset_id)
                    for segment in plan.segments:
                        for ref in segment.pictures:
                            ref.image_file = uploaded[ref.asset_id]['image_file']
                        if segment.source_video:
                            segment.source_video.video_file = uploaded[segment.source_video.asset_id]['video_file']
                    selection = None if job['full_run'] else plan.run_selection(self.previous_plan(job['scene_id'], uid))
                    if selection and not await self.cached_on_gpu(client, job['scene_id'], set(range(len(plan.segments))) - set(selection)):
                        selection = None  # Server moved or cache cleared: regenerate rather than fail mid-timeline.
                    graph = plan.graph(director_id=director_id(job['scene_id']), run_selection=selection)['graph']
                    graph['7']['inputs']['filename_prefix'] = f'video/Studio_Motion_{uid}'
                    schema = await client.get(f'{self.comfy_url}/object_info')
                    schema.raise_for_status()
                    problems = validate(graph, schema.json())
                    if problems:
                        raise ValueError('; '.join(problems)[:2000])
                    self.update(uid, 'uploading', graph_json=json.dumps(graph),
                                metrics_json=json.dumps({'run_selection': [plan.segments[i].id for i in selection] if selection else 'all'}))
                    if self.get(uid)['state'] != 'uploading':
                        return True
                    # Persisted graph + marker precede the only generation POST.
                    try:
                        response = await client.post(f'{self.comfy_url}/prompt', json={
                            'prompt': graph, 'client_id': uid, 'extra_data': {'studio_motion_id': uid}})
                        if response.status_code == 400:
                            self.update(uid, 'failed', error='ComfyUI отклонил граф: ' + response.text[:1000])
                            return True
                        response.raise_for_status()
                        remote = response.json()['prompt_id']
                        if not isinstance(remote, str) or not remote:
                            raise ValueError('Пустая квитанция ComfyUI')
                    except Exception as exc:
                        self.park(uid, f'Ответ на отправку потерян; автоповтор отключён: {type(exc).__name__}')
                        return True
                    self.update(uid, 'queued_gpu', remote_job_id=remote, error='')
            except ValueError as exc:
                self.update(uid, 'failed', error=str(exc))
            except httpx.HTTPError:
                # This branch is before generation POST, safe to retry media preparation.
                self.update(uid, 'queued_local', error='ComfyUI недоступен при подготовке')
                return False
            return True
        if state in {'queued_gpu', 'running', 'downloading'}:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(f"{self.comfy_url}/history/{job['remote_job_id']}")
                response.raise_for_status()
                result = response.json().get(job['remote_job_id'])
                if not result:
                    response = await client.get(f'{self.comfy_url}/queue')
                    response.raise_for_status()
                    remote_queue = response.json()
                    items = remote_queue['queue_running'] + remote_queue['queue_pending']
                    if not any(isinstance(item, list) and len(item) > 1 and item[1] == job['remote_job_id'] for item in items):
                        self.park(uid, 'Задание отсутствует в очереди и истории ComfyUI; требуется сверка')
                    else:
                        self.update(uid, 'running' if any(item[1] == job['remote_job_id'] for item in remote_queue['queue_running']) else 'queued_gpu')
                    return False
                status = result.get('status', {})
                if status.get('status_str') == 'error':
                    self.update(uid, 'failed', error='Ошибка исполнения Motion Director: ' + str(status.get('messages', ''))[:1500])
                    return True
                if not status.get('completed'):
                    return False
                output = result.get('outputs', {}).get('7', {})
                files = output.get('images', []) + output.get('videos', [])
                videos = [item for item in files if isinstance(item, dict) and str(item.get('filename', '')).lower().endswith('.mp4') and item.get('type') == 'output']
                if len(videos) != 1:
                    self.update(uid, 'failed', error='ComfyUI не вернул один итоговый MP4')
                    return True
                metrics = {**json.loads(job.get('metrics_json') or '{}'), **execution_metrics(status, job['plan_json'])}
                from app.execution_evidence import history_evidence
                observation = history_evidence(job['remote_job_id'], result)
                if observation is not None:
                    try:
                        context_response = await client.get(f"{self.comfy_url}/h3/studio/execution/{job['remote_job_id']}", timeout=3)
                        context_response.raise_for_status()
                        context = context_response.json()
                        if isinstance(context, dict):
                            from app.execution_evidence import merge_prompt_context
                            observation = merge_prompt_context(observation, context)
                    except (httpx.HTTPError, ValueError):
                        pass
                    metrics['execution_observation'] = observation
                self.update(uid, 'downloading', metrics_json=json.dumps(metrics))
                destination = self.store.results_dir / f'motion-{uid}.mp4'
                temporary = destination.with_name('.' + destination.name + '.part')
                try:
                    async with client.stream('GET', f'{self.comfy_url}/view', params={key: videos[0][key] for key in ('filename', 'subfolder', 'type') if key in videos[0]}) as media:
                        media.raise_for_status()
                        with temporary.open('wb') as target:
                            async for block in media.aiter_bytes():
                                target.write(block)
                    from .audio_jobs import _stream_kinds
                    if 'video' not in await asyncio.to_thread(_stream_kinds, temporary):
                        raise ValueError('Скачанный результат не содержит видеопотока')
                    digest = await asyncio.to_thread(file_hash, temporary)
                    temporary.replace(destination)
                    self.update(uid, 'completed', output_path=str(destination), output_sha256=digest, error='')
                except Exception as exc:
                    temporary.unlink(missing_ok=True)
                    error = self.download_failed(uid, exc)
                    if error:
                        self.update(uid, 'failed', error=error)
                    return False
                return True
        return False


def make_router(ctx):
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import FileResponse
    from .scene_runs import SCENE_ID

    router = APIRouter(prefix='/api/studio/motion-director')

    @router.post('/scenes/{scene_id}/execute', status_code=202)
    async def execute(scene_id: str, payload: Execute):
        if not SCENE_ID.fullmatch(scene_id):
            raise HTTPException(422, 'Некорректный ID сцены')
        if payload.story_approved is not True:
            raise HTTPException(422, 'Требуется одобренный сюжет или явный запрос на этот рендер')
        path = ctx.data_root / 'scene-runs' / 'motion-plans' / f'{scene_id}.json'
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise HTTPException(404, 'План сцены не сохранён')
        if record['revision'] != payload.expected_revision:
            raise HTTPException(409, 'План изменён; прочитайте текущую ревизию')
        try:
            return await asyncio.to_thread(ctx.motion_queue.create, scene_id, record['revision'], record['plan'], payload.full_run)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get('/scenes/{scene_id}/jobs')
    async def scene_jobs(scene_id: str):
        return ctx.motion_queue.scene_jobs(scene_id)

    @router.post('/scenes/{scene_id}/jobs/{uid}/cancel-waiting')
    async def cancel_waiting(scene_id: str, uid: str):
        try:
            return ctx.motion_queue.cancel_waiting(scene_id, uid)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('/jobs/{uid}/file')
    async def file(uid: str):
        job = ctx.motion_queue.get(uid)
        if not job or job['state'] != 'completed' or not job['output_path']:
            raise HTTPException(404, 'Результат ещё не готов')
        path = Path(job['output_path'])
        if not path.is_file() or not path.resolve().is_relative_to(ctx.store.results_dir.resolve()):
            raise HTTPException(404, 'Результат отсутствует')
        if job.get('output_sha256') and await asyncio.to_thread(file_hash, path) != job['output_sha256']:
            raise HTTPException(409, 'Файл результата изменён после скачивания; требуется новая проверка QC')
        return FileResponse(path, media_type='video/mp4', filename=path.name)

    return router
