"""Durable automatic production; synthetic Context-IR, no GPU or paid calls."""
import asyncio
import copy
import hashlib
import json
import uuid
from unittest.mock import patch

import pytest

from app.context_ir.schema import compile_plan
from local_studio.context_ir import ContextQueue, Input
from local_studio.production import ProductionQueue, ProductionInput
from local_studio.store import StudioStore
from tests.test_context_ir import plan, PNG


@pytest.fixture
def setup(tmp_path):
    studio = StudioStore(tmp_path)
    path = studio.assets_dir / 'person.png'
    path.write_bytes(PNG)
    asset = studio.add_asset(kind='image', role='Персонаж целиком', original_name='person.png', mime='image/png',
                             size=len(PNG), sha256=hashlib.sha256(PNG).hexdigest(), path=path)
    context = ContextQueue(studio, '')
    async def adapters(): return set()
    queue = ProductionQueue(studio, context, adapters)
    async def available(_recipe): return True
    queue.dependencies_ready = available
    request = ProductionInput(client_request_id=uuid.uuid4(), brief='ADAM raises his hand.',
        references=[dict(asset_id=asset['asset_id'], kind='image', role='Персонаж целиком', owner='ADAM', number=1)],
        settings=dict(duration=5, aspect_ratio='16:9', mode='realistic', style_adapter='none', seed=17))
    return queue, request


def prepare(queue, row):
    from app.context_ir.schema import Snapshot
    context = queue.context.db.get(row['context_id'])
    snapshot = Snapshot.model_validate(context['payload'])
    result = compile_plan(snapshot, plan(snapshot))
    queue.context.db.update(context['id'], 'ready', result=result)


def test_one_click_context_to_h3_survives_restart_without_duplicate(setup):
    queue, request = setup
    row = queue.create(request)
    assert row['recipe']['engine'] == 'h3'
    assert queue.create(request)['id'] == row['id']
    from app.context_ir.schema import digest
    assert row['request_hash'] == digest(request.model_dump(mode='json', exclude={'reference_windows'}))
    asyncio.run(queue.advance())
    row = queue.get(row['id'])
    prepare(queue, row)
    asyncio.run(queue.advance())
    row = queue.get(row['id'])
    assert row['state'] == 'rendering'
    assert len(queue.studio.list_jobs()) == 1
    child = queue.studio.get_job(row['child_id'])
    assert json.loads(child['settings_json'])['seed'] == 17
    assert json.loads(child['settings_json'])['cache_mode'] == 'safe'
    assert json.loads(child['settings_json'])['context_ir']['artifact_id'] == row['context_id']
    restarted = ProductionQueue(queue.studio, queue.context, queue.adapters)
    asyncio.run(restarted.advance())
    assert len(queue.studio.list_jobs()) == 1
    changed = request.model_copy(update={'brief': 'Different scene'})
    with pytest.raises(ValueError): queue.create(changed)


def test_pause_and_cancel_prevent_child_generation(setup):
    queue, request = setup
    row = queue.create(request)
    asyncio.run(queue.advance())
    prepare(queue, row)
    queue.studio.set_queue_paused(True)
    asyncio.run(queue.advance())
    assert queue.studio.list_jobs() == []
    queue.cancel(row['id'])
    queue.studio.set_queue_paused(False)
    asyncio.run(queue.advance())
    assert queue.get(row['id'])['state'] == 'cancelled'
    assert queue.studio.list_jobs() == []


def test_uncertain_context_and_unresolved_evidence_never_render(setup):
    queue, request = setup
    row = queue.create(request)
    asyncio.run(queue.advance())
    queue.context.db.update(row['context_id'], 'needs_reconciliation', error='unknown acceptance')
    asyncio.run(queue.advance())
    assert queue.studio.list_jobs() == []
    queue.context.db.update(row['context_id'], 'needs_input', error='Who is the performer?')
    asyncio.run(queue.advance())
    assert queue.get(row['id'])['state'] == 'needs_input'
    assert queue.studio.list_jobs() == []


def test_recovery_after_child_insert_before_parent_update(setup):
    queue, request = setup
    row = queue.create(request)
    asyncio.run(queue.advance()); prepare(queue, row)
    original = queue.update
    def crash(uid, **fields):
        if fields.get('state') == 'rendering': raise RuntimeError('simulated process death')
        return original(uid, **fields)
    with patch.object(queue, 'update', crash), pytest.raises(RuntimeError): asyncio.run(queue.advance())
    assert len(queue.studio.list_jobs()) == 1
    asyncio.run(queue.advance())
    assert len(queue.studio.list_jobs()) == 1
    assert queue.get(row['id'])['state'] == 'rendering'


def test_budget_stops_local_child_and_preserves_remote_unknown(setup):
    queue, request = setup
    row = queue.create(request)
    asyncio.run(queue.advance()); prepare(queue, row); asyncio.run(queue.advance())
    row = queue.get(row['id'])
    queue.update(row['id'], started_at=1)
    asyncio.run(queue.advance())
    assert queue.get(row['id'])['state'] == 'budget_exhausted'
    assert queue.studio.get_job(row['child_id'])['state'] == 'cancelled'


def test_stop_keeps_an_ambiguous_remote_job_for_reconciliation(setup):
    queue,request=setup
    row=queue.create(request);asyncio.run(queue.advance());prepare(queue,row);asyncio.run(queue.advance())
    row=queue.get(row['id'])
    with queue.studio.connect() as db:
        db.execute("UPDATE jobs SET state='needs_reconciliation' WHERE job_id=?",(row['child_id'],))
    queue.cancel(row['id']);asyncio.run(queue.advance())
    assert queue.get(row['id'])['state']=='cancelling'
    assert queue.studio.get_job(row['child_id'])['state']=='needs_reconciliation'
    assert len(queue.studio.list_jobs())==1


def test_visual_review_is_left_to_the_agent_and_never_holds_the_gpu(setup):
    queue, request = setup
    row = queue.create(request)
    queue.update(row['id'], state='awaiting_qc', result={'quality': {}})
    asyncio.run(queue.run_qc_once())
    done = queue.get(row['id'])
    assert done['state'] == 'completed' and queue.first_qc() is None
    assert done['result']['quality']['visual']['status'] == 'manual'
    assert 'job_frames' in done['result']['quality']['visual']['finding']
    stopped = queue.create(request)
    queue.update(stopped['id'], state='awaiting_qc', result={'quality': {}}, stop_requested=True)
    asyncio.run(queue.run_qc_once())
    assert queue.get(stopped['id'])['state'] == 'cancelled'


def test_qc_restart_does_not_repeat_ambiguous_gpu_request(setup):
    queue, request = setup
    row = queue.create(request)
    queue.update(row['id'], state='checking', result={'quality': {}})
    asyncio.run(queue.run_qc_once())
    result = queue.get(row['id'])
    assert result['state'] == 'completed'
    assert result['result']['quality']['visual']['status'] == 'unknown'
    assert queue.first_qc() is None


def test_qc_dispatch_respects_active_render_and_pause():
    from local_studio.main import next_dispatch
    args = dict(h3_active=None, h3_pending=None, upscale=None, audio=None,
                paused=False, production_qc={'state': 'queued_local', 'created_at': 1})
    assert next_dispatch(**args) == 'production_qc'
    assert next_dispatch(**(args | {'paused': True})) is None
    assert next_dispatch(**(args | {'h3_active': {'state': 'running'}})) == 'h3'
    assert next_dispatch(**(args | {'paused': True, 'production_qc': {'state': 'running'}})) == 'production_qc'


def test_cancel_keeps_remote_context_receipt_until_reconciled(setup):
    queue, request = setup
    row = queue.create(request)
    asyncio.run(queue.advance())
    wire = {'wire': {'receipt': 'must-survive'}}
    queue.context.db.update(row['context_id'], 'running', result=wire)
    queue.cancel(row['id'])
    asyncio.run(queue.advance())
    assert queue.get(row['id'])['state'] == 'cancelling'
    assert queue.context.db.get(row['context_id'])['result'] == wire
    assert queue.context.first_active()['id'] == row['context_id']
    queue.context.db.update(row['context_id'], 'failed', error='remote finished')
    asyncio.run(queue.advance())
    assert queue.get(row['id'])['state'] == 'cancelled'
    assert queue.studio.list_jobs() == []


def test_production_prepares_and_reuses_actual_source_window(setup):
    import shutil
    import subprocess
    if not shutil.which('ffmpeg'): pytest.skip('FFmpeg required')
    queue, request = setup
    path = queue.studio.assets_dir / 'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=blue:s=32x32:r=24:d=10',
                    '-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
    asset = queue.studio.add_asset(kind='video',role='Движение',original_name='source.mp4',mime='video/mp4',
        size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),path=path)
    payload = request.model_dump(mode='json')
    payload['references'].append(dict(asset_id=asset['asset_id'],kind='video',role='Движение',
                                     owner='ADAM',performer='the person',number=1))
    payload['reference_windows'] = {asset['asset_id']:dict(start=5,duration=5)}
    request = ProductionInput.model_validate(payload)
    row = queue.create(request)
    prepared = row['snapshot']['references'][1]
    assert prepared['asset_id'] != asset['asset_id']
    assert prepared['duration_seconds'] == pytest.approx(5,abs=0.1)
    assert prepared['owner'] == 'ADAM' and prepared['number'] == 1
    assert row['recipe']['prepared_references'][0]['start'] == 5
    other = queue.create(request.model_copy(update={'client_request_id':uuid.uuid4()}))
    assert other['snapshot']['references'][1]['asset_id'] == prepared['asset_id']
    asyncio.run(queue.advance()); prepare(queue,row); asyncio.run(queue.advance())
    child = queue.studio.get_job(queue.get(row['id'])['child_id'])
    video_ids = [v['asset_id'] for v in json.loads(child['manifest_json'])['videos']]
    assert prepared['asset_id'] in video_ids
    assert asset['asset_id'] not in video_ids
    assert hashlib.sha256(path.read_bytes()).hexdigest() == asset['sha256']


def test_history_keeps_old_active_task_and_exposes_only_reference_manifest(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from local_studio.production import make_router
    queue, request = setup
    row = queue.create(request)
    finished = [{**row, 'id':str(uuid.uuid4()), 'state':'completed'} for _ in range(55)]
    app = FastAPI(); app.include_router(make_router(queue, asyncio.Lock()))
    with patch.object(queue, 'list', return_value=[row, *finished]), TestClient(app) as client:
        rows=client.get('/api/productions').json()
    assert len(rows)==51
    assert rows[0]['id']==row['id']
    assert rows[0]['references'][0]['asset_id']==request.references[0].asset_id
    assert 'sha256' not in rows[0]['references'][0]
    assert 'snapshot' not in rows[0] and 'request_hash' not in rows[0]

