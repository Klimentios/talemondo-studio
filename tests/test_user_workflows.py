"""«Свои воркфлоу» (local_studio/user_workflows.py): a ComfyUI API graph as a form, a REST run and a queue receipt."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

GRAPH = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 5, "steps": 30, "cfg": 7.0, "model": ["4", 0],
                                                "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["10", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["4", 1]}, "_meta": {"title": "Промпт"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["4", 1]}},
    "8": {"class_type": "VAEEncode", "inputs": {"pixels": ["11", 0], "vae": ["4", 2]}},
    "10": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "11": {"class_type": "LoadImage", "inputs": {"image": "example.png"}, "_meta": {"title": "Фото героя"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "ComfyUI"}},
}


def test_detect_params_shows_media_prompts_seed_and_hides_numbers():
    from local_studio.user_workflows import detect_params, model_files
    params = {p["key"]: p for p in detect_params(GRAPH)}
    assert params["11.image"]["kind"] == "image" and params["11.image"]["exposed"]
    assert params["6.text"]["label"] == "Промпт" and params["6.text"]["exposed"]
    assert params["7.text"]["label"].endswith("негатив")
    assert params["3.seed"]["kind"] == "seed" and params["3.seed"]["exposed"]
    assert params["3.steps"]["kind"] == "int" and not params["3.steps"]["exposed"]
    assert params["3.cfg"]["kind"] == "float"
    assert "3.model" not in params  # links are wiring, not settings
    assert model_files(GRAPH) == ["base.safetensors"]


def test_editor_format_is_refused_with_the_way_out():
    from local_studio.user_workflows import check_api_graph
    with pytest.raises(ValueError, match="Export \\(API\\)"):
        check_api_graph({"nodes": [], "links": []})
    with pytest.raises(ValueError, match="отсутствующий узел"):
        check_api_graph({"1": {"class_type": "X", "inputs": {"a": ["2", 0]}}})


def test_dispatcher_runs_waiting_workflow_and_finishes_active_one_first():
    from local_studio.dispatcher import next_dispatch
    base = dict(h3_active=None, h3_pending=None, upscale=None, audio=None, paused=False)
    waiting = {"state": "queued_local", "created_at": "2026-11-07 00:00:00"}
    assert next_dispatch(**base, workflow=waiting) == "workflow"
    assert next_dispatch(**{**base, "paused": True}, workflow=waiting) is None
    assert next_dispatch(**{**base, "h3_active": {"state": "running"}}, workflow={**waiting, "state": "running"}) == "workflow"


def test_import_run_upload_queue_and_download(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from local_studio.store import StudioStore
    from local_studio import user_workflows as module

    store = StudioStore(tmp_path)
    picture = store.assets_dir / "ab" / "hash"
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(b"\x89PNG fake")
    digest = hashlib.sha256(picture.read_bytes()).hexdigest()
    asset = store.add_asset(kind="image", role="", original_name="hero.png", mime="image/png",
                            size=picture.stat().st_size, sha256=digest, path=picture)
    library = module.WorkflowLibrary(tmp_path)
    queue = module.WorkflowQueue(store, "http://comfy", library)
    ctx = SimpleNamespace(comfy_url="http://comfy", workflow_library=library, workflow_queue=queue)
    sent, uploads = [], []

    def comfy(request):
        path = request.url.path
        if path == "/object_info":
            return httpx.Response(200, json={})
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/upload/image":
            uploads.append(request.content)
            return httpx.Response(200, json={"name": f"studio_{digest[:32]}.png", "subfolder": "", "type": "input"})
        if path == "/prompt":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"prompt_id": f"remote-{len(sent)}"})
        if path.startswith("/history/"):
            return httpx.Response(200, json={path.rsplit("/", 1)[1]: {
                "status": {"completed": True, "status_str": "success"},
                "outputs": {"9": {"images": [{"filename": "out_00001_.png", "subfolder": "studio_workflows", "type": "output"}]}}}})
        if path == "/view":
            return httpx.Response(200, content=b"result-bytes")
        return httpx.Response(404)

    original = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(comfy), **kw))
    monkeypatch.setattr(module, "validate", lambda graph, info: [])
    app = FastAPI()
    app.include_router(module.make_router(ctx))

    async def scenario():
        async with original(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            created = (await client.post("/api/workflows", json={"name": "Фото → картинка", "graph": GRAPH,
                                                                   "params": [{"key": "3.steps", "exposed": True}]})).json()
            wid = created["id"]
            assert {item["key"] for item in created["form"]} >= {"11.image", "6.text", "3.seed", "3.steps"}
            refused = await client.post(f"/api/workflows/{wid}/run", json={"values": {"3.cfg": 3}})
            assert refused.status_code == 422 and "не открыты" in refused.text
            wrong = await client.post(f"/api/workflows/{wid}/run", json={"values": {"11.image": "missing"}})
            assert wrong.status_code == 422
            run = await client.post(f"/api/workflows/{wid}/run", json={"values": {
                "11.image": asset["asset_id"], "6.text": "a red fox", "3.seed": 100}, "count": 2})
            assert run.status_code == 202, run.text
            jobs = run.json()["jobs"]
            assert [job["values"]["3.seed"] for job in jobs] == [100, 101]
            for _ in range(4):
                await queue.run_once()
            first = (await client.get(f"/api/workflow-jobs/{jobs[0]['job_id']}")).json()
            assert first["state"] == "completed", first
            graph = sent[0]["prompt"]
            assert graph["11"]["inputs"]["image"] == f"studio_{digest[:32]}.png"
            assert graph["6"]["inputs"]["text"] == "a red fox" and graph["3"]["inputs"]["seed"] == 100
            assert graph["9"]["inputs"]["filename_prefix"].startswith("studio_workflows/")
            assert sent[0]["extra_data"] == {"studio_workflow_job": jobs[0]["job_id"]}
            got = await client.get(first["outputs"][0]["url"])
            assert got.status_code == 200 and got.content == b"result-bytes"
            stale = await client.put(f"/api/workflows/{wid}", json={"expected_revision": 9, "name": "x"})
            assert stale.status_code == 409

    asyncio.run(scenario())
    assert uploads and len(sent) == 2


def test_graph_replace_keeps_form_choices_and_comfy_round_trip(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from local_studio.store import StudioStore
    from local_studio import user_workflows as module

    library = module.WorkflowLibrary(tmp_path)
    queue = module.WorkflowQueue(StudioStore(tmp_path), "http://comfy", library)
    ctx = SimpleNamespace(comfy_url="http://comfy", workflow_library=library, workflow_queue=queue)
    posted = []

    def comfy(request):
        if request.url.path == "/object_info":
            return httpx.Response(200, json={})
        if request.method == "POST" and request.url.path == "/h3/studio/workflows":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "11111111-2222-4333-8444-555555555555"})
        if request.url.path == "/h3/studio/workflows/11111111-2222-4333-8444-555555555555":
            edited = json.loads(json.dumps(GRAPH))
            edited["3"]["inputs"]["steps"] = 12
            return httpx.Response(200, json={"name": "x", "prompt": edited, "studio_id": posted[0]["studio_id"]})
        if request.url.path == "/h3/studio/workflows":
            return httpx.Response(200, json={"items": [{"id": "a", "origin": "studio"}, {"id": "b", "origin": None}]})
        return httpx.Response(404)

    original = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(comfy), **kw))
    monkeypatch.setattr(module, "validate", lambda graph, info: [])
    app = FastAPI()
    app.include_router(module.make_router(ctx))

    async def scenario():
        async with original(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            created = (await client.post("/api/workflows", json={"name": "W", "graph": GRAPH, "params": [
                {"key": "3.steps", "exposed": True, "label": "Шаги"}, {"key": "7.text", "exposed": False}]})).json()
            wid = created["id"]
            graph = json.loads(json.dumps(GRAPH))
            graph["6"]["inputs"]["text"] = "a dog"
            del graph["7"]["inputs"]["text"]  # negative prompt became a link-free node without text
            stale = await client.put(f"/api/workflows/{wid}/graph", json={"expected_revision": 5, "graph": graph})
            assert stale.status_code == 409
            saved = (await client.put(f"/api/workflows/{wid}/graph", json={"expected_revision": 1, "graph": graph})).json()
            assert saved["revision"] == 2
            form = {item["key"]: item for item in saved["form"]}
            assert form["3.steps"]["label"] == "Шаги" and form["6.text"]["default"] == "a dog"
            assert "7.text" not in form and "7.text" not in saved["hidden_params"]
            opened = (await client.post(f"/api/workflows/{wid}/open-in-comfy")).json()
            assert opened["url"].endswith("?h3_workflow=11111111-2222-4333-8444-555555555555")
            assert posted[0]["studio_id"] == wid and posted[0]["prompt"]["6"]["inputs"]["text"] == "a dog"
            assert posted[0]["origin"] == "studio"
            inbox = (await client.get("/api/workflows/comfy-inbox")).json()
            assert [item["id"] for item in inbox["items"]] == ["b"]  # the copy opened from Studio is not an edit
            updated = (await client.post("/api/workflows/comfy-inbox/11111111-2222-4333-8444-555555555555/import",
                                         json={"target": wid})).json()
            assert updated["id"] == wid and updated["revision"] == 3 and updated["source"] == "comfyui"
            assert len((await client.get("/api/workflows")).json()["workflows"]) == 1

    asyncio.run(scenario())


# --- structural edits, versions and forks (app/graph_ops.py) ----------------------------------------------------

INFO = {
    "KSampler": {"input": {"required": {"seed": ["INT", {"min": 0, "max": 2**53}], "steps": ["INT", {"default": 20, "min": 1, "max": 200}],
                                        "cfg": ["FLOAT", {"default": 7.0}], "model": ["MODEL"], "positive": ["CONDITIONING"],
                                        "negative": ["CONDITIONING"], "latent_image": ["LATENT"],
                                        "sampler_name": [["euler", "res_multistep"]]}}, "output": ["LATENT"]},
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["base.safetensors", "base_turbo.safetensors"]]}},
                               "output": ["MODEL", "CLIP", "VAE"]},
    "CLIPTextEncode": {"input": {"required": {"text": ["STRING", {}], "clip": ["CLIP"]}}, "output": ["CONDITIONING"]},
    "VAEEncode": {"input": {"required": {"pixels": ["IMAGE"], "vae": ["VAE"]}}, "output": ["LATENT"]},
    "EmptyLatentImage": {"input": {"required": {"width": ["INT", {"min": 16}], "height": ["INT", {"min": 16}],
                                                "batch_size": ["INT", {"min": 1}]}}, "output": ["LATENT"]},
    "LoadImage": {"input": {"required": {"image": [["example.png"]]}}, "output": ["IMAGE", "MASK"]},
    "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING", {}]}}, "output": []},
    "LoraLoaderModelOnly": {"input": {"required": {"model": ["MODEL"], "lora_name": [["style.safetensors", "fast_lightning_4steps.safetensors"]],
                                                   "strength_model": ["FLOAT", {"default": 1.0, "min": -100, "max": 100}]}},
                            "output": ["MODEL"]},
}
FULL = {**GRAPH, "3": {**GRAPH["3"], "inputs": {**GRAPH["3"]["inputs"], "sampler_name": "euler"}}}


def _studio(tmp_path, monkeypatch, jobs=None, comfy_graphs=None):
    from fastapi import FastAPI
    from local_studio.comfy_schema import ObjectInfoCache
    from local_studio.job_graphs import JobGraphs
    from local_studio.store import StudioStore
    from local_studio import user_workflows as module

    library = module.WorkflowLibrary(tmp_path)
    queue = module.WorkflowQueue(StudioStore(tmp_path), "http://comfy", library)
    graphs = JobGraphs(tmp_path, "http://comfy")
    ctx = SimpleNamespace(comfy_url="http://comfy", workflow_library=library, workflow_queue=queue,
                          comfy_schema=ObjectInfoCache("http://comfy"), job_graphs=graphs,
                          store=SimpleNamespace(get_job=(jobs or {}).get))

    def comfy(request):
        path = request.url.path
        if path == "/object_info":
            return httpx.Response(200, json=INFO)
        if path.startswith("/h3/studio/graph/") and comfy_graphs is not None and path.rsplit("/", 1)[1] in comfy_graphs:
            return httpx.Response(200, json={"prompt": comfy_graphs[path.rsplit("/", 1)[1]]})
        return httpx.Response(404)

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(comfy), **kw))
    app = FastAPI()
    app.include_router(module.make_router(ctx))
    return app, original


def test_edit_ops_save_a_new_revision_and_keep_history(tmp_path, monkeypatch):
    app, original = _studio(tmp_path, monkeypatch)

    async def scenario():
        async with original(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            wid = (await client.post("/api/workflows", json={"name": "wf", "graph": FULL})).json()["id"]
            ops = [{"op": "add_lora", "lora_name": "style.safetensors", "strength_model": 0.7, "ref": "lora"},
                   {"op": "set", "node": "3", "input": "sampler_name", "value": "res_multistep"},
                   {"op": "set", "node": "3", "input": "steps", "value": 40}]
            dry = (await client.post(f"/api/workflows/{wid}/edit", json={"expected_revision": 1, "ops": ops, "dry_run": True})).json()
            assert dry["saved"] is False and dry["new_problems"] == []
            assert dry["graph"]["3"]["inputs"]["model"] == [dry["refs"]["lora"], 0]
            assert (await client.get(f"/api/workflows/{wid}")).json()["revision"] == 1
            saved = await client.post(f"/api/workflows/{wid}/edit", json={"expected_revision": 1, "ops": ops, "note": "LoRA стиля"})
            assert saved.status_code == 200, saved.text
            assert saved.json()["workflow"]["revision"] == 2 and saved.json()["workflow"]["note"] == "LoRA стиля"
            stale = await client.post(f"/api/workflows/{wid}/edit", json={"expected_revision": 1, "ops": ops})
            assert stale.status_code == 409
            fast = (await client.post(f"/api/workflows/{wid}/edit", json={"expected_revision": 2, "dry_run": True, "ops": [
                {"op": "add_lora", "lora_name": "fast_lightning_4steps.safetensors"}]})).json()
            assert fast["new_problems"] == [] and "быстрые" in fast["warnings"][0]  # allowed, only noted
            for bad, words in (([{"op": "set", "node": "3", "input": "sampler_name", "value": "dpm_fake"}], "не из списка"),
                               ([{"op": "remove_node", "node": "4"}], "bypass"),
                               ([{"op": "add_node", "class_type": "NoSuchNode"}], "нет узла")):
                got = await client.post(f"/api/workflows/{wid}/edit", json={"expected_revision": 2, "ops": bad})
                assert got.status_code == 422 and words in got.text, (bad, got.text)
            versions = (await client.get(f"/api/workflows/{wid}/versions")).json()["versions"]
            assert [(v["revision"], v["current"]) for v in versions] == [(2, True), (1, False)]
            old = (await client.get(f"/api/workflows/{wid}/versions/1")).json()
            assert old["graph"] == FULL and old["changes_to_current"]["added"][0]["class_type"] == "LoraLoaderModelOnly"
            back = (await client.post(f"/api/workflows/{wid}/restore", json={"expected_revision": 2, "revision": 1})).json()
            assert back["revision"] == 3 and back["note"] == "возврат к ревизии 1"
            assert (await client.get(f"/api/workflows/{wid}?graph=true")).json()["graph"] == FULL
            assert len((await client.get(f"/api/workflows/{wid}/versions")).json()["versions"]) == 3

    asyncio.run(scenario())


def test_job_graph_forks_into_a_workflow_and_survives_the_server(tmp_path, monkeypatch):
    remote = "11111111-2222-3333-4444-555555555555"
    comfy_graphs = {remote: FULL}
    app, original = _studio(tmp_path, monkeypatch, jobs={"job-1": {"job_id": "job-1", "remote_job_id": remote}},
                            comfy_graphs=comfy_graphs)

    async def scenario():
        async with original(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            forked = await client.post("/api/workflows/from-job/job-1", json={"name": "Проба LoRA"})
            assert forked.status_code == 201, forked.text
            assert forked.json()["origin"]["graph_source"] == "comfyui" and forked.json()["source"] == "job"
            comfy_graphs.clear()  # the rented server is gone
            again = await client.post("/api/workflows/from-job/job-1")
            assert again.status_code == 201 and again.json()["origin"]["graph_source"] == "archive"
            missing = await client.post("/api/workflows/from-job/nope")
            assert missing.status_code == 404

    asyncio.run(scenario())
