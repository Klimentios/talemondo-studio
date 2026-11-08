"""The scene gate must not advance on a completed but unreviewed render."""
from types import SimpleNamespace
import asyncio
import tempfile
from pathlib import Path

import httpx

from local_studio import main as local_main
from local_studio.store import StudioStore
from local_studio.studio_readiness import scene_readiness


class FakeStore:
    def __init__(self, scene, job):
        self.scene, self.job = scene, job

    def load_draft_snapshot(self):
        return {"scenes": [self.scene]}, 7

    def get_job(self, job_id):
        return self.job if job_id == "job-1" else None


def test_scene_gate_requires_current_job_and_qc_receipt(tmp_path):
    mp4 = tmp_path / "result.mp4"
    mp4.write_bytes(b"result")
    cut = {"id": "shot-1", "prompt": "Cat enters", "shotJobId": "job-1"}
    scene = {"id": "scene-1", "franchiseSource": {"franchise_id": "f-1"}, "cuts": [cut]}
    job = {"state": "completed", "output_path": str(mp4)}
    run = {"state": "awaiting", "cuts": [cut]}
    store = FakeStore(scene, job)
    runs = SimpleNamespace(load=lambda _: run)

    state = scene_readiness(store, runs, "scene-1")
    assert state["source"] == "scene_run"
    assert state["next_action"] == "review_full_mp4"
    assert state["accepted_shots"] == 0

    cut.update(shotQcReview="accepted", shotQc={"job_id": "other-job", "decision": "accept"})
    assert scene_readiness(store, runs, "scene-1")["accepted_shots"] == 0
    cut["shotQc"]["job_id"] = "job-1"
    assert scene_readiness(store, runs, "scene-1")["accepted_shots"] == 1

    mp4.unlink()
    state = scene_readiness(store, runs, "scene-1")
    assert state["shots"][0]["next_action"] == "reconcile_result"
    assert state["accepted_shots"] == 0


def test_completed_clip_outside_results_is_not_reviewable(tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"clip")
    scene = {"id": "s", "franchiseSource": {"franchise_id": "f"},
             "cuts": [{"id": "c", "shotJobId": "job-1", "shotQcReview": "accepted",
                       "shotQc": {"job_id": "job-1", "decision": "accept"}}]}
    store = FakeStore(scene, {"state": "completed", "output_path": str(outside)})
    store.results_dir = tmp_path / "results"
    result = scene_readiness(store, SimpleNamespace(load=lambda _: None), "s")
    assert result["shots"][0]["next_action"] == "reconcile_result"
    assert result["accepted_shots"] == 0


def test_ambiguous_submission_blocks_progress():
    scene = {"id": "s", "franchiseSource": {"franchise_id": "f"},
             "cuts": [{"id": "c", "prompt": "An action"}]}
    run = {"state": "running", "cuts": scene["cuts"], "pending_submission": {"request_id": "x"}}
    result = scene_readiness(FakeStore(scene, None), SimpleNamespace(load=lambda _: run), "s")
    assert result["next_action"] == "reconcile_scene"
    assert "не запускай повторно" in result["blockers"][0]


def test_next_action_waits_for_first_unfinished_shot():
    scene = {"id": "s", "franchiseSource": {"franchise_id": "f"}, "cuts": [
        {"id": "first", "shotJobId": "job-1"}, {"id": "second", "prompt": "Continue"}]}
    result = scene_readiness(FakeStore(scene, {"state": "running"}),
                             SimpleNamespace(load=lambda _: None), "s")
    assert result["next_action"] == "wait_for_job"


def test_readiness_route_reads_saved_draft_without_gpu():
    with tempfile.TemporaryDirectory() as temp:
        original = local_main.ctx.store
        local_main.ctx.store = StudioStore(Path(temp))
        try:
            local_main.ctx.store.save_draft({"scenes": [{"id": "readiness-test-scene",
                "franchiseSource": {"franchise_id": "f-1"},
                "cuts": [{"id": "shot-1", "prompt": "Cat enters"}]}]}, 0)

            async def exercise():
                transport = httpx.ASGITransport(app=local_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                    response = await client.get("/api/draft/scenes/readiness-test-scene/readiness")
                    assert response.status_code == 200, response.text
                    result = response.json()
                    assert result["source"] == "draft"
                    assert result["draft_revision"] == 1
                    assert result["next_action"] == "preflight_and_request_gpu_gate"
                    assert (await client.get("/api/draft/scenes/no-such-scene/readiness")).status_code == 404
                    report = await client.get('/api/draft/production?franchise_id=f-1')
                    assert report.status_code == 200
                    assert report.json()['draft_revision'] == 1
                    assert report.json()['scenes'][0]['scene_id'] == 'readiness-test-scene'
                    other = await client.get('/api/draft/production?franchise_id=another')
                    assert other.json()['scenes'] == []

            asyncio.run(exercise())
        finally:
            local_main.ctx.store = original


def test_completed_old_scene_is_not_ready_after_saved_shot_revision(tmp_path):
    import copy
    from local_studio.scene_versions import scene_definition
    mp4 = tmp_path / 'result.mp4'
    mp4.write_bytes(b'rendered')
    cut = {'id': 'shot-1', 'prompt': 'Vega waits', 'shotJobId': 'job-1',
           'shotQcReview': 'accepted', 'shotQc': {'job_id': 'job-1', 'decision': 'accept'}}
    run = {'state': 'done', 'cuts': [copy.deepcopy(cut)], 'definition_snapshot': scene_definition([cut])}
    scene = {'id': 's', 'franchiseSource': {'franchise_id': 'f'}, 'cuts': [cut]}
    store = FakeStore(scene, {'state': 'completed', 'output_path': str(mp4)})
    runs = SimpleNamespace(load=lambda _: run)
    assert scene_readiness(store, runs, 's')['definition_version']['matches']
    cut['prompt'] = 'Orion runs'
    result = scene_readiness(store, runs, 's')
    assert not result['definition_version']['matches']
    assert result['next_action'] == 'reconcile_scene' and result['blockers']


def test_production_state_uses_one_saved_revision_and_scopes_franchise():
    from local_studio.studio_readiness import production_state
    class Store:
        reads = 0
        def load_draft_snapshot(self):
            self.reads += 1
            return {'scenes': [
                {'id': 's1', 'franchiseSource': {'franchise_id': 'f1'},
                 'cuts': [{'id': 'c1', 'prompt': 'Act', 'shotJobId': 'j1', 'takes': [{'jobId': 'old'}]}]},
                {'id': 's2', 'franchiseSource': {'franchise_id': 'f2'}, 'cuts': []}]}, 9
        def get_job(self, job_id):
            return {'state': 'running'} if job_id == 'j1' else None
    store = Store()
    result = production_state(store, SimpleNamespace(load=lambda _: None), 'f1')
    assert store.reads == 1 and result['draft_revision'] == 9
    assert [scene['scene_id'] for scene in result['scenes']] == ['s1']
    assert result['scenes'][0]['takes'] == [
        {'shot_id': 'c1', 'job_id': 'old', 'selected': False, 'state': 'missing', 'recorded_output_sha256': None},
        {'shot_id': 'c1', 'job_id': 'j1', 'selected': True, 'state': 'running', 'recorded_output_sha256': None}]
    assert result['file_integrity_verified'] is False and result['automatic_generation'] is False


def test_scene_launch_context_expires_when_reference_or_quality_changes(tmp_path):
    import copy
    from local_studio.scene_versions import context_definition
    scene = {'id': 's', 'franchiseSource': {'franchise_id': 'f'}, 'cuts': [{'id': 'c', 'prompt': 'Act'}],
             'sceneStyle': 'cinema', 'seed': 42, 'renderState': {'steps': 24},
             'referenceState': {'references': [{'id': 'hero', 'owner': 'VEGA', 'role': 'Лицо героя'}]}}
    run = {'state': 'done', 'cuts': copy.deepcopy(scene['cuts']), 'context_snapshot': context_definition(copy.deepcopy(scene))}
    store = FakeStore(scene, None)
    runs = SimpleNamespace(load=lambda _: run)
    assert scene_readiness(store, runs, 's')['context_version']['matches']
    scene['renderState']['steps'] = 25
    changed = scene_readiness(store, runs, 's')
    assert changed['context_version']['matches'] is False
    assert changed['next_action'] == 'reconcile_scene'
    scene['renderState']['steps'] = 24
    scene['referenceState']['references'][0]['owner'] = 'ORION'
    assert scene_readiness(store, runs, 's')['context_version']['matches'] is False
    run.pop('context_snapshot')
    assert scene_readiness(store, runs, 's')['context_version']['matches'] is None


def test_production_overview_includes_motion_versions_without_second_state_store(tmp_path):
    from local_studio.motion_jobs import MotionQueue
    from local_studio.studio_readiness import production_state
    store = StudioStore(tmp_path)
    store.save_draft({'scenes': [{'id': 's1', 'franchiseSource': {'franchise_id': 'f1'},
                                 'cuts': [{'id': 'c1', 'prompt': 'Act'}]}]}, 0)
    queue = MotionQueue(store, '')
    receipt = queue.create('s1', 3, {'width': 768, 'height': 1344, 'seed': 42, 'segments': [
        {'id': 'a', 'mode': 't2v', 'prompt': 'A quiet room', 'duration': 5}]})
    result = production_state(store, SimpleNamespace(load=lambda _: None), 'f1')
    assert result['scenes'][0]['motion_results'] == [{
        'motion_id': receipt['motion_id'], 'revision': 3, 'state': 'queued_local',
        'remote_job_id': None, 'output_sha256': None}]
