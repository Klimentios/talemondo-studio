from __future__ import annotations
import asyncio
import os
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import Field
from app.contract import director_schema_sha256
from app.motion_reference_policy import POLICY as MOTION_POLICY
from app.scenarist_profile import capability as model_capability
from app.h3_ir import is_blocking
from app.qwen_lifecycle import GpuBusy
from .schema import Strict, Snapshot, VideoFrame, VERSION, digest
from .store import Receipts
from .engine import generate

# Unknown model revisions never reuse cache across a server process/deployment.
BOOT = str(uuid.uuid4())


def capability():
    model = model_capability()
    identity = {key: model.get(key) for key in ('profile', 'model', 'revision', 'vllm', 'thinking')}
    identity['deployment'] = os.environ.get('H3_CONTEXT_DEPLOYMENT_ID') or BOOT
    identity['process'] = BOOT
    identity['schema_hash'] = director_schema_sha256()
    identity['motion_policy_hash'] = digest(MOTION_POLICY)
    return {'version': VERSION, 'analysis': ['image', 'video_frames'], 'max_images': 9,
            'audio_analysis': False, 'video_analysis': 'sampled_frames', 'identity': identity,
            'fingerprint': digest({'version': VERSION, **identity})}


class Request(Strict):
    client_request_id: uuid.UUID
    snapshot: Snapshot
    images: dict[str, str] = Field(default_factory=dict, max_length=9)
    videos: dict[str, list[VideoFrame]] = Field(default_factory=dict, max_length=3)
    fingerprint: str


def make_router():
    router = APIRouter()
    db = Receipts(Path(os.environ.get('H3_CONTEXT_REGISTRY_PATH', str(Path.home() / '.local/state/h3-studio/context-ir.sqlite3'))))
    tasks = {}

    async def execute(key, request, cap):
        try:
            result = await asyncio.to_thread(generate, request.snapshot, request.images, cap,
                                             {k: [f.model_dump() for f in v] for k, v in request.videos.items()})
            result['cache_key'] = digest([request.snapshot.cache_input(), cap['fingerprint']])
            state = 'needs_input' if any(is_blocking(p) for p in result['problems']) else 'ready'
            db.update(key, state, result=result, expected={'running'})
        except GpuBusy as exc:
            db.update(key, 'waiting_gpu', error=str(exc), expected={'running'})
        except Exception as exc:
            db.update(key, 'failed', error=str(exc)[:2000], expected={'running'})
        finally:
            tasks.pop(key, None)

    @router.post('/api/context-ir/jobs', status_code=202)
    async def create(request: Request):
        cap = capability()
        if request.fingerprint != cap['fingerprint']:
            raise HTTPException(409, 'Профиль Context-IR изменился: подготовь новый снимок')
        key = str(request.client_request_id)
        payload = request.model_dump(mode='json')
        try:
            row, _ = db.accept(key, payload, 'waiting_gpu')
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if row['state'] == 'waiting_gpu' and db.update(key, 'running', expected={'waiting_gpu'}):
            tasks[key] = asyncio.create_task(execute(key, request, cap))
        return public(db.get(key))

    @router.get('/api/context-ir/jobs/{key}')
    async def status(key: uuid.UUID):
        row = db.get(str(key))
        if not row:
            raise HTTPException(404, 'Подготовка не найдена на этом сервере')
        if row['state'] == 'running' and str(key) not in tasks and row['payload'].get('fingerprint') != capability()['fingerprint']:
            db.update(str(key), 'failed', error='Процесс подготовки был перезапущен; частичный результат не используется', expected={'running'})
            row = db.get(str(key))
        return public(row)

    def public(row):
        return {k: v for k, v in row.items() if k != 'payload'}

    return router
