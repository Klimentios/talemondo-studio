import pytest
from pydantic import ValidationError
from local_studio.motion_director import RerunPlan
from local_studio.motion_director import CompileTimeline
import json
import asyncio
import hashlib
from types import SimpleNamespace
import httpx
from local_studio.motion_director import upload_image, upload_video
from local_studio.motion_director import GraphPlan
from scripts.h3_graph_validate import validate


def test_video_upload_chunks_verified_snapshot_and_stops_on_bad_ack(tmp_path):
    video = tmp_path / "content-hash-without-extension"
    video.write_bytes(b"x" * (512 * 1024 + 17))
    asset = {"kind": "video", "path": str(video), "original_name": "source.mp4",
             "sha256": hashlib.sha256(video.read_bytes()).hexdigest()}
    store = SimpleNamespace(assets_dir=tmp_path, get_asset=lambda uid: asset)
    calls = []
    bad_ack = False
    def respond(request):
        assert request.url.path == "/minimax/motion-director/upload_chunk"
        calls.append(request.content)
        assert (asset["sha256"] + ".mp4").encode() in request.content
        if len(calls) == 1:
            return httpx.Response(200, json={"status": "ok", "chunk_index": 99 if bad_ack else 0})
        return httpx.Response(200, json={"type": "input", "name": "confirmed.mp4"})
    async def scenario():
        nonlocal bad_ack
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await upload_video(store, client, "http://comfy", "source")
            assert result["video_file"] == "confirmed.mp4" and result["sha256"] == asset["sha256"]
            assert result["bytes"] == 512 * 1024 + 17 and not result["submitted"]
            assert len(calls) == 2
            calls.clear()
            bad_ack = True
            with pytest.raises(ValueError, match="часть видео"):
                await upload_video(store, client, "http://comfy", "source")
            assert len(calls) == 1
            calls.clear()
            video.write_bytes(b"changed")
            with pytest.raises(ValueError, match="изменилось"):
                await upload_video(store, client, "http://comfy", "source")
            assert not calls
    asyncio.run(scenario())


def test_saved_scene_plan_survives_restart_rejects_conflicts_and_invalidates_dependencies(tmp_path):
    from fastapi import FastAPI
    from local_studio.scene_runs import SceneRuns, make_router
    def app():
        instance = FastAPI()
        instance.include_router(make_router(SceneRuns(lambda: tmp_path)))
        return instance
    plan = {"width": 768, "height": 1344, "seed": 42, "segments": [
        {"id": "a", "mode": "t2v", "prompt": "Room", "duration": 5},
        {"id": "b", "mode": "t2v", "prompt": "Continue", "duration": 5, "audio_continuity": True},
        {"id": "c", "mode": "t2v", "prompt": "New scene", "duration": 5}]}
    async def scenario():
        endpoint = "/api/scene-runs/scene-a/motion-plan"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="http://test") as client:
            first = await client.put(endpoint, json={"plan": plan, "expected_revision": 0})
            assert first.status_code == 200 and first.json()["revision"] == 1
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="http://test") as client:
            assert (await client.get(endpoint)).json() == first.json()
            assert (await client.get("/api/scene-runs/scene-b/motion-plan")).status_code == 404
            plan["segments"][0]["prompt"] = "Changed room"
            assert (await client.put(endpoint, json={"plan": plan, "expected_revision": 0})).status_code == 409
            second = await client.put(endpoint, json={"plan": plan, "expected_revision": 1})
            assert second.status_code == 200
            assert second.json()["invalidated_ids"] == ["a", "b"]
            assert not second.json()["submitted"]
    asyncio.run(scenario())


def test_source_video_keeps_selected_duration_and_identity_pictures():
    segment = {"id": "s", "mode": "source_video", "prompt": "<Picture 1> follows <Video 1>", "duration": 5,
               "source_video": {"video_file": "studio/source.mp4", "start": 3, "end": 8},
               "pictures": [{"asset_id": "hero", "image_file": "studio/hero.png"}],
               "replacements": [{"owner": "TARGET", "performer": "the source actor", "body_asset_id": "hero"}]}
    result = CompileTimeline(width=768, height=1344, segments=[segment]).compile()
    shot = json.loads(result["timeline_data"])["segments"][0]
    assert shot["frameCount"] == 120
    assert shot["inputs"]["sourceVideo"]["range"] == {"startSec": 3, "endSec": 8}
    assert "assetId" not in shot["inputs"]["sourceVideo"]
    assert shot["inputs"]["identityPictures"][0]["assetId"] == "hero"
    assert "TARGET: face from <Picture 1>" in shot["prompt"] and "mapped performer" in shot["prompt"]
    with pytest.raises(ValidationError):
        CompileTimeline(width=768, height=1344, segments=[{**segment, "duration": 6}])


def test_graph_preflight_rejects_invalid_output_slot_and_type():
    info = {"Source": {"input": {}, "output": ["IMAGE", "AUDIO"]},
            "Sink": {"input": {"required": {"images": ["IMAGE"]}}, "output": []}}
    graph = {"1": {"class_type": "Source", "inputs": {}},
             "2": {"class_type": "Sink", "inputs": {"images": ["1", 0]}}}
    assert validate(graph, info) == []
    graph["2"]["inputs"]["images"] = ["1", 1]
    assert any("AUDIO" in problem for problem in validate(graph, info))
    graph["2"]["inputs"]["images"] = ["1", 7]
    assert any("отсутствующий выход" in problem for problem in validate(graph, info))


def test_graph_wires_full_quality_model_and_video_audio_outputs():
    plan = GraphPlan(width=768, height=1344, seed=42, segments=[
        {"id": "a", "mode": "t2v", "prompt": "An empty room", "duration": 5}])
    result = plan.graph()
    graph = result["graph"]
    assert graph["1"]["inputs"]["unet_name"] == "minimax_h3_ref2va_bf16.safetensors"
    assert graph["5"]["inputs"]["steps"] == 25 and graph["5"]["inputs"]["seed"] == 42
    assert graph["6"]["inputs"]["audio"] == ["5", 1]
    assert graph["6"]["inputs"]["images"] == ["5", 0]
    assert not result["submitted"] and not result["graph_verified"]


def test_upload_uses_library_hash_and_confirmed_remote_path(tmp_path):
    image = tmp_path / "hero.png"
    image.write_bytes(b"image-fixture")
    asset = {"kind": "image", "path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
    store = SimpleNamespace(assets_dir=tmp_path, get_asset=lambda uid: asset)
    calls = []
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"type": "input", "subfolder": "studio-motion-director", "name": "confirmed.png"})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await upload_image(store, client, "http://comfy", "hero")
            assert result["image_file"] == "studio-motion-director/confirmed.png"
            assert result["submitted"] is False
            image.write_bytes(b"changed")
            with pytest.raises(ValueError, match="изменилось"):
                await upload_image(store, client, "http://comfy", "hero")
        assert calls == ["/upload/image"]
    asyncio.run(scenario())


def test_compile_preserves_per_segment_identity_and_native_frame_grid():
    plan = CompileTimeline(width=768, height=1344, segments=[
        {"id": "a", "mode": "r2v", "prompt": "<Picture 1> walks", "duration": 5,
         "pictures": [{"asset_id": "hero", "image_file": "studio/hero.png"}]},
        {"id": "b", "mode": "i2v", "prompt": "continues", "duration": 5,
         "pictures": [{"asset_id": "frame", "image_file": "studio/frame.png"}]}])
    result = plan.compile()
    timeline = json.loads(result["timeline_data"])
    assert result["total_frames"] == 248
    assert timeline["segments"][0]["inputs"]["pictures"][0]["assetId"] == "hero"
    assert timeline["segments"][1]["inputs"]["startFrame"]["assetId"] == "frame"
    assert not result["submitted"] and not result["media_verified"]
    with pytest.raises(ValidationError):
        CompileTimeline(**{**plan.model_dump(), "silently_ignored_setting": True})
    with pytest.raises(ValidationError):
        CompileTimeline(width=768, height=1344, segments=[{**plan.segments[0].model_dump(),
            "pictures": [{"asset_id": "hero", "image_file": "../secret.png"}]}])


def test_rerun_propagates_audio_and_result_dependencies_but_stops_at_cut():
    segments = [{"id": "a", "mode": "r2v"},
                {"id": "b", "mode": "r2v", "audio_continuity": True},
                {"id": "c", "mode": "i2v", "result_sources": ["b"]},
                {"id": "d", "mode": "r2v"}]
    result = RerunPlan(segments=segments, changed_ids=["a"]).plan()
    assert result["rerun_ids"] == ["a", "b", "c"]
    assert result["reusable_ids"] == ["d"]
    assert result["submitted"] is False
    with pytest.raises(ValidationError):
        RerunPlan(segments=[{**segments[0], "result_sources": ["d"]}, *segments[1:]], changed_ids=["a"])


def test_motion_queue_recovers_lost_submit_without_second_generation(tmp_path, monkeypatch):
    from local_studio.store import StudioStore
    from local_studio.motion_jobs import MotionQueue
    from local_studio.dispatcher import next_dispatch
    import local_studio.motion_jobs as module
    store = StudioStore(tmp_path)
    queue = MotionQueue(store, 'http://comfy')
    plan = {'width': 768, 'height': 1344, 'seed': 42, 'segments': [
        {'id': 'a', 'mode': 't2v', 'prompt': 'A quiet room', 'duration': 5}]}
    job = queue.create('scene-a', 1, plan)
    assert queue.create('scene-a', 1, plan)['motion_id'] == job['motion_id']
    with pytest.raises(ValueError, match='уже выполняется'):
        queue.create('scene-a', 2, plan)
    assert next_dispatch(h3_active=None, h3_pending=None, upscale=None, audio=None,
                         motion=job, paused=True) is None
    assert next_dispatch(h3_active=None, h3_pending=None, upscale=None, audio=None,
                         motion=job, paused=False) == 'motion'
    submissions = []
    def respond(request):
        if request.url.path == '/queue':
            return httpx.Response(200, json={'queue_running': [], 'queue_pending': []})
        if request.url.path == '/object_info':
            return httpx.Response(200, json={})
        if request.url.path == '/prompt':
            submissions.append(json.loads(request.content))
            raise httpx.ReadTimeout('lost response')
        if request.url.path == '/history':
            return httpx.Response(200, json={'remote': {'prompt': [0, 'remote', {}, {'studio_motion_id': job['motion_id']}]}})
        if request.url.path == '/history/remote':
            return httpx.Response(200, json={'remote': {'status': {'status_str': 'error', 'messages': ['test failure']}}})
        raise AssertionError(request.url)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(module, 'validate', lambda graph, info: [])
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw))
    async def scenario():
        assert await queue.run_once()
        assert queue.get(job['motion_id'])['state'] == 'needs_reconciliation'
        restarted = MotionQueue(store, 'http://comfy')
        assert not await restarted.run_once()
        assert await restarted.reconcile()
        assert restarted.get(job['motion_id'])['remote_job_id'] == 'remote'
        assert await restarted.run_once()
        assert restarted.get(job['motion_id'])['state'] == 'failed'
        assert len(submissions) == 1
        assert submissions[0]['extra_data']['studio_motion_id'] == job['motion_id']
    asyncio.run(scenario())


def test_motion_download_attaches_video_to_scene_receipt(tmp_path, monkeypatch):
    from local_studio.store import StudioStore
    from local_studio.motion_jobs import MotionQueue, make_router
    from fastapi import FastAPI
    import local_studio.audio_jobs as audio
    import local_studio.motion_jobs as module
    store = StudioStore(tmp_path)
    queue = MotionQueue(store, 'http://comfy')
    job = queue.create('scene-a', 1, {'width': 768, 'height': 1344, 'seed': 42, 'segments': [
        {'id': 'a', 'mode': 't2v', 'prompt': 'A quiet room', 'duration': 5}]})
    queue.update(job['motion_id'], 'queued_gpu', remote_job_id='remote')
    def respond(request):
        if request.url.path == '/history/remote':
            return httpx.Response(200, json={'remote': {'prompt': [0, 'remote', {'7': {'class_type': 'MiniMaxH3MotionDirector', 'inputs': {}}}], 'status': {'completed': True, 'messages': [
                ['execution_start', {'timestamp': 1_000_000}], ['execution_success', {'timestamp': 1_600_000}]]}, 'outputs': {
                '7': {'images': [{'filename': 'result.mp4', 'subfolder': 'video', 'type': 'output'}]}}}})
        if request.url.path == '/h3/studio/execution/remote':
            return httpx.Response(404)
        if request.url.path == '/view':
            assert request.url.params['filename'] == 'result.mp4'
            return httpx.Response(200, content=b'video-fixture')
        raise AssertionError(request.url)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(audio, '_stream_kinds', lambda path: {'video', 'audio'})
    async def scenario():
        assert await queue.run_once()
        result = queue.get(job['motion_id'])
        assert result['state'] == 'completed' and result['scene_id'] == 'scene-a'
        assert Path(result['output_path']).read_bytes() == b'video-fixture'
        assert result['output_sha256'] == hashlib.sha256(b'video-fixture').hexdigest()
        observed_metrics = json.loads(result['metrics_json'])
        assert observed_metrics.pop('execution_observation')['prompt_id'] == 'remote'
        assert observed_metrics == {'segments': 1, 'video_seconds': 5.0, 'gpu_seconds': 600.0,
                                                      'gpu_seconds_per_video_second': 120.0, 'mean_gpu_seconds_per_segment': 600.0}
        compact = queue.scene_jobs('scene-a')
        assert len(compact) == 1 and 'plan_json' not in compact[0] and 'graph_json' not in compact[0]
        assert queue.scene_jobs('scene-b') == []
        from local_studio.side_jobs import side_job_summary
        summary = side_job_summary({'motion': queue})[0]
        assert summary['scene_id'] == 'scene-a' and summary['revision'] == 1
        assert summary['file_url'] == f"/api/studio/motion-director/jobs/{job['motion_id']}/file"
    from pathlib import Path
    asyncio.run(scenario())
    app = FastAPI()
    app.include_router(make_router(SimpleNamespace(data_root=tmp_path, store=store, motion_queue=queue)))
    assert '/api/studio/motion-director/scenes/{scene_id}/execute' in app.openapi()['paths']
    async def check_file():
        async with original_client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            endpoint = f"/api/studio/motion-director/jobs/{job['motion_id']}/file"
            assert (await client.get(endpoint)).content == b'video-fixture'
            Path(queue.get(job['motion_id'])['output_path']).write_bytes(b'edited-video')
            assert (await client.get(endpoint)).status_code == 409
    asyncio.run(check_file())


def test_motion_cancel_waiting_and_dispatch_claim_are_exclusive(tmp_path):
    from local_studio.store import StudioStore
    from local_studio.motion_jobs import MotionQueue
    queue = MotionQueue(StudioStore(tmp_path), 'http://comfy')
    plan = {'width': 768, 'height': 1344, 'seed': 42, 'segments': [
        {'id': 'a', 'mode': 't2v', 'prompt': 'Room', 'duration': 5}]}
    first = queue.create('scene-a', 1, plan)['motion_id']
    with pytest.raises(LookupError):
        queue.cancel_waiting('scene-b', first)
    assert queue.cancel_waiting('scene-a', first)['state'] == 'cancelled'
    assert not queue.claim(first)
    second = queue.create('scene-a', 2, plan)['motion_id']
    assert queue.claim(second)
    with pytest.raises(ValueError, match='уже передаётся'):
        queue.cancel_waiting('scene-a', second)
    assert queue.get(second)['state'] == 'uploading'


def test_second_revision_regenerates_only_changed_segment_in_scene_cache(tmp_path, monkeypatch):
    from local_studio.store import StudioStore
    from local_studio.motion_jobs import MotionQueue, director_id
    import local_studio.motion_jobs as module
    queue = MotionQueue(StudioStore(tmp_path), 'http://comfy')
    segments = [{'id': 'a', 'mode': 't2v', 'prompt': 'A quiet room', 'duration': 5},
                {'id': 'b', 'mode': 't2v', 'prompt': 'The door opens', 'duration': 5, 'visual_continuity': True},
                {'id': 'c', 'mode': 't2v', 'prompt': 'A street', 'duration': 5}]
    plan = {'width': 640, 'height': 1120, 'seed': 7, 'segments': segments}
    first = queue.create('scene-a', 1, plan)
    queue.update(first['motion_id'], 'completed', output_path='done.mp4')
    edited = {**plan, 'segments': [*segments[:2], {**segments[2], 'prompt': 'A rainy street'}]}
    submissions = []
    def respond(request):
        if request.url.path == '/queue':
            return httpx.Response(200, json={'queue_running': [], 'queue_pending': []})
        if request.url.path == '/object_info':
            return httpx.Response(200, json={})
        if request.url.path == '/prompt':
            submissions.append(json.loads(request.content)['prompt'])
            return httpx.Response(200, json={'prompt_id': f'remote-{len(submissions)}'})
        if request.url.path == '/view' and request.method == 'HEAD':
            assert request.url.params['subfolder'] == f"minimax_seg_cache/{director_id('scene-a')}"
            return httpx.Response(200 if request.url.params['filename'] in cached else 404)
        raise AssertionError(request.url)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(module, 'validate', lambda graph, info: [])
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw))
    cached = {'seg_0000.pt', 'seg_0000.audio.pt', 'seg_0001.pt', 'seg_0001.audio.pt'}
    second = queue.create('scene-a', 2, edited)
    asyncio.run(queue.run_once())
    node = submissions[0][director_id('scene-a')]
    timeline = json.loads(node['inputs']['timeline_data'])
    assert timeline['runSelectEnabled'] is True and timeline['runSelection'] == [2]
    assert json.loads(queue.get(second['motion_id'])['metrics_json']) == {'run_selection': ['c']}
    assert director_id('scene-a') != director_id('scene-b')
    queue.update(second['motion_id'], 'completed', output_path='done2.mp4')
    full = queue.create('scene-a', 3, {**edited, 'segments': [*edited['segments'][:2], {**segments[2], 'prompt': 'Snow'}]}, full_run=True)
    asyncio.run(queue.run_once())
    timeline = json.loads(submissions[1][director_id('scene-a')]['inputs']['timeline_data'])
    assert timeline['runSelectEnabled'] is False and json.loads(queue.get(full['motion_id'])['metrics_json']) == {'run_selection': 'all'}
    cached.clear()  # A new GPU server has no segment cache: the partial run becomes a full one.
    queue.update(full['motion_id'], 'completed', output_path='done3.mp4')
    moved = queue.create('scene-a', 4, {**edited, 'segments': [*edited['segments'][:2], {**segments[2], 'prompt': 'Fog'}]})
    asyncio.run(queue.run_once())
    assert json.loads(submissions[2][director_id('scene-a')]['inputs']['timeline_data'])['runSelectEnabled'] is False
    assert json.loads(queue.get(moved['motion_id'])['metrics_json']) == {'run_selection': 'all'}


def test_source_replacement_requires_bound_target_proportions_before_graph_compilation():
    base = {'id': 's', 'mode': 'source_video', 'prompt': 'Retarget the action', 'duration': 5,
            'source_video': {'video_file': 'source.mp4', 'start': 0, 'end': 5},
            'pictures': [{'asset_id': 'hero', 'image_file': 'hero.png'}]}
    with pytest.raises(ValidationError, match='replacements'):
        CompileTimeline(width=768, height=1344, segments=[base])
    with pytest.raises(ValidationError, match='pictures'):
        CompileTimeline(width=768, height=1344, segments=[{**base, 'replacements': [
            {'owner': 'TARGET', 'performer': 'source actor', 'body_asset_id': 'missing'}]}])
    shared = {**base, 'replacements': [
        {'owner': 'A', 'performer': 'first actor', 'body_asset_id': 'hero'},
        {'owner': 'B', 'performer': 'second actor', 'body_asset_id': 'hero'}]}
    with pytest.raises(ValidationError, match='полный образ'):
        CompileTimeline(width=768, height=1344, segments=[shared])
    same = {**base, 'replacements': [{'owner': 'TARGET', 'performer': 'source actor',
                                      'body_asset_id': 'hero', 'face_asset_id': 'hero'}]}
    compiled = CompileTimeline(width=768, height=1344, segments=[same]).compile()
    assert 'face from <Picture 1>' in json.loads(compiled['timeline_data'])['segments'][0]['prompt']
    with pytest.raises(ValueError, match='референсы целевого героя'):
        CompileTimeline(width=768, height=1344, segments=[
            {**same, 'prompt': 'Keep the source body proportions.'}]).compile()
