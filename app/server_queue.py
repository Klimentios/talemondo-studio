"""Durable H3 intake: uploaded work continues without its Mac client.

Uses the existing /api/jobs renderer unchanged, only when ComfyUI is idle.
A crash during submission is reconciled by client ID, never blindly retried.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from fastapi import APIRouter, HTTPException, Request
from starlette.datastructures import UploadFile
import httpx

log = logging.getLogger(__name__)


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ServerQueue:
    def __init__(self, root: Path, application, comfy_url: str, renderer_url: str | None = None):
        self.root = root
        self.application = application
        self.comfy_url = comfy_url
        self.renderer_url = renderer_url
        self.lock = asyncio.Lock()
        self.task = None

    def renderer(self):
        if self.renderer_url:
            return httpx.AsyncClient(base_url=self.renderer_url, timeout=1800)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://server')

    def record(self, job_id):
        try:
            if str(uuid.UUID(job_id)) != job_id:
                raise ValueError()
            return json.loads((self.root / job_id / 'record.json').read_text())
        except (ValueError, FileNotFoundError):
            return None

    def save(self, record):
        atomic_json(self.root / record['client_job_id'] / 'record.json', record)

    @staticmethod
    def receipt(record):
        return {'job_id': record.get('remote_job_id') or record['client_job_id'],
                'client_job_id': record['client_job_id'], 'status': 'queued',
                'server_queue': True, 'server_state': record['state'],
                'director': {'server_queue': True}}

    async def accept(self, request):
        form = await request.form(max_files=12, max_fields=80, max_part_size=2 * 1024 * 1024)
        fields = {key: value for key, value in form.multi_items() if isinstance(value, str)}
        job_id = fields.get('client_job_id', '')
        try:
            if str(uuid.UUID(job_id)) != job_id:
                raise ValueError()
            manifest = json.loads(fields['manifest_json'])
        except (ValueError, KeyError):
            raise HTTPException(422, 'Нужны канонический client_job_id и манифест')
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.upload-', dir=self.root))
        try:
            files = []
            counts = {}
            for key, upload in form.multi_items():
                if not isinstance(upload, UploadFile):
                    continue
                if key not in {'images', 'videos', 'audios'}:
                    raise HTTPException(422, 'Неизвестный тип референса')
                index = counts.get(key, 0)
                entries = manifest.get(key, [])
                if index >= len(entries):
                    raise HTTPException(422, 'Файлы не совпадают с манифестом')
                counts[key] = index + 1
                limit = (25 if key == 'images' else 500 if key == 'videos' else 100) * 1024 * 1024
                path = temporary / f'{key}-{index}'
                digest = hashlib.sha256()
                size = 0
                with path.open('wb') as stream:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise HTTPException(413, 'Референс слишком большой')
                        digest.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not size or digest.hexdigest() != entries[index]['sha256']:
                    raise HTTPException(422, 'SHA-256 переданного референса не совпадает')
                files.append({'field': key, 'path': path.name, 'name': upload.filename,
                              'mime': upload.content_type, 'sha256': digest.hexdigest()})
            if any(counts.get(key, 0) != len(manifest.get(key, [])) for key in ('images', 'videos', 'audios')):
                raise HTTPException(422, 'Не все референсы переданы')
            fingerprint = hashlib.sha256(json.dumps({'fields': fields, 'files': files}, sort_keys=True).encode()).hexdigest()
            async with self.lock:
                existing = self.record(job_id)
                if existing:
                    if existing['fingerprint'] != fingerprint:
                        raise HTTPException(409, 'client_job_id уже принят с другим заданием')
                    return self.receipt(existing)
                record = {'client_job_id': job_id, 'state': 'queued', 'fields': fields,
                          'files': files, 'fingerprint': fingerprint}
                atomic_json(temporary / 'record.json', record)
                os.rename(temporary, self.root / job_id)
                fd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                return self.receipt(record)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
            await form.close()

    async def run_once(self):
        if not self.root.exists():
            return
        async with self.renderer() as client:
            records = [self.record(p.name) for p in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime)
                       if p.is_dir() and not p.name.startswith('.')]
            # Recover only the same accepted request after a process interruption.
            for record in records:
                if record and record['state'] in {'submitting', 'needs_reconciliation'}:
                    found = await client.get('/api/jobs/by-client/' + record['client_job_id'])
                    if found.status_code == 200 and found.json().get('job_id'):
                        record.update(state='submitted', remote_job_id=found.json()['job_id'])
                        self.save(record)
                        await self._honour_cancel(client, record['client_job_id'])
                    elif record.get('cancel_requested'):
                        record.update(state='cancelled', error='Отменено пользователем до приёма на GPU')
                        self.save(record)
                    elif record['state'] == 'submitting':
                        record.update(state='needs_reconciliation', error='Приём после перезапуска неоднозначен; автоповтор отключён')
                        self.save(record)
            pending = next((r for r in records if r and r['state'] == 'queued'), None)
            if not pending:
                return
            async with httpx.AsyncClient(timeout=10) as remote:
                response = await remote.get(self.comfy_url + '/queue')
                response.raise_for_status()
                queue = response.json()
            if queue.get('queue_running') or queue.get('queue_pending'):
                return
            async with self.lock:
                pending = self.record(pending['client_job_id'])
                if pending['state'] != 'queued':
                    return
                pending['state'] = 'submitting'
                self.save(pending)
            try:
                with ExitStack() as stack:
                    files = []
                    for item in pending['files']:
                        path = self.root / pending['client_job_id'] / item['path']
                        with path.open('rb') as stream:
                            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                        if checksum != item['sha256']:
                            raise ValueError('Сохранённый референс изменился')
                        files.append((item['field'], (item['name'], stack.enter_context(path.open('rb')), item['mime'])))
                    response = await client.post('/api/jobs', data=pending['fields'], files=files)
                if response.is_success:
                    pending.update(state='submitted', remote_job_id=response.json()['job_id'])
                elif 400 <= response.status_code < 500:
                    pending.update(state='failed', error=str(response.json().get('detail', response.text))[:1000])
                else:
                    pending.update(state='needs_reconciliation', error=f'Приём неоднозначен: HTTP {response.status_code}')
            except Exception as exc:
                pending.update(state='needs_reconciliation', error=str(exc)[:1000])
            async with self.lock:
                # A cancel may have arrived while the upload ran: keep its flag, never overwrite it.
                pending['cancel_requested'] = bool((self.record(pending['client_job_id']) or {}).get('cancel_requested'))
                self.save(pending)
            if pending['state'] == 'submitted':
                for item in pending['files']:
                    (self.root / pending['client_job_id'] / item['path']).unlink(missing_ok=True)
                await self._honour_cancel(client, pending['client_job_id'])

    async def _honour_cancel(self, client, job_id: str) -> None:
        """07.11.2026: «Прервать» during submitting/reconciliation used to answer cancelled=False and the job
        rendered anyway (56a16520 on 06.11). The flag is kept on the record and applied once the GPU id is known."""
        record = self.record(job_id)
        if not record or not record.get('cancel_requested') or record['state'] != 'submitted':
            return
        try:
            result = await client.post('/api/jobs/' + record['remote_job_id'] + '/cancel')
            done = result.is_success and result.json().get('cancelled') is True
        except Exception:
            done = False
        async with self.lock:
            record = self.record(job_id)
            if done and record and record['state'] == 'submitted':
                record.update(state='cancelled', error='Отменено пользователем')
                self.save(record)

    async def loop(self):
        # One autonomous dispatcher across API processes; keep uploads independent.
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / 'dispatcher.lock').open('a+b') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            while True:
                try:
                    await self.run_once()
                except Exception:
                    log.exception('Серверная очередь')
                await asyncio.sleep(2)


def install(application, comfy_url, renderer_url=None):
    root = Path(os.environ.get('H3_SERVER_QUEUE_DIR', '/workspace/h3/api-data/server-queue'))
    spool = ServerQueue(root, application, comfy_url, renderer_url)
    router = APIRouter(prefix='/api/server-queue')

    @router.get('/capabilities')
    async def capabilities():
        return {'server_queue_v1': os.environ.get('H3_SERVER_QUEUE_ENABLED') == '1', 'durable': True}

    @router.post('/jobs', status_code=202)
    async def accept(request: Request):
        if os.environ.get('H3_SERVER_QUEUE_ENABLED') != '1':
            raise HTTPException(503, 'Серверная очередь отключена')
        return await spool.accept(request)

    @router.get('/jobs/{job_id}')
    async def status(job_id: str):
        record = spool.record(job_id)
        if record is None:
            raise HTTPException(404, 'Нет задания в серверной очереди')
        if record['state'] == 'submitted':
            async with spool.renderer() as client:
                result = await client.get('/api/jobs/' + record['remote_job_id'])
                result.raise_for_status()
                receipt = await client.get('/api/jobs/by-client/' + job_id)
                return {**result.json(), 'rendered_job_id': record['remote_job_id'],
                        'director': receipt.json().get('director') if receipt.is_success else None}
        if record['state'] in {'failed', 'cancelled', 'needs_reconciliation'}:
            return {'status': 'error', 'error': record.get('error') or record['state']}
        return {**spool.receipt(record), 'status': 'queued'}

    @router.post('/jobs/{job_id}/cancel')
    async def cancel(job_id: str):
        async with spool.lock:
            record = spool.record(job_id)
            if record is None:
                raise HTTPException(404, 'Нет задания')
            if record['state'] == 'queued':
                record.update(state='cancelled', error='Отменено пользователем')
                spool.save(record)
                return {'cancelled': True}
            if record['state'] in {'submitting', 'needs_reconciliation'}:
                record['cancel_requested'] = True
                spool.save(record)
                return {'cancelled': True}
            if record['state'] != 'submitted':
                return {'cancelled': False}
        async with spool.renderer() as client:
            result = await client.post('/api/jobs/' + record['remote_job_id'] + '/cancel')
            return result.json()

    application.include_router(router)

    @application.on_event('startup')
    async def start():
        if os.environ.get('H3_SERVER_QUEUE_ENABLED') == '1':
            spool.task = asyncio.create_task(spool.loop())

    @application.on_event('shutdown')
    async def stop():
        if spool.task:
            spool.task.cancel()
            await asyncio.gather(spool.task, return_exceptions=True)

    return spool
