import asyncio
from types import SimpleNamespace
import httpx
from fastapi import FastAPI
from local_studio.franchises import make_router


def test_boards_persist_isolate_franchises_and_reject_stale_writes(tmp_path):
    def make_app():
        app = FastAPI()
        app.include_router(make_router(SimpleNamespace(root=tmp_path)))
        return app

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app()), base_url='http://localhost') as client:
            franchises = (await client.get('/api/franchises')).json()
            assert len(franchises) == 4
            fid = franchises[0]['id']
            board = (await client.get(f'/api/franchises/{fid}/boards')).json()[0]
            path = f"/api/franchises/{fid}/boards/{board['id']}"
            content = {'children': [{'id': 'hero-1', 'type': 'geometry', 'studio': {'type': 'character', 'title': 'VEGA', 'body': '', 'state': 'draft', 'asset_ids': []}}]}
            assert (await client.put(path, json={'content': content, 'revision': 0})).json() == {'revision': 1}
            assert (await client.put(path, json={'content': {'children': []}, 'revision': 0})).status_code == 409
            assert (await client.get(path)).json()['content'] == content
            copied = (await client.post(path + '/duplicate')).json()
            copied_path = f"/api/franchises/{fid}/boards/{copied['id']}"
            assert (await client.get(copied_path)).json()['content'] == content
            assert (await client.patch(copied_path + '/state', json={'archived': True})).json() == {'archived': True}
            assert (await client.get(copied_path)).json()['archived'] == 1
            assert (await client.patch(copied_path + '/state', json={'archived': False})).status_code == 200
            assert (await client.get(path + '/previous')).json()['content'] == {'children': []}
            assert (await client.get(f"/api/franchises/{franchises[1]['id']}/boards/{board['id']}")).status_code == 404
            assert (await client.put(path, json={'content': {'children': None}, 'revision': 1})).status_code == 422
            assert (await client.post('/api/franchises', json={'name': '  '})).status_code == 422
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app()), base_url='http://localhost') as client:
            assert (await client.get(path)).json()['content'] == content
            assert len((await client.get('/api/franchises')).json()) == 4
        assert not (tmp_path / 'studio.sqlite3').exists()
    asyncio.run(exercise())


def test_registry_updates_multiple_boards_without_mixing_franchises(tmp_path):
    app = FastAPI()
    app.include_router(make_router(SimpleNamespace(root=tmp_path)))

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
            franchises = (await client.get('/api/franchises')).json()
            fid, other = franchises[0]['id'], franchises[1]['id']
            entity_path = f'/api/franchises/{fid}/entities'
            record = (await client.post(entity_path, json={'type': 'character', 'title': 'VEGA', 'body': 'Версия сериала'})).json()
            eid = record['id']
            boards = (await client.get(f'/api/franchises/{fid}/boards')).json()
            content = {'children': [{'id': 'card', 'type': 'geometry', 'points': [[0,0],[200,100]],
                                    'studio': {'entity_id': eid}, 'text': {'children': [{'text': 'Старое'}]}}]}
            for board in boards:
                assert (await client.put(f"/api/franchises/{fid}/boards/{board['id']}", json={'content': content, 'revision': 0})).status_code == 200
            update = {'type': 'character', 'title': 'VEGA · новый костюм', 'body': 'Только эта франшиза', 'revision': 0}
            assert (await client.put(f'{entity_path}/{eid}', json=update)).status_code == 200
            assert (await client.put(f'{entity_path}/{eid}', json=update)).status_code == 409
            for index, board in enumerate(boards):
                loaded = (await client.get(f"/api/franchises/{fid}/boards/{board['id']}")).json()
                card = loaded['content']['children'][0]
                assert card['studio']['body'] == 'Только эта франшиза'
                assert card['text']['children'][0]['text'] == 'VEGA · новый костюм'
                assert card['points'] == [[0,0],[200,100]]
                assert loaded['revision'] == 1
            other_board = (await client.get(f'/api/franchises/{other}/boards')).json()[0]
            assert (await client.put(f"/api/franchises/{other}/boards/{other_board['id']}", json={'content': content, 'revision': 0})).status_code == 422
            assert (await client.get(f'/api/franchises/{other}/entities/{eid}')).status_code == 404
            assert (await client.get(f'/api/franchises/{other}/entities')).json() == []
            first_board = boards[0]['id']
            assert (await client.put(f'/api/franchises/{fid}/boards/{first_board}', json={'content': {'children': []}, 'revision': 1})).status_code == 200
            assert (await client.get(f'{entity_path}/{eid}')).status_code == 200
    asyncio.run(exercise())
