from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio import montage


class Store:
    def __init__(self, root):
        self.root = root


def client(tmp_path):
    app = FastAPI()
    app.include_router(montage.make_router(Store(tmp_path)))
    return TestClient(app)


def test_empty_montage_saves_and_agent_edit_bumps_revision(tmp_path):
    api = client(tmp_path)
    made = api.post("/api/montages", json={"name": "Пилот"}).json()
    project, revision = made["project"], made["revision"]
    assert project["edl"]["clips"] == [] and project["edl"]["loudness"] == -14
    assert api.get("/api/montages").json()["active"] == project["id"]
    project["edl"]["clips"] = [{"job_id": "a", "out": 3}, {"job_id": "b", "transition_in": "whip_pan",
                                                           "accents": [{"type": "vhs"}]}]
    saved = api.put(f"/api/montages/{project['id']}", json={"project": project, "revision": revision})
    assert saved.status_code == 200, saved.text
    # the user's stale tab cannot overwrite the agent's change
    project["edl"]["look"] = "noir"
    agent = api.put(f"/api/montages/{project['id']}", json={"project": project, "by": "agent", "note": "нуар"})
    assert agent.status_code == 200
    stale = api.put(f"/api/montages/{project['id']}", json={"project": project, "revision": saved.json()["revision"]})
    assert stale.status_code == 409
    seen = api.get(f"/api/montages/{project['id']}/revision").json()
    assert seen == {**seen, "revision": agent.json()["revision"], "by": "agent", "note": "нуар"}
    back = api.get(f"/api/montages/{project['id']}").json()["project"]["edl"]
    assert back["clips"][0]["out"] == 3 and back["clips"][1]["accents"][0]["type"] == "vhs"


def test_bad_sheet_is_refused_with_its_field(tmp_path):
    api = client(tmp_path)
    project = api.post("/api/montages", json={}).json()["project"]
    project["edl"]["clips"] = [{"job_id": "a", "transition_in": "teleport"}]
    response = api.put(f"/api/montages/{project['id']}", json={"project": project})
    assert response.status_code == 422 and "teleport" in response.json()["detail"]
    project["edl"]["clips"] = [{"job_id": "a", "accents": [{"type": "explode"}]}]
    assert api.put(f"/api/montages/{project['id']}", json={"project": project}).status_code == 422


def test_append_shots_from_the_story(tmp_path):
    api = client(tmp_path)
    project = api.post("/api/montages", json={}).json()["project"]
    out = api.post(f"/api/montages/{project['id']}/append",
                   json={"shots": [{"job_id": "a", "duration": 4}, {"job_id": "b", "transition_in": "dissolve"}]}).json()
    clips = out["project"]["edl"]["clips"]
    assert [(c["job_id"], c["out"], c["transition_in"]) for c in clips] == [("a", 4, "cut"), ("b", None, "dissolve")]


def test_old_manual_timeline_is_imported_once(tmp_path):
    old = tmp_path / "editor_projects"
    old.mkdir()
    (old / "current.json").write_text('{"version":1,"name":"Старый","aspectRatio":"16:9","tracks":['
        '{"id":"video1","clips":[{"type":"video","assetId":"j1","name":"Шот","timelineStart":0,"sourceStart":1,'
        '"sourceEnd":4,"speed":1,"volume":0,"aiMetadata":{"generationId":"j1"}},'
        '{"type":"video","assetId":"f2","name":"Файл","timelineStart":3,"sourceStart":0,"sourceEnd":2,"speed":1,'
        '"volume":1,"transition":"fade"}]},'
        '{"id":"sourceAudio","clips":[{"assetId":"j1","volume":0.8}]},'
        '{"id":"music","clips":[{"assetId":"m","timelineStart":0,"sourceStart":0,"volume":0.3}]}]}')
    api = client(tmp_path)
    listing = api.get("/api/montages").json()
    assert len(listing["montages"]) == 1
    edl = api.get(f"/api/montages/{listing['active']}").json()["project"]["edl"]
    assert edl["aspect_ratio"] == "16:9" and edl["music"]["asset_id"] == "m"
    assert [(c["job_id"], c["asset_id"], c["in"], c["volume"], c["transition_in"]) for c in edl["clips"]] == [
        ("j1", "", 1, 0.8, "cut"), ("", "f2", 0, 1, "dissolve")]
    assert len(client(tmp_path).get("/api/montages").json()["montages"]) == 1   # not imported twice


def test_agent_operations_are_atomic_and_revision_bound(tmp_path):
    api = client(tmp_path)
    made = api.post('/api/montages', json={'edl': {**montage.blank_edl(), 'clips': [
        {'job_id': 'a', 'out': 4}, {'job_id': 'b', 'out': 5}]}}).json()
    url = f"/api/montages/{made['project']['id']}"
    body = {'revision': made['revision'], 'note': 'Переставил и обрезал', 'ops': [
        {'op': 'moveClip', 'index': 1, 'to': 0},
        {'op': 'trimClip', 'index': 0, 'source_in': 1, 'source_out': 3},
        {'op': 'setTransition', 'index': 1, 'transition': 'dissolve', 'seconds': .3}]}
    changed = api.post(url + '/edit', json=body)
    assert changed.status_code == 200, changed.text
    project = changed.json()['project']
    assert [c['job_id'] for c in project['edl']['clips']] == ['b', 'a']
    assert project['edl']['clips'][0]['in'] == 1
    assert project['edl']['clips'][1]['transition_duration'] == .3
    assert project['by'] == 'agent' and project['note'] == body['note']
    assert api.post(url + '/edit', json=body).status_code == 409
    current = api.get(url).json()
    invalid = {**body, 'revision': current['revision'], 'ops': [
        {'op': 'removeClip', 'index': 0}, {'op': 'trimClip', 'index': 0, 'source_out': .1, 'source_in': 2}]}
    assert api.post(url + '/edit', json=invalid).status_code == 422
    assert api.get(url).json() == current
    invalid['ops'] = [{'op': 'setTransition', 'index': 0, 'transition': 'teleport'}]
    assert api.post(url + '/edit', json=invalid).status_code == 422
    assert api.get(url).json() == current
