import asyncio
import hashlib
import json
import uuid

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import httpx

from app.server_queue import ServerQueue, install


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('H3_SERVER_QUEUE_DIR', str(tmp_path))
    monkeypatch.setenv('H3_SERVER_QUEUE_ENABLED', '1')
    app = FastAPI()
    accepted = []

    @app.post('/api/jobs')
    async def render(request: Request):
        form = await request.form()
        accepted.append({'client_job_id': form['client_job_id'], 'prompt': form['prompt'],
                         'file': await form['images'].read()})
        return {'job_id': 'actual-render-id'}

    @app.get('/api/jobs/by-client/{job_id}')
    async def lookup(job_id):
        from fastapi import HTTPException
        raise HTTPException(404)

    queue = install(app, 'http://comfy')
    client = TestClient(app)
    original = httpx.AsyncClient
    busy = {'value': False}

    def async_client(*args, **kwargs):
        if 'transport' not in kwargs:
            kwargs['transport'] = httpx.MockTransport(lambda req: httpx.Response(200, json={
                'queue_running': ['active'] if busy['value'] else [], 'queue_pending': []}))
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', async_client)
    return queue, client, accepted, busy


def request(job_id, content=b'photo'):
    return {'data': {'client_job_id': job_id, 'prompt': 'original scene',
                     'manifest_json': json.dumps({'images': [{'sha256': hashlib.sha256(content).hexdigest()}]})},
            'files': [('images', ('face.png', content, 'image/png'))]}


def test_durable_upload_then_autonomous_execution_without_mac(tmp_path, monkeypatch):
    queue, client, accepted, busy = setup(tmp_path, monkeypatch)
    job_id = str(uuid.uuid4())
    busy['value'] = True
    assert client.post('/api/server-queue/jobs', **request(job_id)).status_code == 202
    # Restoring the server dispatcher from disk does not need the Mac or its files.
    restored = ServerQueue(tmp_path, queue.application, 'http://comfy')
    assert (tmp_path / job_id / 'images-0').read_bytes() == b'photo'
    asyncio.run(restored.run_once())
    assert accepted == []
    busy['value'] = False
    asyncio.run(restored.run_once())
    assert accepted == [{'client_job_id': job_id, 'prompt': 'original scene', 'file': b'photo'}]
    assert restored.record(job_id)['remote_job_id'] == 'actual-render-id'
    asyncio.run(restored.run_once())
    assert accepted == [{'client_job_id': job_id, 'prompt': 'original scene', 'file': b'photo'}]


def test_idempotent_acceptance_and_conflicting_client_id(tmp_path, monkeypatch):
    queue, client, _, _ = setup(tmp_path, monkeypatch)
    job_id = str(uuid.uuid4())
    assert client.post('/api/server-queue/jobs', **request(job_id)).status_code == 202
    assert client.post('/api/server-queue/jobs', **request(job_id)).status_code == 202
    changed = request(job_id)
    changed['data']['prompt'] = 'another scene'
    assert client.post('/api/server-queue/jobs', **changed).status_code == 409
    assert len(list(tmp_path.glob('*/record.json'))) == 1


def test_missing_or_corrupt_upload_is_never_acknowledged(tmp_path, monkeypatch):
    queue, client, _, _ = setup(tmp_path, monkeypatch)
    job_id = str(uuid.uuid4())
    bad = request(job_id)
    bad['files'] = [('images', ('face.png', b'changed', 'image/png'))]
    assert client.post('/api/server-queue/jobs', **bad).status_code == 422
    assert queue.record(job_id) is None
    assert not list(tmp_path.iterdir())


def test_crashed_submission_is_not_automatically_repeated(tmp_path, monkeypatch):
    queue, client, accepted, _ = setup(tmp_path, monkeypatch)
    job_id = str(uuid.uuid4())
    client.post('/api/server-queue/jobs', **request(job_id))
    record = queue.record(job_id)
    record['state'] = 'submitting'
    queue.save(record)
    asyncio.run(queue.run_once())
    assert accepted == []
    assert queue.record(job_id)['state'] == 'needs_reconciliation'


def test_cancel_server_pending_job_prevents_render(tmp_path, monkeypatch):
    queue, client, accepted, _ = setup(tmp_path, monkeypatch)
    job_id = str(uuid.uuid4())
    client.post('/api/server-queue/jobs', **request(job_id))
    assert client.post(f'/api/server-queue/jobs/{job_id}/cancel').json()['cancelled']
    asyncio.run(queue.run_once())
    assert accepted == []
    assert client.get(f'/api/server-queue/jobs/{job_id}').json()['status'] == 'error'


def test_server_drains_multiple_saved_jobs_in_order_without_mac(tmp_path, monkeypatch):
    queue, client, accepted, busy = setup(tmp_path, monkeypatch)
    busy['value'] = True
    ids = [str(uuid.uuid4()) for _ in range(2)]
    for job_id in ids:
        assert client.post('/api/server-queue/jobs', **request(job_id)).status_code == 202
    busy['value'] = False
    restored = ServerQueue(tmp_path, queue.application, 'http://comfy')
    asyncio.run(restored.run_once())
    asyncio.run(restored.run_once())
    assert [row['client_job_id'] for row in accepted] == ids
    assert all(row['file'] == b'photo' and row['prompt'] == 'original scene' for row in accepted)


def test_cancel_during_reconciliation_is_kept(tmp_path, monkeypatch):
    # 07.11.2026: «Прервать» on an ambiguous submission answered cancelled=False and the job could render later.
    queue, client, accepted, _ = setup(tmp_path, monkeypatch)
    job_id = str(uuid.uuid4())
    client.post('/api/server-queue/jobs', **request(job_id))
    record = queue.record(job_id)
    record['state'] = 'needs_reconciliation'
    queue.save(record)
    assert client.post(f'/api/server-queue/jobs/{job_id}/cancel').json()['cancelled'] is True
    asyncio.run(queue.run_once())
    assert accepted == []
    assert queue.record(job_id)['state'] == 'cancelled'


def test_cancel_arriving_during_upload_cancels_the_render(tmp_path, monkeypatch):
    # 56a16520 on 06.11: cancelled in Studio while the server was submitting it, rendered anyway.
    queue, client, accepted, _ = setup(tmp_path, monkeypatch)
    cancelled = []
    app = queue.application
    app.router.routes = [r for r in app.router.routes if getattr(r, 'path', '') != '/api/jobs']

    @app.post('/api/jobs')
    async def render(request: Request):
        form = await request.form()
        accepted.append(form['client_job_id'])
        record = queue.record(form['client_job_id'])  # the user's cancel lands mid-upload
        assert record['state'] == 'submitting'
        record['cancel_requested'] = True
        queue.save(record)
        return {'job_id': 'actual-render-id'}

    @app.post('/api/jobs/{remote}/cancel')
    async def cancel(remote: str):
        cancelled.append(remote)
        return {'cancelled': True}

    job_id = str(uuid.uuid4())
    client.post('/api/server-queue/jobs', **request(job_id))
    asyncio.run(queue.run_once())
    assert accepted == [job_id] and cancelled == ['actual-render-id']
    assert queue.record(job_id)['state'] == 'cancelled'
    assert client.get(f'/api/server-queue/jobs/{job_id}').json()['status'] == 'error'
