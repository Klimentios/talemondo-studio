"""Contract/queue tests: synthetic model responses, no GPU generation."""
import asyncio
import base64
import copy
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock
from contextlib import nullcontext
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.context_ir.schema import Snapshot, ScenePlan, ReferenceChange, compile_plan, digest, VERSION
from app.context_ir.store import Receipts
from app.context_ir.router import capability
from app.context_ir import engine
from app.h3_ir import is_blocking
from local_studio.context_ir import ContextQueue, Input, make_router
from local_studio.store import StudioStore
from tests.test_render_golden import PNG, BASE_SETTINGS, _gpu_submission


def plan(snapshot):
    return ScenePlan(summary='The hero raises a hand and smiles.',
        evidence=[{'asset_id': a.asset_id, 'observation': 'A person in a dark coat.', 'status': 'observed'} for a in snapshot.references],
        beats=[{'initial_state': '<Subject 1> stands beside the window.', 'action': '<Subject 1> raises one hand.',
                'final_state': '<Subject 1> lowers the hand.',
                'description': 'The camera holds a steady medium shot. The face remains in sharp focus. '
                               '<Subject 1> says <d>[Russian] Привет.</d>'} for _ in range(snapshot.shots)],
        overall_soundscape='Quiet room ambience and natural speech.', non_diegetic_music='A quiet sustained melody.')


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.no_live_agent = patch("local_studio.agent_tasks._ask", side_effect=AssertionError("Real CLI forbidden in tests"))
        self.no_live_agent.start()
        self.addCleanup(self.no_live_agent.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.studio = StudioStore(self.root)
        path = self.studio.assets_dir / 'hero.png'
        path.write_bytes(PNG)
        self.asset = self.studio.add_asset(kind='image', role='Персонаж целиком', original_name='hero.png',
            mime='image/png', size=len(PNG), sha256=hashlib.sha256(PNG).hexdigest(), path=path)
        self.queue = ContextQueue(self.studio, 'http://gpu')
        self.input = Input(client_request_id=uuid.uuid4(), brief='Адам поднимает руку и говорит «Привет».',
            references=[{'asset_id': self.asset['asset_id'], 'kind': 'image', 'role': 'Персонаж целиком', 'owner': 'ADAM', 'number': 1}],
            settings={**BASE_SETTINGS, 'duration': 5}, required_lines=['Привет.'])
        self.snapshot = self.queue.snapshot(self.input)
        self.cap = capability()
        self.result = compile_plan(self.snapshot, plan(self.snapshot))
        self.result.update(provenance=self.cap, input_hash=digest(self.snapshot.cache_input()),
            cache_key=digest([self.snapshot.cache_input(), self.cap['fingerprint']]), model_calls=1)

    def tearDown(self):
        self.temp.cleanup()

    def accept(self, snapshot=None):
        key = str(uuid.uuid4())
        self.queue.db.accept(key, (snapshot or self.snapshot).model_dump())
        return key

    def app(self):
        app = FastAPI()
        async def adapters(): return set()
        app.include_router(make_router(self.queue, adapters))
        return app

    def test_capabilities_use_subscription_without_contacting_gpu(self):
        with self.mock_client(lambda request: (_ for _ in ()).throw(AssertionError('GPU called'))), TestClient(self.app()) as client:
            result = client.get('/api/context-ir/capabilities').json()
        self.assertEqual(result['backend'], 'subscription_agent')
        self.assertFalse(result['preparation_requires_gpu'])
        self.assertEqual(result['model_revision'], 'unknown')

    def test_agent_preparation_compiles_without_gpu_and_marks_interruption(self):
        key = self.accept()
        with self.mock_client(lambda request: (_ for _ in ()).throw(AssertionError('GPU called'))), \
             patch('local_studio.agent_tasks._ask', new=AsyncMock(return_value=plan(self.snapshot).model_dump_json())):
            asyncio.run(self.queue.run_once())
        row = self.queue.db.get(key)
        self.assertEqual(row['state'], 'ready')
        self.assertEqual(row['result']['backend'], 'subscription_agent')
        self.assertEqual(row['result']['provenance']['identity']['model_revision'], 'unknown')
        interrupted = self.accept()
        self.queue.db.update(interrupted, 'running', result={'backend': 'subscription_agent'})
        asyncio.run(self.queue.reconcile())
        self.assertEqual(self.queue.db.get(interrupted)['state'], 'needs_reconciliation')
        self.assertIsNone(self.queue.first_active())

    def test_compiler_owns_subjects_and_rejects_missing_line_unknown_label_and_asset(self):
        result = compile_plan(self.snapshot, plan(self.snapshot))
        self.assertFalse([p for p in result['problems'] if is_blocking(p)])
        self.assertIn('<Subject 1> is ADAM', result['prompt'])
        bad = plan(self.snapshot)
        bad.beats[0].description = 'Someone uses <Picture 9>.'
        result = compile_plan(self.snapshot, bad)
        self.assertTrue(any('метки' in p for p in result['problems']))
        self.assertTrue(any('реплика' in p for p in result['problems']))
        bad.evidence = []
        with self.assertRaises(ValueError): compile_plan(self.snapshot, bad)

    def test_ownership_and_new_files_fail_closed(self):
        request = self.input.model_copy(deep=True)
        request.references[0].owner = ''
        with self.assertRaises(ValueError): self.queue.snapshot(request)
        Path(self.asset['path']).write_bytes(PNG + b'changed')
        with self.assertRaises(ValueError): self.queue.verify_files(self.snapshot)

    def test_seed_reuses_context_but_dialogue_owner_and_file_do_not(self):
        original = digest(self.snapshot.cache_input())
        changed = self.snapshot.model_copy(deep=True)
        changed.settings['seed'] = 12
        self.assertEqual(original, digest(changed.cache_input()))
        for change in ('brief', 'owner', 'sha256'):
            changed = self.snapshot.model_copy(deep=True)
            if change == 'brief': changed.brief += ' Улыбается.'
            elif change == 'owner': changed.references[0].owner = 'MARTIN'
            else: changed.references[0].sha256 = 'a' * 64
            self.assertNotEqual(original, digest(changed.cache_input()))

    def test_settings_camera_validation_and_multiple_subject_ownership(self):
        from tests.test_render_golden import DOLLY, DIRECTOR_CONFIG
        request = self.input.model_copy(deep=True)
        request.settings.update(camera_state=DOLLY, director_config=DIRECTOR_CONFIG,
                                camera_optics={'sensor_format': 'full_frame'})
        self.assertEqual(self.queue.snapshot(request).settings['camera_state'], DOLLY)
        request.settings['camera_state'] = {'version': 2, 'keys': [{}]}
        with self.assertRaises(ValueError): self.queue.snapshot(request)
        data = self.snapshot.model_dump()
        other = {**data['references'][0], 'asset_id': 'other-hero', 'owner': 'MARTIN', 'number': 2}
        costume = {**other, 'asset_id': 'costume', 'role': 'Костюм', 'number': 3}
        data['references'] += [other, costume]
        candidate = Snapshot.model_validate(data)
        result = compile_plan(candidate, plan(candidate))
        self.assertIn('<Picture 3>', result['prompt'])
        data['references'][2]['owner'] = ''
        with self.assertRaises(ValueError): Snapshot.model_validate(data)

    def test_receipts_are_durable_and_do_not_accept_changed_snapshot(self):
        key = self.accept()
        db = Receipts(self.queue.db.path)
        _, created = db.accept(key, self.snapshot.model_dump())
        self.assertFalse(created)
        with self.assertRaises(ValueError): db.accept(key, {'brief': 'different'})
        db.update(key, 'cancelled', expected={'queued_local'})
        self.assertFalse(db.update(key, 'ready', result=self.result, expected={'running'}))

    def mock_client(self, handler):
        real = httpx.AsyncClient
        return patch('httpx.AsyncClient', lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw))

    def test_queue_cache_hit_does_not_submit_or_prepare_images(self):
        old = self.accept()
        self.queue.db.update(old, 'ready', result=self.result)
        key = self.accept()
        self.queue.db.update(key, 'queued_local', result={'wire': {'fingerprint': self.cap['fingerprint']}})
        calls = []
        def handler(req):
            calls.append(req.method)
            return httpx.Response(200, json={'context_ir_v1': self.cap})
        with self.mock_client(handler), patch('local_studio.context_ir.preview_jpeg', side_effect=AssertionError('cache missed')):
            asyncio.run(self.queue.run_once())
        row = self.queue.db.get(key)
        self.assertEqual(row['state'], 'ready')
        self.assertTrue(row['result']['cache_hit'])
        self.assertEqual(calls, ['GET'])

    def test_uncertain_post_is_reconciled_without_second_post(self):
        key = self.accept()
        self.queue.db.update(key, 'queued_local', result={'wire': {'fingerprint': self.cap['fingerprint']}})
        calls = []
        def handler(req):
            calls.append((req.method, req.url.path))
            if req.url.path == '/api/capabilities': return httpx.Response(200, json={'context_ir_v1': self.cap})
            if req.method == 'POST': raise httpx.ReadTimeout('lost response', request=req)
            return httpx.Response(200, json={'state': 'ready', 'result': self.result})
        with self.mock_client(handler), patch('local_studio.context_ir.preview_jpeg', return_value=base64.b64encode(PNG).decode()):
            asyncio.run(self.queue.run_once())
            self.assertEqual(self.queue.db.get(key)['state'], 'needs_reconciliation')
            asyncio.run(self.queue.reconcile())
        self.assertEqual(self.queue.db.get(key)['state'], 'ready')
        self.assertEqual(sum(method == 'POST' for method, _ in calls), 1)

    def test_missing_remote_receipt_does_not_requeue(self):
        key = self.accept()
        self.queue.db.update(key, 'running', result={'wire': {'fingerprint': self.cap['fingerprint']}})
        with self.mock_client(lambda req: httpx.Response(404)):
            asyncio.run(self.queue.run_once())
        self.assertEqual(self.queue.db.get(key)['state'], 'needs_reconciliation')
        self.assertIsNone(self.queue.first_active())

    def test_tampered_model_result_is_rejected(self):
        bad = copy.deepcopy(self.result)
        bad['prompt'] += ' different'
        with self.assertRaises(ValueError): self.queue.validate_result(self.snapshot, bad)

    def render(self, key, client=None, request_id=None):
        client = client or TestClient(self.app())
        return client.post(f'/api/context-ir/jobs/{key}/render', json={'client_request_id': request_id or str(uuid.uuid4())})

    def test_render_receipt_and_real_worker_to_graph_bypass_qwen(self):
        key = self.accept()
        self.queue.db.update(key, 'ready', result=self.result)
        client = TestClient(self.app())
        receipt = str(uuid.uuid4())
        response = self.render(key, client, receipt)
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertEqual(self.render(key, client, receipt).json()['job_id'], job['job_id'])
        self.assertEqual(len(self.studio.list_jobs()), 1)
        settings = json.loads(job['settings_json'])
        self.assertFalse(settings['emotional_direction'])
        self.assertEqual(settings['context_ir']['artifact_id'], key)
        submission = _gpu_submission(self.studio, job, self.temp.name, 'fails')
        self.assertEqual(submission['qwen_calls'], [])
        effective = submission['graph']['136']['inputs']['prompt']
        self.assertIn('Привет.', effective)
        self.assertIn('<Subject 1> is ADAM', effective)
        self.assertIn('ir_execution', submission['response']['director'])

    def test_cancel_and_missing_capability_are_visible(self):
        key = self.accept()
        client = TestClient(self.app())
        self.assertEqual(client.post(f'/api/context-ir/jobs/{key}/cancel').status_code, 200)
        self.assertEqual(self.render(key, client).status_code, 409)
        key = self.accept()
        self.queue.db.update(key, 'queued_local', result={'wire': {'fingerprint': self.cap['fingerprint']}})
        with self.mock_client(lambda req: httpx.Response(200, json={})):
            asyncio.run(self.queue.run_once())
        self.assertEqual(self.queue.db.get(key)['state'], 'failed')

    def test_video_reference_survives_context_worker_and_graph(self):
        path = self.studio.assets_dir / 'movement.mp4'
        raw = b'\x00\x00\x00\x18ftypmp42 synthetic motion fixture'
        path.write_bytes(raw)
        asset = self.studio.add_asset(kind='video', role='Движение', original_name='movement.mp4',
            mime='video/mp4', size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)
        request = self.input.model_copy(deep=True)
        from local_studio.context_ir import InputReference
        request.references.append(InputReference(asset_id=asset['asset_id'],kind='video',number=1,
                                  role='Движение',owner='ADAM',performer='the person in the centre'))
        request.settings['mode'] = 'realistic'
        request.change = 'Перенести движение на Адам, сохранив лицо и костюм с фото.'
        with patch('local_studio.context_ir._media_duration_seconds', return_value=5):
            snapshot = self.queue.snapshot(request)
        result = compile_plan(snapshot, plan(snapshot))
        self.assertIn('attribute_transfer', result['prompt'])
        job = self.studio.create_job(prompt=result['prompt'],
            asset_ids=[a.asset_id for a in snapshot.references], settings=snapshot.settings,
            reference_numbers={a.asset_id: a.number for a in snapshot.references},
            reference_roles={a.asset_id: a.role for a in snapshot.references},
            reference_owners={a.asset_id: a.owner for a in snapshot.references},
            reference_performers={asset['asset_id']: {'pairs': [{'owner':'ADAM','performer':'the person in the centre'}]}},
            style_adapters={'character_swap'})
        submission = _gpu_submission(self.studio, job, self.temp.name, 'fails')
        self.assertEqual(submission['qwen_calls'], [])
        self.assertEqual(len(submission['prepared_videos']), 1)
        self.assertIn('<Video 1>', submission['graph']['136']['inputs']['prompt'])
        self.assertEqual(json.loads(job['manifest_json'])['videos'][0]['owner'], 'ADAM')

    def test_reference_changes_require_a_user_instruction(self):
        from app.context_ir.schema import ReferenceChange
        snapshot = self.snapshot.model_copy(deep=True)
        snapshot.change = 'Заменить тёмный костюм на белый.'
        candidate = plan(snapshot)
        candidate.reference_changes = [ReferenceChange(asset_id=self.asset['asset_id'],
            instruction_quote=snapshot.change, description='The costume becomes white; face and body remain as referenced.')]
        result = compile_plan(snapshot, candidate)
        self.assertIn('partially_preserved', result['prompt'])
        self.assertFalse([p for p in result['problems'] if is_blocking(p)])
        candidate.reference_changes[0].instruction_quote = 'Invented request'
        with self.assertRaises(ValueError): compile_plan(snapshot, candidate)

    def test_gpu_receipt_survives_duplicate_request_and_restart(self):
        import threading
        from app.context_ir import router as remote
        async def scenario():
            gate = threading.Event()
            def generate(*args):
                gate.wait(timeout=3)
                return copy.deepcopy(self.result)
            with patch.dict('os.environ', {'H3_CONTEXT_REGISTRY_PATH': str(self.root / 'gpu.sqlite3')}), \
                 patch.object(remote, 'generate', side_effect=generate) as run:
                router = remote.make_router()
                create = next(r.endpoint for r in router.routes if 'POST' in r.methods)
                status = next(r.endpoint for r in router.routes if 'GET' in r.methods)
                request = remote.Request(client_request_id=uuid.uuid4(), snapshot=self.snapshot,
                    images={self.asset['asset_id']: base64.b64encode(PNG).decode()}, fingerprint=self.cap['fingerprint'])
                first = await create(request)
                second = await create(request)
                self.assertEqual(first['id'], second['id'])
                self.assertEqual(second['state'], 'running')
                gate.set()
                for _ in range(100):
                    result = await status(request.client_request_id)
                    if result['state'] == 'ready': break
                    await asyncio.sleep(.01)
                self.assertEqual(result['state'], 'ready')
                self.assertEqual(run.call_count, 1)
                self.assertEqual((await create(request))['state'], 'ready')
                db = Receipts(self.root / 'gpu.sqlite3')
                old = str(uuid.uuid4())
                db.accept(old, {'fingerprint': 'previous-process'}, 'running')
                self.assertEqual((await status(uuid.UUID(old)))['state'], 'failed')
        asyncio.run(scenario())

    def test_context_queue_respects_pause_and_existing_render(self):
        from local_studio.main import next_dispatch
        args = dict(h3_active=None, h3_pending=None, upscale=None, audio=None,
                    paused=False, context_ir={'state': 'queued_local', 'created_at': '2026-10-09'})
        self.assertEqual(next_dispatch(**args), 'context_ir')
        self.assertIsNone(next_dispatch(**{**args, 'paused': True}))
        self.assertEqual(next_dispatch(**{**args, 'h3_active': {'state': 'running'}}), 'h3')

    def test_gpu_engine_sleep_on_failure_and_busy_no_wake(self):
        class Director:
            model = 'qwen'
            base_url = 'http://127.0.0.1:8000/v1'
            scenarist_extras = {}
            def __init__(self): self.calls = []
            def _is_sleeping(self): return True
            def _runtime_control(self, name): self.calls.append(name)
        d = Director()
        images = {self.asset['asset_id']: base64.b64encode(PNG).decode()}
        with patch.object(engine, 'LocalQwenDirector', return_value=d), \
             patch.object(engine, 'gpu_admission', nullcontext), patch.object(engine, 'planning_lock', nullcontext), \
             patch.object(engine, 'queue_active', return_value=True):
            with self.assertRaises(engine.GpuBusy): engine.generate(self.snapshot, images, self.cap)
        self.assertEqual(d.calls, [])
        with patch.object(engine, 'LocalQwenDirector', return_value=d), \
             patch.object(engine, 'gpu_admission', nullcontext), patch.object(engine, 'planning_lock', nullcontext), \
             patch.object(engine, 'queue_active', return_value=False), \
             patch('urllib.request.urlopen', side_effect=OSError('network')):
            with self.assertRaises(OSError): engine.generate(self.snapshot, images, self.cap)
        self.assertEqual(d.calls, ['wake_up', 'sleep?level=1'])
        from io import BytesIO
        d.calls.clear()
        answer = {'choices': [{'finish_reason': 'stop', 'message': {'content': plan(self.snapshot).model_dump_json()}}]}
        with patch.object(engine, 'LocalQwenDirector', return_value=d), \
             patch.object(engine, 'gpu_admission', nullcontext), patch.object(engine, 'planning_lock', nullcontext), \
             patch.object(engine, 'queue_active', return_value=False), \
             patch('urllib.request.urlopen', return_value=BytesIO(json.dumps(answer).encode())) as chat:
            result = engine.generate(self.snapshot, images, self.cap)
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(d.calls, ['wake_up', 'sleep?level=1'])
        body = json.loads(chat.call_args.args[0].data)
        self.assertIn('structured_outputs', body)



if __name__ == '__main__': unittest.main()


def test_prepared_evidence_cache_reuses_exact_inputs_and_rebuilds_corruption(tmp_path):
    from local_studio.reference_preparation import prepared_evidence
    path = tmp_path / 'source'; path.write_bytes(b'original')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = []
    def build():
        calls.append(1)
        return [{'time_seconds':0,'jpeg_base64':'test'}]
    first = prepared_evidence(tmp_path,path,sha,'video',5,build)
    assert prepared_evidence(tmp_path,path,sha,'video',5,build) == first
    assert len(calls) == 1
    prepared_evidence(tmp_path,path,sha,'video',6,build)
    assert len(calls) == 2
    for item in (tmp_path/'reference-previews').glob('*.json'): item.write_text('{}')
    assert prepared_evidence(tmp_path,path,sha,'video',5,build) == first
    assert len(calls) == 3
    path.write_bytes(b'changed')
    import pytest
    with pytest.raises(ValueError,match='изменился'):
        prepared_evidence(tmp_path,path,sha,'video',5,build)
    assert len(calls) == 3
