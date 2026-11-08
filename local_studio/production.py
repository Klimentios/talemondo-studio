"""Mac-owned orchestration over existing queues; survives browser/process restarts.

Only this state machine chooses transitions. Context-IR contributes scene intent, not
executable code. Child creation has durable receipts, never an ambiguous automatic retry.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import subprocess
import time
import uuid

import httpx
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.context_ir.schema import Snapshot, digest
from app.h3_ir import is_blocking
from app.production_recipes import resolve_recipe
from .context_ir import Input, RenderInput
from .worker import sha256_file
from .reference_segments import SegmentRequest, reference_segment

TERMINAL = {'completed', 'failed', 'cancelled', 'needs_input', 'budget_exhausted'}
CHILD_TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


class ProductionInput(Input):
    reference_windows: dict[str, SegmentRequest] = Field(default_factory=dict, max_length=3)
    max_generation_jobs: int = Field(default=4, ge=1, le=6)
    max_execution_seconds: int = Field(default=14400, ge=60, le=86400)


def child_key(uid, stage):
    return str(uuid.uuid5(uuid.UUID(uid), stage))


class ProductionQueue:
    def __init__(self, studio, context, adapters):
        self.studio, self.context, self.adapters = studio, context, adapters
        self.qc_tasks = {}
        with studio.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS production_jobs (
                id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, snapshot TEXT NOT NULL, recipe TEXT NOT NULL,
                state TEXT NOT NULL, context_id TEXT NOT NULL, child_id TEXT, child_kind TEXT,
                stage_index INTEGER NOT NULL DEFAULT 0, frame_asset_id TEXT,
                used_jobs INTEGER NOT NULL DEFAULT 0, max_jobs INTEGER NOT NULL, max_seconds INTEGER NOT NULL,
                started_at REAL, stop_requested INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '', result TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    @staticmethod
    def unpack(row):
        if row is None: return None
        value = dict(row)
        for key in ('snapshot', 'recipe', 'result'):
            value[key] = json.loads(value[key]) if value[key] else None
        return value

    def get(self, uid):
        with self.studio.connect() as db:
            return self.unpack(db.execute('SELECT * FROM production_jobs WHERE id=?', (uid,)).fetchone())

    def list(self, active=False):
        with self.studio.connect() as db:
            sql = 'SELECT * FROM production_jobs'
            if active:
                sql += " WHERE state NOT IN ('completed','failed','cancelled','needs_input','budget_exhausted')"
            return [self.unpack(r) for r in db.execute(sql + ' ORDER BY created_at,id')]

    def update(self, uid, **fields):
        allowed = {'state','child_id','child_kind','stage_index','frame_asset_id','used_jobs','started_at',
                   'stop_requested','error','result'}
        if not fields or set(fields) - allowed: raise ValueError('Недопустимый переход производства')
        if 'result' in fields: fields['result'] = json.dumps(fields['result'], ensure_ascii=False)
        with self.studio.connect() as db:
            db.execute('UPDATE production_jobs SET ' + ','.join(k + '=?' for k in fields)
                       + ',updated_at=CURRENT_TIMESTAMP WHERE id=?', (*fields.values(), uid))

    def create(self, request):
        uid = str(request.client_request_id)
        request_data = request.model_dump(mode='json')
        if not request_data['reference_windows']:
            request_data.pop('reference_windows')  # Existing immutable IDs retain their original fingerprint.
        h = digest(request_data)
        old = self.get(uid)
        if old:
            if old['request_hash'] != h: raise ValueError('Этот ID принадлежит другой задаче производства')
            return old
        context_id = child_key(uid, 'context')
        payload = request.model_dump(exclude={'max_generation_jobs', 'max_execution_seconds', 'reference_windows'})
        payload['client_request_id'] = uuid.UUID(context_id)
        for asset_id, window in request.reference_windows.items():
            ref = next((r for r in payload['references'] if r['asset_id'] == asset_id and r['kind'] == 'video'), None)
            if not ref:
                raise ValueError('Участок должен относиться к прикреплённому видео')
            if window.duration != request.settings.get('duration', 10):
                raise ValueError('Длительность участка должна совпадать с длительностью шота')
            segment = reference_segment(self.studio, asset_id, window)
            ref['asset_id'] = segment['asset_id']
        snapshot = self.context.snapshot(Input.model_validate(payload))
        if snapshot.settings.get('seed') is None:
            snapshot.settings['seed'] = secrets.randbelow(2**53)
        recipe = resolve_recipe(snapshot)
        recipe['prepared_references'] = [
            {'source_asset_id': original.asset_id, 'prepared_asset_id': prepared.asset_id,
             'start': request.reference_windows[original.asset_id].start,
             'duration': request.reference_windows[original.asset_id].duration,
             'sha256': prepared.sha256}
            for original, prepared in zip(request.references, snapshot.references)
            if original.asset_id in request.reference_windows]
        with self.studio.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM production_jobs WHERE id=?', (uid,)).fetchone()
            if old:
                if old['request_hash'] != h: raise ValueError('Этот ID принадлежит другой задаче производства')
                return self.unpack(old)
            db.execute('INSERT INTO production_jobs(id,request_hash,snapshot,recipe,state,context_id,max_jobs,max_seconds) '
                       "VALUES (?,?,?,?,'analyzing',?,?,?)", (uid,h,snapshot.model_dump_json(),json.dumps(recipe,ensure_ascii=False),
                        context_id,request.max_generation_jobs,request.max_execution_seconds))
        return self.get(uid)

    def child(self, row):
        if not row['child_id']: return None
        # Only H3 children since 2026-11-03; old Qwen keyframe / LTX children read as missing.
        return self.studio.get_job(row['child_id']) if row['child_kind'] == 'h3' else None

    def cancel(self, uid):
        row = self.get(uid)
        if not row: raise ValueError('Задача производства не найдена')
        if row['state'] in TERMINAL: return row
        self.update(uid, stop_requested=1, state='cancelling')
        return self.get(uid)

    def first_qc(self):
        # Running reviews retain the GPU slot, including while cancellation is requested.
        if self.qc_tasks:
            row = self.get(next(iter(self.qc_tasks)))
            return {**row, 'state':'running'}
        rows = [r for r in self.list(active=True) if r['state'] in {'awaiting_qc','checking'}]
        if not rows: return None
        row = next((r for r in rows if r['state']=='checking'), rows[0])
        return {**row,'state':'running' if row['state']=='checking' else 'queued_local'}

    async def run_qc_once(self):
        candidate = self.first_qc()
        if not candidate: return
        uid = candidate['id']; row = self.get(uid)
        if row['state']=='awaiting_qc':
            # The visual review is the Studio agent's (MCP job_frames) or the user's: no Qwen VL call holds the
            # GPU queue any more. The technical checks above already ran.
            result=row['result'];result['quality']['visual']={'status':'manual','automatic_regeneration':False,
                'finding':'Визуальную проверку делает агент (job_frames) или ты; автоматическая проверка Qwen убрана'}
            self.update(uid,state='cancelled' if row['stop_requested'] else 'completed',result=result)
            return
        task=self.qc_tasks.get(uid)
        if task and not task.done():return
        if task:
            try: report=task.result()
            except (Exception, asyncio.CancelledError) as exc: report={'status':'unknown','finding':str(exc)[:1000],'automatic_regeneration':False}
            self.qc_tasks.pop(uid,None)
        else:
            # The POST may have run before this process died. Do not repeat the GPU call.
            report={'status':'unknown','finding':'Проверка прервана перезапуском; автоматический повтор отключён'}
        row=self.get(uid);result=row['result'];result['quality']['visual']=report
        state='cancelled' if row['stop_requested'] else 'completed'
        self.update(uid,state=state,result=result)

    async def advance(self):
        # The main dispatch lock serializes this pass with submit/cancel/approve endpoints.
        for row in self.list(active=True):
            try:
                await self._advance(row)
            except HTTPException as exc:
                self.update(row['id'], state='failed', error=str(exc.detail))
            except (ValueError, OSError) as exc:
                self.update(row['id'], state='failed', error=str(exc)[:2000])

    async def _advance(self, row):
        uid = row['id']
        child = self.child(row)
        expired = bool(row['started_at'] and time.time() - row['started_at'] > row['max_seconds'])
        if row['stop_requested'] or expired:
            task = self.qc_tasks.get(uid)
            if task and not task.done():
                self.update(uid,stop_requested=1,error='После текущей проверки дальнейшие этапы остановлены')
                return
            # Cancel only definitely local children. A remote job is drained and its files preserved.
            if child and child['state'] == 'queued_local':
                self.studio.cancel_queued(row['child_id'])
                child = self.child(row)
            if child and child['state'] not in CHILD_TERMINAL:
                self.update(uid, state='cancelling', stop_requested=1,
                            error='Новые этапы остановлены; уже отправленный этап сохраняется до завершения или сверки')
                return
            context = self.context.db.get(row['context_id'])
            if context and context['state'] in {'uploading','running','needs_reconciliation'}:
                self.update(uid, state='cancelling', stop_requested=1,
                            error='Анализ уже отправлен; ожидает завершения или сверки без новых генераций')
                return
            if context and context['state'] == 'queued_local':
                self.context.db.update(row['context_id'], 'cancelled', error='Производство отменено', expected={'queued_local'})
            self.update(uid, state='budget_exhausted' if expired else 'cancelled',
                        error='Лимит времени исчерпан' if expired else 'Дальнейшие этапы отменены')
            return
        if self.studio.queue_paused(): return
        snapshot = Snapshot.model_validate(row['snapshot'])
        if row['state'] == 'analyzing':
            context, _ = self.context.db.accept(row['context_id'], snapshot.model_dump())
            if context['state'] in {'failed','cancelled','needs_input'}:
                problems = (context.get('result') or {}).get('problems', [])
                self.update(uid, state=context['state'], error=context['error'] or '\n'.join(problems))
                return
            if context['state'] != 'ready': return
            await asyncio.to_thread(self.context.verify_files, snapshot)
            result = self.context.validate_result(snapshot, context['result'])
            if any(is_blocking(p) for p in result['problems']):
                self.update(uid, state='needs_input', error='\n'.join(result['problems']))
                return
            if not row['started_at']:
                row['started_at'] = time.time()
                self.update(uid,started_at=row['started_at'])
            # A fixed receipt catches a crash after creation but before our transition was saved.
            job = await self.context.render(row['context_id'],
                RenderInput(client_request_id=uuid.UUID(child_key(uid,'h3')),seed=snapshot.settings['seed']),self.adapters)
            self.update(uid,state='rendering',child_kind='h3',child_id=job['job_id'],used_jobs=1)
            return
        if row['state'] != 'rendering': return
        if not child:
            self.update(uid,state='failed',error='Дочернее задание отсутствует; повтор не выполняется'); return
        if child['state'] in {'failed','cancelled','interrupted'}:
            self.update(uid,state='failed',error=child.get('error') or 'Этап не завершён'); return
        if child['state'] != 'completed': return
        output = Path(child.get('output_path') or '')
        if not output.is_file() or not output.resolve().is_relative_to(self.studio.results_dir.resolve()):
            raise ValueError('Этап сообщил завершение, но локальный результат отсутствует')
        output_hash = await asyncio.to_thread(sha256_file, output)
        self.update(uid,state='awaiting_qc',result={'child_id':row['child_id'],'engine':row['child_kind'],
            'output_path':str(output),'sha256':output_hash,'quality':{'technical':'child_queue_validated',
            'identity':'not_evaluated','creative_acceptance':'pending_review'},'automatic_repair_attempts':0})


def public(row):
    result = {k:v for k,v in row.items() if k not in {'snapshot','request_hash'}}
    result['brief'] = row['snapshot']['brief']
    fields = {'asset_id','kind','number','role','owner','performer','duration_seconds'}
    result['references'] = [{k:v for k,v in ref.items() if k in fields} for ref in row['snapshot']['references']]
    return result


LOCK_WAIT_S = 20.0


@contextlib.asynccontextmanager
async def _held(lock):
    """The dispatch lock is held through a whole GPU upload/download; a button must not hang that long."""
    try:
        await asyncio.wait_for(lock.acquire(), LOCK_WAIT_S)
    except asyncio.TimeoutError:
        raise HTTPException(503, 'Очередь сейчас передаёт файлы на GPU, повтори через минуту') from None
    try:
        yield
    finally:
        lock.release()


def make_router(queue, lock):
    router = APIRouter(prefix='/api/productions',tags=['production'])
    @router.get('')
    async def recent():
        rows = queue.list()[::-1]
        active = [r for r in rows if r['state'] not in TERMINAL]
        finished = [r for r in rows if r['state'] in TERMINAL][:50]
        return [public(r) for r in active + finished]
    @router.post('',status_code=202)
    async def create(request:ProductionInput):
        try:
            async with _held(lock): row = await asyncio.to_thread(queue.create,request)
            return public(row)
        except (ValueError,OSError,subprocess.TimeoutExpired) as exc: raise HTTPException(422,str(exc)) from exc
    @router.get('/{uid}')
    async def get(uid:uuid.UUID):
        row=queue.get(str(uid))
        if not row: raise HTTPException(404,'Производство не найдено')
        return public(row)
    @router.post('/{uid}/cancel')
    async def cancel(uid:uuid.UUID):
        try:
            async with _held(lock):
                queue.cancel(str(uid)); await queue.advance()
                return public(queue.get(str(uid)))
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    return router
