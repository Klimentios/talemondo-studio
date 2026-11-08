from local_studio.recipes import accepted_recipes
import json
import hashlib
from types import SimpleNamespace
from local_studio import recipes


def _recipe(request: dict, **extra) -> dict:
    return {"id": "my-swap-v1", "job_id": "j1", "status": "approved_single_performer", "request": request,
            "request_sha256": hashlib.sha256((json.dumps(request, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest(),
            "output_sha256": hashlib.sha256(b"accepted").hexdigest(), **extra}


def test_fresh_studio_has_no_accepted_recipes(tmp_path, monkeypatch):
    monkeypatch.setattr(recipes, "RECIPE_DIR", tmp_path / "recipes")  # the folder does not exist yet
    assert accepted_recipes() == []


def test_catalog_contains_only_approved_replayable_recipes(tmp_path, monkeypatch):
    (tmp_path / "ok.json").write_text(json.dumps(_recipe({"prompt": "HERO swap"})))
    (tmp_path / "draft.json").write_text(json.dumps(_recipe({"prompt": "draft"}, id="draft", status="rejected_motion")))
    monkeypatch.setattr(recipes, "RECIPE_DIR", tmp_path)
    items = accepted_recipes()
    assert [item["id"] for item in items] == ["my-swap-v1"]
    assert all(item["job_id"] and item["request_sha256"] and item["output_sha256"] for item in items)


def test_changed_request_is_excluded_from_list_and_detail(tmp_path, monkeypatch):
    original = _recipe({"prompt": "HERO swap"})
    original["request"]["prompt"] += "changed"
    (tmp_path / "changed.json").write_text(json.dumps(original))
    monkeypatch.setattr(recipes, "RECIPE_DIR", tmp_path)
    assert recipes.accepted_recipes() == []
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(recipes.make_router())
    assert TestClient(app).get('/api/recipes/accepted/' + original['id']).status_code == 404


def test_verify_reads_actual_files_instead_of_asset_metadata(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    image = tmp_path / 'image.png'
    image.write_bytes(b'image')
    output = tmp_path / 'result.mp4'
    output.write_bytes(b'accepted')
    request = {'prompt': 'original'}
    item = {'id': 'r1', 'job_id': 'j1', 'status': 'approved_single_performer', 'request': request,
            'request_sha256': hashlib.sha256((json.dumps(request, ensure_ascii=False, indent=2)+'\n').encode()).hexdigest(),
            'output_sha256': hashlib.sha256(b'accepted').hexdigest(),
            'assets': [{'asset_id': 'a1', 'sha256': hashlib.sha256(b'image').hexdigest()}]}
    (tmp_path / 'recipe.json').write_text(json.dumps(item))
    monkeypatch.setattr(recipes, 'RECIPE_DIR', tmp_path)
    store = SimpleNamespace(get_asset=lambda _: {'path': str(image)},
                            get_job=lambda _: {'output_path': str(output)})
    app = FastAPI()
    app.include_router(recipes.make_router(store))
    client = TestClient(app)
    assert client.get('/api/recipes/accepted/r1/verify').json()['assets_available']
    image.write_bytes(b'changed')
    output.write_bytes(b'changed')
    result = client.get('/api/recipes/accepted/r1/verify').json()
    assert not result['assets_available'] and not result['output_matches']
