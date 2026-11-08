"""Mac-owned Context-IR preparation through a subscription agent; legacy remote reconciliation."""
from __future__ import annotations
import asyncio
import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import Field
from app.context_ir.schema import Strict, Snapshot, ScenePlan, VERSION, digest, compile_plan
from app.context_ir.store import Receipts, TERMINAL
from app.contract import director_schema_sha256, GenerationRequest, DEFAULTS, default_megapixels
from app.director_config import normalize_director_config
from app.camera_optics import normalize_camera_optics
from app.camera_guide_vendor.camera import parse_state
from app.h3_ir import is_blocking
from .scenarist import preview_jpeg
from .worker import sha256_file
from .store import SETTINGS_KEYS, _media_duration_seconds
from .context_ir_media import video_frames
from .reference_preparation import prepared_evidence
from . import context_ir_agent
from .agent_brain import BrainError


class InputReference(Strict):
    asset_id: str
    kind: str = 'image'
    role: str
    owner: str = ''
    performer: str = ''
    number: int


class Input(Strict):
    client_request_id: uuid.UUID
    brief: str = Field(min_length=3, max_length=16000)
    references: list[InputReference] = Field(min_length=1, max_length=12)
    settings: dict = Field(default_factory=dict)
    shots: int = Field(default=1, ge=1, le=6)
    required_lines: list[str] = Field(default_factory=list, max_length=20)
    preserve: str = Field(default='', max_length=4000)
    change: str = Field(default='', max_length=4000)
    variation_id: str = Field(default='', max_length=100)


class RenderInput(Strict):
    client_request_id: uuid.UUID
    seed: int | None = Field(default=None, ge=0, le=2**63-1)


def public(row):
    value = {k: v for k, v in row.items() if k != 'payload'}
    # Transport previews are internal, never echoed into polling responses.
    if (value.get('result') or {}).get('wire'):
        value['result'] = None
    return value


class ContextQueue:
    def __init__(self, studio, gpu_url):
        self.studio, self.gpu_url = studio, gpu_url
        self.db = Receipts(studio.root / 'context-ir.sqlite3')

    def snapshot(self, request: Input):
        settings = copy.deepcopy(request.settings)
        if set(settings) - SETTINGS_KEYS:
            raise ValueError('Неизвестные настройки Context-IR')
        if len(json.dumps(settings, ensure_ascii=False)) > 20000:
            raise ValueError('Слишком большой снимок настроек')
        if settings.get('context_ir'):
            raise ValueError('Новый снимок не должен ссылаться на старый артефакт')
        settings = {**DEFAULTS, **settings}
        quality = settings.get('quality', DEFAULTS['quality'])
        GenerationRequest(request.brief, settings.get('duration', 10),
            settings.get('aspect_ratio', '9:16'), quality,
            steps=settings['steps'], seed=settings.get('seed'),
            megapixels=default_megapixels(quality, settings.get('megapixels')),
            ref_image_size=settings['ref_image_size'])
        if settings.get('director_config'):
            settings['director_config'] = normalize_director_config(settings['director_config'])
        if settings.get('camera_state'):
            try:
                parse_state(settings['camera_state'])
            except (KeyError, TypeError, AttributeError) as exc:
                raise ValueError('Некорректная траектория камеры') from exc
        normalize_camera_optics(settings.get('camera_optics'), settings.get('camera_state'))
        if settings.get('character_refinement'):
            raise ValueError('Постобработка героев запускается через отдельный инструмент')
        if settings.get('mode') == 'replace' and any(r.kind == 'image' for r in request.references):
            videos = [r for r in request.references if r.kind == 'video']
            if videos and (len(videos) != 1 or videos[0].role == 'Движение'):
                raise ValueError('В режиме замены героя нужен один ролик с ролью «Движение и камера» или «Исходное видео». Для переноса только движения выбери обычный визуальный режим.')
        refs = []
        for ref in request.references:
            asset = self.studio.get_asset(ref.asset_id)
            if not asset or asset['kind'] != ref.kind or ref.kind not in {'image', 'video'}:
                raise ValueError('Context-IR принимает фото и видео из медиатеки')
            path = Path(asset['path'])
            if not path.resolve().is_relative_to(self.studio.assets_dir.resolve()) or not path.is_file():
                raise ValueError('Референс отсутствует в медиатеке')
            h = sha256_file(path)
            if h != asset['sha256']:
                raise ValueError('Файл изменился относительно медиатеки')
            duration = _media_duration_seconds(path) if ref.kind == 'video' else None
            refs.append({**ref.model_dump(), 'sha256': h, 'duration_seconds': duration})
        return Snapshot(brief=request.brief, references=refs, settings=settings,
                        duration=settings.get('duration', 10), shots=request.shots,
                        required_lines=request.required_lines, preserve=request.preserve, change=request.change,
                        variation_id=request.variation_id)

    def verify_files(self, snapshot):
        paths = {}
        for ref in snapshot.references:
            asset = self.studio.get_asset(ref.asset_id)
            if not asset:
                raise ValueError('Материал удалён из медиатеки')
            path = Path(asset['path'])
            if not path.resolve().is_relative_to(self.studio.assets_dir.resolve()) or not path.is_file() or sha256_file(path) != ref.sha256:
                raise ValueError('Материал изменён или недоступен: подготовь новый контекст')
            paths[ref.asset_id] = path
        return paths

    def first_active(self):
        rows = [r for r in self.db.active() if r['state'] != 'needs_reconciliation']
        return next((r for r in rows if r['state'] in {'running', 'uploading'}), rows[0] if rows else None)

    async def run_once(self):
        row = self.first_active()
        if not row:
            return
        key = row['id']
        try:
            snapshot = Snapshot.model_validate(row['payload'])
            local = (row.get('result') or {}).get('backend') == 'subscription_agent'
            if local and row['state'] != 'queued_local':
                self.db.update(key, 'needs_reconciliation', result=row['result'],
                    error='Локальная подготовка прервана; повтор автоматически не запускается',
                    expected={'uploading', 'running'})
                return
            if row['state'] == 'queued_local' and not (row.get('result') or {}).get('wire'):
                await self.run_agent(row, snapshot)
                return
            async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5)) as client:
                if row['state'] != 'queued_local':
                    await self.poll(client, row)
                    return
                paths = await asyncio.to_thread(self.verify_files, snapshot)
                response = await client.get(self.gpu_url + '/api/capabilities')
                response.raise_for_status()
                cap = response.json().get('context_ir_v1')
                if not cap or cap.get('version') != VERSION or cap.get('identity', {}).get('schema_hash') != director_schema_sha256():
                    raise ValueError('GPU не поддерживает совместимый Context-IR; требуется обновление сервера')
                cache_key = digest([snapshot.cache_input(), cap['fingerprint']])
                cached = self.db.cached(cache_key)
                if cached:
                    self.validate_result(snapshot, cached)
                    self.db.update(key, 'ready', result={**cached, 'cache_hit': True}, expected={'queued_local'})
                    return
                wire = (row.get('result') or {}).get('wire')
                if not wire:
                    images, videos = {}, {}
                    for ref in snapshot.references:
                        asset_id, path = ref.asset_id, paths[ref.asset_id]
                        if ref.kind == 'video':
                            videos[asset_id] = await asyncio.to_thread(prepared_evidence, self.studio.root, path, ref.sha256,
                                'video', ref.duration_seconds, lambda: video_frames(path, ref.duration_seconds))
                            continue
                        image = await asyncio.to_thread(prepared_evidence, self.studio.root, path, ref.sha256,
                            'image', None, lambda: preview_jpeg(path))
                        if not image:
                            raise ValueError('Не удалось прочитать фотографию ' + asset_id)
                        images[asset_id] = image
                    wire = {'client_request_id': key, 'snapshot': snapshot.model_dump(),
                            'images': images, 'videos': videos, 'fingerprint': cap['fingerprint']}
                if self.studio.queue_paused():
                    self.db.update(key, 'queued_local', result={'wire': wire}, expected={'queued_local'})
                    return
                if not self.db.update(key, 'uploading', result={'wire': wire}, expected={'queued_local'}):
                    return
                response = await client.post(self.gpu_url + '/api/context-ir/jobs', json=wire)
                response.raise_for_status()
                self.consume(key, snapshot, response.json(), wire)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {400, 404, 405, 409, 413, 422}:
                self.db.update(key, 'failed', error='Context-IR API: ' + exc.response.text[:1000],
                               expected={'queued_local', 'uploading'})
            else:
                self.uncertain(row, str(exc))
        except httpx.HTTPError as exc:
            self.uncertain(row, str(exc))
        except (ValueError, OSError) as exc:
            self.db.update(key, 'failed', error=str(exc)[:2000], expected={'queued_local', 'uploading', 'running'})

    async def run_agent(self, row, snapshot):
        key = row['id']
        await asyncio.to_thread(self.verify_files, snapshot)
        images, videos = {}, {}
        for ref in snapshot.references:
            path = Path(self.studio.get_asset(ref.asset_id)['path'])
            if ref.kind == 'video':
                videos[ref.asset_id] = await asyncio.to_thread(prepared_evidence, self.studio.root, path,
                    ref.sha256, 'video', ref.duration_seconds, lambda: video_frames(path, ref.duration_seconds))
            else:
                images[ref.asset_id] = await asyncio.to_thread(prepared_evidence, self.studio.root, path,
                    ref.sha256, 'image', None, lambda: preview_jpeg(path))
                if not images[ref.asset_id]:
                    raise ValueError('Не удалось прочитать фотографию ' + ref.asset_id)
        cap = context_ir_agent.capability()
        # Persist before calling the CLI; after a crash a second agent call is never implicit.
        marker = {'backend': 'subscription_agent', 'provenance': cap}
        if not self.db.update(key, 'running', result=marker, expected={'queued_local'}):
            return
        try:
            result = await context_ir_agent.generate(snapshot, images, videos, cap)
            self.validate_result(snapshot, result)
            await asyncio.to_thread(self.verify_files, snapshot)
            state = 'needs_input' if any(is_blocking(p) for p in result['problems']) else 'ready'
            self.db.update(key, state, result={**result, 'backend': 'subscription_agent', 'cache_hit': False}, expected={'running'})
        except (BrainError, TimeoutError) as exc:
            self.db.update(key, 'failed', error=str(exc)[:2000], expected={'running'})

    def uncertain(self, row, message):
        current = self.db.get(row['id'])
        if current['state'] in {'uploading', 'running'}:
            self.db.update(row['id'], 'needs_reconciliation', result=current['result'], error=message[:1000],
                           expected={'uploading', 'running'})
        # Before submission an offline GPU leaves the immutable request queued.

    @staticmethod
    def validate_result(snapshot, result):
        rebuilt = compile_plan(snapshot, ScenePlan.model_validate(result['context']))
        if any(rebuilt.get(k) != result.get(k) for k in ('version', 'prompt', 'compiled_prompt_hash', 'bindings', 'problems', 'execution')):
            raise ValueError('Результат подготовки не совпадает с локальной компиляцией')
        return rebuilt

    def consume(self, key, snapshot, remote, wire):
        state = remote['state']
        current = self.db.get(key)
        if current['state'] in TERMINAL:
            return
        if state in {'ready', 'needs_input'}:
            result = remote['result']
            self.validate_result(snapshot, result)
            if result.get('input_hash') != digest(snapshot.cache_input()) or result.get('provenance', {}).get('fingerprint') != wire['fingerprint']:
                raise ValueError('Получен контекст для другого снимка/профиля')
            if any(is_blocking(p) for p in result['problems']):
                state = 'needs_input'
            self.db.update(key, state, result={**result, 'cache_hit': False}, expected={current['state']})
        elif state == 'failed':
            self.db.update(key, 'failed', error=remote.get('error', ''), expected={current['state']})
        else:
            target = 'queued_local' if state == 'waiting_gpu' else 'running'
            self.db.update(key, target, result={'wire': wire}, expected={current['state']})

    async def poll(self, client, row):
        wire = (row.get('result') or {}).get('wire')
        if not wire:
            raise ValueError('Нет сохранённой квитанции отправки')
        response = await client.get(self.gpu_url + '/api/context-ir/jobs/' + row['id'])
        if response.status_code == 404:
            self.db.update(row['id'], 'needs_reconciliation', result=row['result'],
                           error='Сервер не знает этот ID; автоматическая повторная отправка остановлена',
                           expected={'uploading', 'running', 'needs_reconciliation'})
            return
        response.raise_for_status()
        self.consume(row['id'], Snapshot.model_validate(row['payload']), response.json(), wire)

    async def reconcile(self):
        async with httpx.AsyncClient(timeout=10) as client:
            for row in self.db.active():
                if row['state'] not in {'needs_reconciliation', 'running', 'uploading'}:
                    continue
                if (row.get('result') or {}).get('backend') == 'subscription_agent':
                    self.db.update(row['id'], 'needs_reconciliation', result=row['result'],
                        error='Подготовка агентом была прервана; проверь результат перед новым запросом',
                        expected={'running', 'uploading'})
                    continue
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row['created_at']).replace(tzinfo=timezone.utc)).total_seconds()
                if age > 7200:
                    self.db.update(row['id'], 'failed', error='Срок сверки подготовки истёк; рендер не создавался', expected={row['state']})
                    continue
                try:
                    await self.poll(client, row)
                except (httpx.HTTPError, ValueError):
                    pass


    async def render(self, key, request: RenderInput, style_adapters):
        row = self.db.get(str(key))
        if not row or row['state'] != 'ready':
            raise HTTPException(409, 'Контекст ещё не готов')
        try:
            snapshot = Snapshot.model_validate(row['payload'])
            await asyncio.to_thread(self.verify_files, snapshot)
            result = self.validate_result(snapshot, row['result'])
            if any(is_blocking(p) for p in result['problems']):
                raise ValueError('У контекста остались блокирующие замечания')
            settings = {**snapshot.settings, 'duration': snapshot.duration,
                        'seed': request.seed if request.seed is not None else snapshot.settings.get('seed'),
                        'context_ir': {'artifact_id': str(key), 'version': VERSION,
                                       'compiled_prompt_hash': result['compiled_prompt_hash']}}
            # Official IR already includes direction. Existing store keeps its graph controls
            # and disables a second Qwen pass; original references are rechecked by the GPU.
            adapters = await style_adapters()
            return await asyncio.to_thread(self.studio.create_job,
                prompt=result['prompt'], asset_ids=[a.asset_id for a in snapshot.references], settings=settings,
                reference_numbers={a.asset_id: a.number for a in snapshot.references},
                reference_roles={a.asset_id: a.role for a in snapshot.references},
                reference_owners={a.asset_id: a.owner for a in snapshot.references},
                reference_performers={a.asset_id: {'pairs': [{'owner': a.owner, 'performer': a.performer}]}
                    for a in snapshot.references if a.kind == 'video' and a.owner and a.role in {'Движение', 'Движение и камера', 'Исходное видео'}},
                style_adapters=adapters,
                receipt_key='context-ir:' + str(request.client_request_id),
                receipt_hash=digest([str(key), request.model_dump(mode='json')]))
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc



def make_router(queue: ContextQueue, style_adapters):
    router = APIRouter()

    @router.get('/api/context-ir/capabilities')
    async def capabilities():
        cap = context_ir_agent.capability()
        result = {'version': VERSION, 'workflows': ['h3'],
                  'reference_required': True, 'audio_analysis': False,
                  'video_analysis': 'sampled_frames', 'gpu_ready': False,
                  'backend': 'subscription_agent', 'provider': cap['identity']['provider'],
                  'state': 'subscription_agent', 'message': 'Подготовка через подписку агента; GPU используется только для генерации',
                  'preparation_requires_gpu': False, 'model_revision': 'unknown'}
        return result

    @router.post('/api/context-ir/jobs', status_code=202)
    async def create(request: Input):
        try:
            snapshot = await asyncio.to_thread(queue.snapshot, request)
            row, _ = queue.db.accept(str(request.client_request_id), snapshot.model_dump())
            return public(row)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get('/api/context-ir/jobs')
    async def recent():
        return [public(row) for row in queue.db.recent()]

    @router.get('/api/context-ir/jobs/{key}')
    async def status(key: uuid.UUID):
        row = queue.db.get(str(key))
        if not row:
            raise HTTPException(404, 'Контекст не найден')
        return public(row)

    @router.post('/api/context-ir/jobs/{key}/cancel')
    async def cancel(key: uuid.UUID):
        row = queue.db.get(str(key))
        if not row:
            raise HTTPException(404, 'Контекст не найден')
        queue.db.update(str(key), 'cancelled', error='Подготовка отменена; уже начатый запрос может завершиться без применения результата',
                        expected={'queued_local', 'uploading', 'running', 'needs_reconciliation'})
        return public(queue.db.get(str(key)))

    @router.post('/api/context-ir/jobs/{key}/render', status_code=202)
    async def render(key: uuid.UUID, request: RenderInput):
        return await queue.render(key, request, style_adapters)

    return router
