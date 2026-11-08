import base64
import copy
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from local_studio.subscription_bridge import Bridge, Result, Turn


class Hub:
    def __init__(self):
        self.runs = {}
        self.chat = {'id': 'c' * 32, 'title': 'Новый чат', 'messages': []}

    def load(self, cid):
        assert cid == self.chat['id']
        return copy.deepcopy(self.chat)

    def save(self, chat):
        self.chat = copy.deepcopy(chat)


@pytest.fixture
def bridge(tmp_path):
    b = Bridge(SimpleNamespace(root=tmp_path), Hub())
    b.seen = time.time()
    return b


def turn(rid='a' * 32, mode='chat'):
    return Turn(request_id=rid, chat_id='c' * 32, mode=mode, text='Адам на диване')


def test_offline_connector_never_creates_a_turn(bridge):
    bridge.seen = 0
    with pytest.raises(HTTPException) as err:
        bridge.create(turn())
    assert err.value.status_code == 409
    assert bridge.hub.chat['messages'] == []


def test_claim_and_result_are_idempotent_and_do_not_replay(bridge):
    request = bridge.create(turn())
    assert request == bridge.create(turn())
    assert 'token' not in request and 'prompt' not in request
    claimed = bridge.claim()
    assert claimed['state'] == 'claimed'
    assert bridge.claim() is None
    assert bridge.create(turn())['state'] == 'claimed'
    payload = Result(token=claimed['token'], text='Ответ из ChatGPT')
    assert bridge.finish(request['id'], payload)['state'] == 'completed'
    bridge.finish(request['id'], payload)
    assert len(bridge.hub.chat['messages']) == 2
    assert bridge.hub.chat['messages'][-1]['provider'] == 'chatgpt'
    assert not bridge.hub.chat.get('subscription_pending')
    assert bridge.hub.chat['subscription_context_changed']
    assert bridge.claim() is None


def test_wrong_receipt_token_and_conflicting_request_are_rejected(bridge):
    bridge.create(turn())
    claimed = bridge.claim()
    with pytest.raises(HTTPException) as err:
        bridge.finish(claimed['id'], Result(token='wrong', text='x'))
    assert err.value.status_code == 403
    with pytest.raises(HTTPException) as err:
        bridge.create(turn().model_copy(update={'text': 'другой запрос'}))
    assert err.value.status_code == 409


def test_missing_image_records_failure_in_same_chat_without_regeneration(bridge):
    bridge.create(turn(mode='image'))
    claimed = bridge.claim()
    result = bridge.finish(claimed['id'], Result(token=claimed['token'], text='Я рисую'))
    assert result['state'] == 'failed'
    assert bridge.hub.chat['messages'][-1]['error']
    assert bridge.claim() is None


def test_returned_image_is_attached_to_the_assistant_message(bridge):
    bridge.create(turn(mode='image'))
    claimed = bridge.claim()
    bridge.image = lambda encoded, rid, n: {'asset_id': 'actual-image', 'original_name': 'image.png'}
    result = bridge.finish(claimed['id'], Result(token=claimed['token'], images=['image-data']))
    assert result['state'] == 'completed'
    assert bridge.hub.chat['messages'][-1]['attachments'][0]['asset_id'] == 'actual-image'


def test_real_png_is_registered_and_deduplicated(tmp_path):
    from local_studio.store import StudioStore
    b = Bridge(StudioStore(tmp_path), Hub())
    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
    asset = b.image(png, 'a' * 32, 0)
    assert asset['kind'] == 'image'
    assert b.image(png, 'a' * 32, 0)['asset_id'] == asset['asset_id']
    assert len(b.store.list_assets()) == 1


def test_http_roundtrip_persists_image_in_original_chat_without_starting_agent(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from local_studio.agent_chat import make_router as chat_router
    from local_studio.store import StudioStore
    from local_studio.subscription_bridge import make_router
    store = StudioStore(tmp_path)
    chats = chat_router(store)
    router = make_router(store, chats.hub)
    app = FastAPI()
    app.include_router(chats)
    app.include_router(router)
    with TestClient(app) as client:
        chat = client.post('/api/agent-chat/chats', json={'provider': 'codex'}).json()
        assert client.post('/api/subscription-bridge/claim', json={}).json() is None
        request = {**turn(mode='image').model_dump(), 'chat_id': chat['id']}
        row = client.post('/api/subscription-bridge/turns', json=request)
        assert row.status_code == 201
        claim = client.post('/api/subscription-bridge/claim', json={}).json()
        png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
        result = client.post(f"/api/subscription-bridge/turns/{claim['id']}/result", json={'token': claim['token'], 'images': [png]})
        assert result.json()['state'] == 'completed'
        saved = client.get(f"/api/agent-chat/chats/{chat['id']}").json()
        assert len(saved['messages']) == 2
        assert saved['messages'][-1]['attachments'][0]['asset_id'] == store.list_assets()[0]['asset_id']
        assert not saved['running'] and not chats.hub.runs
        assert client.post(f"/api/agent-chat/chats/{chat['id']}/messages", json={'text': 'x', 'interaction': 'image'}).status_code == 409
