import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from local_studio import chat_images as ci
from local_studio.store import StudioStore


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = StudioStore(tmp_path)
    monkeypatch.setattr(ci, 'load_profile', lambda *a: (SimpleNamespace(model_dump=lambda: {'look': 'approved'}), None))
    monkeypatch.setattr(ci, 'get_scene_kit', lambda *a: {'franchise': 'Cats', 'cast': [{'name': 'VEGA', 'sheet': {'scale': 'adult cat'}}]})
    scene = {'id': 'scene', 'title': 'Planet', 'sceneDirector': {'idea': 'VEGA imagines a planet',
             'agentScene': {'franchise_id': 'cats'}}, 'cuts': [{'id': 'shot', 'prompt': 'VEGA looks ahead',
                'sceneShot': {'first_frame': 'VEGA stands on the left, one paw on the icy ridge.'}}],
             'references': [], 'aspect': '9:16'}
    store.save_draft({'scenes': [scene]})
    path = store.assets_dir / 'result.png'
    Image.new('RGB', (16, 24), (40, 90, 150)).save(path)
    raw = path.read_bytes()
    asset = store.add_asset(kind='image', role='Кадр сцены', original_name='result.png', mime='image/png',
                            size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), path=path)
    return store, scene, asset


def test_claim_is_durable_exclusive_and_result_idempotent(setup):
    store, scene, asset = setup
    q = ci.ChatImages(store)
    request = ci.ImageRequest(scene_id='scene', shot_id='shot')
    row = q.create(request)
    assert q.create(request)['id'] == row['id']
    first = q.claim(row['id'], 'chat-a')
    assert first['context']['story'] == 'VEGA imagines a planet'
    assert first['context']['image_brief'] == 'VEGA stands on the left, one paw on the icy ridge.'
    assert first['context']['franchise_kit']['cast'][0]['sheet']['scale'] == 'adult cat'
    assert ci.ChatImages(store).claim(row['id'], 'chat-a')['claim_token'] == first['claim_token']
    with pytest.raises(HTTPException) as other:
        q.claim(row['id'], 'chat-b')
    assert other.value.status_code == 409
    with pytest.raises(HTTPException) as wrong:
        q.finish(row['id'], 'wrong', asset['asset_id'])
    assert wrong.value.status_code == 403
    result = q.finish(row['id'], first['claim_token'], asset['asset_id'])
    assert result['state'] == 'completed' and result['accepted'] is False
    assert result['output_sha256'] == asset['sha256']
    assert 'claim_token' not in result and 'context' not in result
    assert q.finish(row['id'], first['claim_token'], asset['asset_id']) == result


def test_changed_scene_rejects_old_claim_and_marks_late_result_stale(setup):
    store, scene, asset = setup
    q = ci.ChatImages(store)
    row = q.create(ci.ImageRequest(scene_id='scene', shot_id='shot'))
    claim = q.claim(row['id'], 'chat-a')
    scene['cuts'][0]['prompt'] = 'VEGA faces another way'
    store.save_draft({'scenes': [scene]})
    assert q.finish(row['id'], claim['claim_token'], asset['asset_id'])['state'] == 'stale'
    pending = q.create(ci.ImageRequest(scene_id='scene', shot_id='shot'))
    scene['cuts'][0]['prompt'] = 'VEGA sits'
    store.save_draft({'scenes': [scene]})
    with pytest.raises(HTTPException) as stale:
        q.claim(pending['id'], 'chat-b')
    assert stale.value.status_code == 409


def test_fantasy_scope_and_real_http_queue(setup):
    store, scene, asset = setup
    scene['references'] = [{'id': asset['asset_id'], 'kind': 'image', 'role': 'Окружение'}]
    scene['referenceState'] = {'references': scene.pop('references')}
    scene['renderState'] = {'aspect': '9:16'}
    scene['cuts'][0]['fantasy'] = True
    store.save_draft({'scenes': [scene]})
    with pytest.raises(HTTPException):
        ci.image_context(store, ci.ImageRequest(scene_id='scene', shot_id='shot'))
    app = FastAPI(); app.include_router(ci.make_router(SimpleNamespace(store=store)))
    client = TestClient(app)
    body = {'scene_id': 'scene', 'shot_id': 'shot', 'purpose': 'fantasy_world'}
    row = client.post('/api/chat-images', json=body)
    assert row.status_code == 201
    rid = row.json()['id']
    claim = client.post(f'/api/chat-images/{rid}/claim', json={'claim_id': 'worker'}).json()
    assert claim['context']['references'][0]['sha256'] == asset['sha256']
    assert claim['context']['aspect'] == '9:16'
    assert 'empty fantasy world' in claim['context']['instructions']
    listed = client.get('/api/chat-images?scene_id=scene').json()
    assert len(listed) == 1 and 'claim_token' not in listed[0]
    failed = client.post(f'/api/chat-images/{rid}/result', json={'claim_token': claim['claim_token'], 'error': 'Host cannot provide ImageGen file'})
    assert failed.json()['state'] == 'failed'


def test_failed_request_can_be_asked_again_and_missing_draft_is_404(setup, tmp_path):
    store, scene, asset = setup
    q = ci.ChatImages(store)
    request = ci.ImageRequest(scene_id='scene', shot_id='shot')
    row = q.create(request)
    claim = q.claim(row['id'], 'chat-a')
    assert q.finish(row['id'], claim['claim_token'], '', 'ImageGen unavailable')['state'] == 'failed'
    again = q.create(request)
    assert again['id'] != row['id'] and again['state'] == 'waiting_chatgpt'
    with pytest.raises(HTTPException) as missing:
        ci.image_context(StudioStore(tmp_path / 'empty'), request)
    assert missing.value.status_code == 404
